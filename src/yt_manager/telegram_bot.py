import asyncio
import html
import io
import logging
from pathlib import Path
import re
import uuid
from typing import Any, Optional
from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import (
    Application,
    CallbackQueryHandler,
    CommandHandler,
    ContextTypes,
    MessageHandler,
    filters,
)

from yt_manager.config import Settings, get_settings
from yt_manager.db import Database
from yt_manager.extractor import extract_from_url
from yt_manager.metube import MeTubeClient
from yt_manager.scanner import get_scan_stats, sync_disks_to_db_and_archive

logger = logging.getLogger("yt_manager.telegram")

URL_REGEX = re.compile(r"https?://[^\s<>\"'`]+")

# In-memory action cache for callback queries (due to Telegram 64-byte callback_data limit)
ACTION_CACHE: dict[str, list[str]] = {}


def extract_urls_from_text(text: str) -> list[str]:
    """Find and return unique URLs preserving order."""
    matches = URL_REGEX.findall(text)
    return list(dict.fromkeys(matches))


async def send_chunked_reply(
    message: Any,
    lines: list[str],
    parse_mode: str = "HTML",
    max_chars: int = 3800,
    reply_markup: Any = None,
) -> None:
    """
    Send messages in line-based chunks to ensure messages never exceed
    Telegram's 4,096 character limit, while avoiding splitting mid-HTML tags.
    Attaches reply_markup to the final chunk if provided.
    """
    chunk: list[str] = []
    chunk_len = 0
    chunks: list[list[str]] = []

    for line in lines:
        line_len = len(line)
        if chunk and (chunk_len + line_len + 1 > max_chars):
            chunks.append(chunk)
            chunk = [line]
            chunk_len = line_len
        else:
            chunk.append(line)
            chunk_len += line_len + 1

    if chunk:
        chunks.append(chunk)

    for i, c in enumerate(chunks):
        markup = reply_markup if (i == len(chunks) - 1) else None
        await message.reply_text("\n".join(c), parse_mode=parse_mode, reply_markup=markup)


def analyze_urls(
    urls: list[str],
    db: Database,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], list[str]]:
    """
    Analyze given URLs against the database.
    Returns: (found_items, missing_items, invalid_urls)
    """
    found = []
    missing = []
    invalid = []

    for url in urls:
        parsed = extract_from_url(url)
        if not parsed:
            invalid.append(url)
            continue

        extractor, video_id = parsed
        file_path = db.find_by_id(extractor, video_id)
        if not file_path:
            fb = db.find_by_video_id_only(video_id)
            if fb:
                extractor, file_path = fb

        if file_path:
            found.append({
                "url": url,
                "extractor": extractor,
                "video_id": video_id,
                "file_path": file_path,
            })
        else:
            missing.append({
                "url": url,
                "extractor": extractor,
                "video_id": video_id,
            })

    return found, missing, invalid


def is_user_allowed(user_id: int, settings: Settings) -> bool:
    allowed = settings.parsed_telegram_allowed_users
    if not allowed:
        return True
    return user_id in allowed


