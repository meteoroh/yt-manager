import asyncio
import io
import logging
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
                    f"⛔️ Access denied.\n(Your Telegram ID: `{user.id}`)",
                    parse_mode="Markdown",
                )
            return False
        return True

    async def handle_start(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        if not await self._check_auth(update):
            return
        user_id = update.effective_user.id if update.effective_user else 0
        text = (
            f"👋 Hello! Welcome to **yt-manager bot**.\n\n"
            f"📌 **How to use**:\n"
            f"• Send one or more links from YouTube, Twitter/X, Instagram, or TikTok.\n"
            f"• You can also send a `.txt` file containing links.\n"
            f"• The bot checks if videos are already saved, and lets you download missing ones to MeTube with one click.\n\n"
            f"⚙️ **Commands**:\n"
            f"• `/scan` - Scan NAS disk & sync archive immediately\n"
            f"• `/status` - Check server status & media count\n\n"
            f"🆔 Your Telegram ID: `{user_id}`"
        )
        await update.message.reply_text(text, parse_mode="Markdown")

    async def handle_status(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        if not await self._check_auth(update):
            return
        db = self._get_db()
        stats = get_scan_stats()
        text = (
            f"📊 **yt-manager System Status**\n\n"
            f"• Total Saved Media: **{db.count():,}**\n"
            f"• Last Scanned At: `{stats.last_scanned_at or 'Never'}`\n"
            f"• Scan Duration: `{stats.duration_seconds}s`\n"
            f"• Media Directory: `{self.settings.media_dir}`\n"
            f"• MeTube URL: `{self.settings.metube_url}`"
        )
        await update.message.reply_text(text, parse_mode="Markdown")

    async def handle_scan(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        if not await self._check_auth(update):
            return
        status_msg = await update.message.reply_text("🔄 Scanning NAS disk...")
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

        await status_msg.edit_text(
            f"✅ **Scan Complete!**\n\n"
            f"• Total Files: **{stats.total_files:,}**\n"
            f"• Deleted/Pruned: **{stats.deleted_files:,}**\n"
            f"• Duration: **{stats.duration_seconds}s**",
            parse_mode="Markdown",
        )

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

        status_msg = await update.message.reply_text("📄 Extracting links from text file...")
        try:
            tg_file = await doc.get_file()
            byte_array = await tg_file.download_as_bytearray()
            content = byte_array.decode("utf-8", errors="ignore")
            urls = extract_urls_from_text(content)

            if not urls:
                await status_msg.edit_text("⚠️ No valid links found in the file.")
                return

            await status_msg.delete()
            await self._process_and_respond(update, urls)
        except Exception as e:
            logger.error(f"Error reading document: {e}", exc_info=True)
            await status_msg.edit_text(f"❌ Error processing file: {e}")

    async def _process_and_respond(self, update: Update, urls: list[str]):
        db = self._get_db()
        found, missing, invalid = analyze_urls(urls, db)

        # Case 1: Single URL
        if len(urls) == 1:
            if found:
                item = found[0]
                text = (
                    f"✅ **Video already saved!**\n\n"
                    f"• Platform: **{item['extractor'].upper()}**\n"
                    f"• ID: `{item['video_id']}`\n"
                    f"• Location:\n`{item['file_path']}`"
                )
                await update.message.reply_text(text, parse_mode="Markdown")
            elif missing:
                item = missing[0]
                action_id = str(uuid.uuid4())[:8]
                ACTION_CACHE[action_id] = [item["url"]]

                text = (
                    f"❌ **Video not saved.**\n\n"
                    f"• Platform: **{item['extractor'].upper()}**\n"
                    f"• ID: `{item['video_id']}`\n"
                    f"• URL: {item['url']}\n\n"
                    f"Download now via MeTube?"
                )
                keyboard = [
                    [
                        InlineKeyboardButton("⬇️ Download via MeTube", callback_data=f"dl_single:{action_id}"),
                        InlineKeyboardButton("❌ Cancel", callback_data=f"cancel:{action_id}"),
                    ]
                ]
                await update.message.reply_text(
                    text,
                    reply_markup=InlineKeyboardMarkup(keyboard),
                    parse_mode="Markdown",
                )
            else:
                await update.message.reply_text("⚠️ Could not extract video ID from the provided URL.")
            return

        # Case 2: Bulk URLs (2 or more)
        report_lines = [f"📋 **Checked {len(urls)} link(s)**\n"]

        if found:
            report_lines.append(f"✅ **Already Saved ({len(found)}):**")
            for item in found:
                report_lines.append(f"• [{item['extractor'].upper()}] `{item['video_id']}`\n  └ `{item['file_path']}`")
            report_lines.append("")

        if missing:
            report_lines.append(f"❌ **Missing ({len(missing)}):**")
            for item in missing:
                report_lines.append(f"• [{item['extractor'].upper()}] `{item['video_id']}`")
            report_lines.append("")

        if invalid:
            report_lines.append(f"⚠️ **Unrecognized ({len(invalid)}):**")
            for u in invalid[:3]:
                report_lines.append(f"• `{u[:40]}...`" if len(u) > 40 else f"• `{u}`")
            if len(invalid) > 3:
                report_lines.append(f"• ...and {len(invalid)-3} more")
            report_lines.append("")

        if not missing:
            report_lines.append("🎉 **All verified videos are already saved!**")
            await update.message.reply_text("\n".join(report_lines), parse_mode="Markdown")
            return

        # Missing videos exist -> Provide bulk download button
        action_id = str(uuid.uuid4())[:8]
        missing_urls = [m["url"] for m in missing]
        ACTION_CACHE[action_id] = missing_urls

        keyboard = [
            [
                InlineKeyboardButton(
                    f"⬇️ Download all {len(missing)} missing video(s)",
                    callback_data=f"dl_bulk:{action_id}",
                )
            ],
            [
                InlineKeyboardButton("❌ Cancel", callback_data=f"cancel:{action_id}")
            ],
        ]

        await update.message.reply_text(
            "\n".join(report_lines),
            reply_markup=InlineKeyboardMarkup(keyboard),
            parse_mode="Markdown",
        )

    async def handle_callback_query(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
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
            await query.message.reply_text("❌ Download cancelled.")
            return

        if not urls:
            await query.edit_message_reply_markup(reply_markup=None)
            await query.message.reply_text("⚠️ Request expired. Please resend the link(s).")
            return

        metube = self._get_metube()

        if action == "dl_single":
            url = urls[0]
            await query.edit_message_reply_markup(reply_markup=None)
            res = await metube.add_download(url)
            if res.get("success"):
                await query.message.reply_text("🚀 **Download request sent to MeTube!**", parse_mode="Markdown")
            else:
                await query.message.reply_text(f"❌ Download request failed: {res.get('error', 'Unknown error')}")

        elif action == "dl_bulk":
            await query.edit_message_reply_markup(reply_markup=None)
            status_msg = await query.message.reply_text(f"⏳ Adding {len(urls)} video(s) to MeTube queue...")
            success_count, results = await metube.add_bulk_downloads(urls)

            if success_count == len(urls):
                await status_msg.edit_text(
                    f"🚀 **Success! All {success_count} video(s) queued in MeTube!**",
                    parse_mode="Markdown",
                )
            else:
                fail_count = len(urls) - success_count
                await status_msg.edit_text(
                    f"⚠️ **{success_count} succeeded, {fail_count} failed**\nPlease check MeTube status.",
                    parse_mode="Markdown",
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