class TelegramBotService:
    def __init__(self, settings: Settings):
        self.settings = settings
        self.app: Optional[Application] = None
        self.is_running = False

    def _get_db(self) -> Database:
        return Database(self.settings.db_path)

    def _get_metube(self) -> MeTubeClient:
        return MeTubeClient(self.settings.metube_url)

    async def _check_auth(self, update: Update) -> bool:
        user = update.effective_user
        if not user:
            return False
        if not is_user_allowed(user.id, self.settings):
            if update.message:
                await update.message.reply_text(
                    f"Access denied.\n(Your Telegram ID: <code>{user.id}</code>)",
                    parse_mode="HTML",
                )
            elif update.callback_query:
                await update.callback_query.answer(
                    f"Access denied. (ID: {user.id})",
                    show_alert=True,
                )
            return False
        return True

    async def handle_start(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        if not await self._check_auth(update):
            return
        user_id = update.effective_user.id if update.effective_user else 0
        text = (
            f"Hello! Welcome to <b>yt-manager bot</b>.\n\n"
            f"<b>How to use</b>:\n"
            f"• Send one or more links from YouTube, Twitter/X, Instagram, or TikTok.\n"
            f"• You can also send a <code>.txt</code> file containing links.\n"
            f"• The bot checks if videos are already saved, and lets you download missing ones to MeTube with one click.\n\n"
            f"<b>Commands</b>:\n"
            f"• <code>/scan</code> - Scan NAS disk & sync archive immediately\n"
            f"• <code>/status</code> - Check server status & media count\n"
            f"• <code>/history</code> - View recent request history\n\n"
            f"Your Telegram ID: <code>{user_id}</code>"
        )
        await update.message.reply_text(text, parse_mode="HTML")

    async def handle_status(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        if not await self._check_auth(update):
            return
        db = self._get_db()
        stats = get_scan_stats()
        text = (
            f"<b>yt-manager System Status</b>\n\n"
            f"• Total Saved Media: <b>{db.count():,}</b>\n"
            f"• Last Scanned At: <code>{html.escape(stats.last_scanned_at or 'Never')}</code>\n"
            f"• Scan Duration: <code>{stats.duration_seconds}s</code>\n"
            f"• Media Directory: <code>{html.escape(str(self.settings.media_dir))}</code>\n"
            f"• MeTube URL: <code>{html.escape(self.settings.metube_url)}</code>"
        )
        await update.message.reply_text(text, parse_mode="HTML")

    async def handle_scan(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        if not await self._check_auth(update):
            return
        status_msg = await update.message.reply_text("Scanning NAS disk...")
        db = self._get_db()

        loop = asyncio.get_running_loop()
        stats = await loop.run_in_executor(
            None,
            lambda: sync_disks_to_db_and_archive(
                directories=[self.settings.downloads_dir, self.settings.media_dir],
                archive_file_path=self.settings.archive_file_path,
                db=db,
                exclude_dirs=self.settings.parsed_exclude_dirs,
                exclude_patterns=self.settings.parsed_exclude_patterns,
            ),
        )

        change_status = "Changes Synced" if stats.has_changes else "No Changes"
        diff_text = f" (+{stats.added_files}, -{stats.deleted_files})" if stats.has_changes else ""
        await status_msg.edit_text(
            f"<b>Scan Complete!</b> ({change_status})\n\n"
            f"• Total Files: <b>{stats.total_files:,}</b>{diff_text}\n"
            f"• Duration: <b>{stats.duration_seconds}s</b>",
            parse_mode="HTML",
        )

    async def handle_history(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        if not await self._check_auth(update):
            return
        db = self._get_db()
        limit = 10
        if context.args and context.args[0].isdigit():
            limit = min(50, max(1, int(context.args[0])))

        records = db.get_history(limit=limit)
        if not records:
            await update.message.reply_text("No request history found.")
            return

        lines = [f"<b>Recent Request History ({len(records)})</b>:\n"]
        status_icons = {
            "EXISTS": "✅",
            "QUEUED": "🚀",
            "MISSING": "⚠️",
            "FAILED": "❌",
            "INVALID": "⛔",
        }

        for r in records:
            icon = status_icons.get(r["status"], "•")
            time_part = r["created_at"].split("T")[-1][:5] if "T" in r["created_at"] else ""
            date_part = r["created_at"].split("T")[0] if "T" in r["created_at"] else ""
            ext = f"[{r['extractor'].upper()}]" if r["extractor"] else ""
            vid = f"<code>{r['video_id']}</code>" if r["video_id"] else ""
            source_tag = f"({r['source']})"

            detail_info = ""
            if r["status"] == "FAILED" and r["detail"]:
                safe_detail = html.escape(r["detail"][:50] + "..." if len(r["detail"]) > 50 else r["detail"])
                detail_info = f"\n  └ <i>{safe_detail}</i>"
            elif r["status"] == "EXISTS" and r["detail"]:
                folder_name = Path(r["detail"]).parent.name
                detail_info = f"\n  └ <code>{html.escape(folder_name)}</code>"

            lines.append(f"{icon} <b>{r['status']}</b> {source_tag} {ext} {vid} <code>{date_part} {time_part}</code>{detail_info}")

        await send_chunked_reply(update.message, lines, parse_mode="HTML")

    async def handle_text_message(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        if not await self._check_auth(update):
            return
        text = update.message.text or ""
        urls = extract_urls_from_text(text)
        if not urls:
            return

        await self._process_and_respond(update, urls)

    async def handle_document_message(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        if not await self._check_auth(update):
            return
        doc = update.message.document
        if not doc or not doc.file_name or not doc.file_name.lower().endswith(".txt"):
            return

        status_msg = await update.message.reply_text("Extracting links from text file...")
        try:
            tg_file = await doc.get_file()
            byte_array = await tg_file.download_as_bytearray()
            content = byte_array.decode("utf-8", errors="ignore")
            urls = extract_urls_from_text(content)

            if not urls:
                await status_msg.edit_text("No valid links found in the file.")
                return

            await status_msg.delete()
            await self._process_and_respond(update, urls)
        except Exception as e:
            logger.error(f"Error reading document: {e}", exc_info=True)
            await status_msg.edit_text(f"Error processing file: {e}")

    async def _process_and_respond(self, update: Update, urls: list[str]):
        db = self._get_db()
        found, missing, invalid = analyze_urls(urls, db)

        # Record all checked URLs in request history
        for item in found:
            db.record_request(
                source="telegram",
                url=item["url"],
                extractor=item["extractor"],
                video_id=item["video_id"],
                status="EXISTS",
                detail=item["file_path"],
            )
        for item in missing:
            db.record_request(
                source="telegram",
                url=item["url"],
                extractor=item["extractor"],
                video_id=item["video_id"],
                status="MISSING",
            )
        for u in invalid:
            db.record_request(
                source="telegram",
                url=u,
                status="INVALID",
            )

        # Case 1: Single URL
        if len(urls) == 1:
            if found:
                item = found[0]
                folder_path = str(Path(item["file_path"]).parent)
                text = (
                    f"<b>Video already saved!</b>\n\n"
                    f"• Platform: <b>{html.escape(item['extractor'].upper())}</b>\n"
                    f"• ID: <code>{html.escape(item['video_id'])}</code>\n"
                    f"• Location: <code>{html.escape(folder_path)}</code>"
                )
                await update.message.reply_text(text, parse_mode="HTML")
            elif missing:
                item = missing[0]
                action_id = str(uuid.uuid4())[:8]
                ACTION_CACHE[action_id] = [item["url"]]

                text = (
                    f"<b>Video not saved.</b>\n\n"
                    f"• Platform: <b>{html.escape(item['extractor'].upper())}</b>\n"
                    f"• ID: <code>{html.escape(item['video_id'])}</code>\n\n"
                    f"Download now via MeTube?"
                )
                keyboard = [
                    [
                        InlineKeyboardButton("Download", callback_data=f"dl_single:{action_id}"),
                    ]
                ]
                await update.message.reply_text(
                    text,
                    reply_markup=InlineKeyboardMarkup(keyboard),
                    parse_mode="HTML",
                )
            else:
                await update.message.reply_text("Could not extract video ID from the provided URL.")
            return

        # Case 2: Bulk URLs (2 or more)
        report_lines = [f"<b>Checked {len(urls)} link(s)</b>\n"]

        if found:
            report_lines.append(f"<b>Already Saved ({len(found)}):</b>")
            for item in found:
                folder_path = str(Path(item["file_path"]).parent)
                report_lines.append(
                    f"• [{html.escape(item['extractor'].upper())}] <code>{html.escape(item['video_id'])}</code>\n"
                    f"  └ <code>{html.escape(folder_path)}</code>"
                )
            report_lines.append("")

        if missing:
            report_lines.append(f"<b>Missing ({len(missing)}):</b>")
            for item in missing:
                report_lines.append(
                    f"• [{html.escape(item['extractor'].upper())}] <code>{html.escape(item['video_id'])}</code>"
                )
            report_lines.append("")

        if invalid:
            report_lines.append(f"<b>Unrecognized ({len(invalid)}):</b>")
            for u in invalid[:3]:
                safe_u = html.escape(u[:40] + "..." if len(u) > 40 else u)
                report_lines.append(f"• <code>{safe_u}</code>")
            if len(invalid) > 3:
                report_lines.append(f"• ...and {len(invalid)-3} more")
            report_lines.append("")

        if not missing:
            report_lines.append("<b>All verified videos are already saved!</b>")
            await send_chunked_reply(update.message, report_lines, parse_mode="HTML")
            return

        # Missing videos exist -> Provide bulk download button
        action_id = str(uuid.uuid4())[:8]
        missing_urls = [m["url"] for m in missing]
        ACTION_CACHE[action_id] = missing_urls

        keyboard = [
            [
                InlineKeyboardButton(
                    f"Download ({len(missing)})",
                    callback_data=f"dl_bulk:{action_id}",
                )
            ],
        ]

        await send_chunked_reply(
            update.message,
            report_lines,
            reply_markup=InlineKeyboardMarkup(keyboard),
            parse_mode="HTML",
        )

    async def handle_callback_query(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        if not await self._check_auth(update):
            return
        query = update.callback_query
        if not query:
            return
        await query.answer()

        data = query.data or ""
        parts = data.split(":", 1)
        if len(parts) != 2:
            return

        action, action_id = parts[0], parts[1]
        urls = ACTION_CACHE.pop(action_id, [])

        if action == "cancel":
            await query.edit_message_reply_markup(reply_markup=None)
            return

        if not urls:
            await query.edit_message_reply_markup(reply_markup=None)
            await query.message.reply_text("Request expired. Please resend the link(s).")
            return

        metube = self._get_metube()
        db = self._get_db()

        if action == "dl_single":
            url = urls[0]
            parsed = extract_from_url(url)
            extractor, video_id = parsed if parsed else (None, None)
            await query.edit_message_reply_markup(reply_markup=None)
            res = await metube.add_download(url)
            if res.get("success"):
                db.record_request(
                    source="telegram",
                    url=url,
                    extractor=extractor,
                    video_id=video_id,
                    status="QUEUED",
                )
                await query.message.reply_text("<b>Download request sent to MeTube!</b>", parse_mode="HTML")
            else:
                err_str = str(res.get("error", "Unknown error"))
                db.record_request(
                    source="telegram",
                    url=url,
                    extractor=extractor,
                    video_id=video_id,
                    status="FAILED",
                    detail=err_str,
                )
                await query.message.reply_text(f"Download request failed: {html.escape(err_str)}")

        elif action == "dl_bulk":
            await query.edit_message_reply_markup(reply_markup=None)
            status_msg = await query.message.reply_text(f"Adding {len(urls)} video(s) to MeTube queue...")
            success_count, results = await metube.add_bulk_downloads(urls)

            for url, r in zip(urls, results):
                parsed = extract_from_url(url)
                extractor, video_id = parsed if parsed else (None, None)
                if r.get("success"):
                    db.record_request(
                        source="telegram",
                        url=url,
                        extractor=extractor,
                        video_id=video_id,
                        status="QUEUED",
                    )
                else:
                    db.record_request(
                        source="telegram",
                        url=url,
                        extractor=extractor,
                        video_id=video_id,
                        status="FAILED",
                        detail=str(r.get("error", "Unknown error")),
                    )

            if success_count == len(urls):
                await status_msg.edit_text(
                    f"<b>Success! All {success_count} video(s) queued in MeTube!</b>",
                    parse_mode="HTML",
                )
            else:
                fail_count = len(urls) - success_count
                await status_msg.edit_text(
                    f"<b>{success_count} succeeded, {fail_count} failed</b>\nPlease check MeTube status.",
                    parse_mode="HTML",
                )

    async def start(self):
        if not self.settings.telegram_bot_token:
            logger.info("TELEGRAM_BOT_TOKEN is not configured. Telegram bot service is skipped.")
            return

        logger.info("Initializing Telegram Bot Service...")
        self.app = Application.builder().token(self.settings.telegram_bot_token).build()

        self.app.add_handler(CommandHandler("start", self.handle_start))
        self.app.add_handler(CommandHandler("help", self.handle_start))
        self.app.add_handler(CommandHandler("status", self.handle_status))
        self.app.add_handler(CommandHandler("scan", self.handle_scan))
        self.app.add_handler(CommandHandler("history", self.handle_history))
        self.app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, self.handle_text_message))
        self.app.add_handler(MessageHandler(filters.Document.FileExtension("txt"), self.handle_document_message))
        self.app.add_handler(CallbackQueryHandler(self.handle_callback_query))

        await self.app.initialize()
        await self.app.start()
        await self.app.updater.start_polling()
        self.is_running = True
        logger.info("Telegram bot is now actively polling for messages!")

    async def stop(self):
        if self.app and self.is_running:
            logger.info("Stopping Telegram Bot Service...")
            try:
                if self.app.updater and self.app.updater.running:
                    await self.app.updater.stop()
                await self.app.stop()
                await self.app.shutdown()
            except Exception as e:
                logger.error(f"Error while stopping telegram bot: {e}")
            self.is_running = False
            logger.info("Telegram Bot Service stopped.")


# Singleton instance
bot_service: Optional[TelegramBotService] = None


def get_telegram_service() -> TelegramBotService:
    global bot_service
    if bot_service is None:
        bot_service = TelegramBotService(get_settings())
    return bot_service
