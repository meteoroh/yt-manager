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
                    f"⛔️ 접근 권한이 없습니다.\n(내 텔레그램 ID: `{user.id}`)",
                    parse_mode="Markdown",
                )
            return False
        return True

    async def handle_start(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        if not await self._check_auth(update):
            return
        user_id = update.effective_user.id if update.effective_user else 0
        text = (
            f"👋 안녕하세요! **yt-manager 봇**입니다.\n\n"
            f"📌 **사용 방법**:\n"
            f"• 유튜브, 트위터, 인스타, 틱톡 링크를 하나 또는 여러 개 복사해서 보내주세요.\n"
            f"• 링크가 담긴 `.txt` 파일을 전송하셔도 됩니다.\n"
            f"• 소장 여부를 확인하고 미소장 영상은 원클릭으로 MeTube에 일괄 다운로드합니다.\n\n"
            f"⚙️ **명령어**:\n"
            f"• `/scan` - NAS 디스크 즉시 재스캔 & archive 동기화\n"
            f"• `/status` - 서버 상태 및 소장 파일 수 확인\n\n"
            f"🆔 내 텔레그램 ID: `{user_id}`"
        )
        await update.message.reply_text(text, parse_mode="Markdown")

    async def handle_status(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        if not await self._check_auth(update):
            return
        db = self._get_db()
        stats = get_scan_stats()
        text = (
            f"📊 **yt-manager 시스템 상태**\n\n"
            f"• 총 소장 미디어: **{db.count():,}개**\n"
            f"• 마지막 스캔 시각: `{stats.last_scanned_at or '없음'}`\n"
            f"• 스캔 소요 시간: `{stats.duration_seconds}s`\n"
            f"• 보관함 경로: `{self.settings.media_dir}`\n"
            f"• MeTube 주소: `{self.settings.metube_url}`"
        )
        await update.message.reply_text(text, parse_mode="Markdown")

    async def handle_scan(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        if not await self._check_auth(update):
            return
        status_msg = await update.message.reply_text("🔄 NAS 디스크 스캔을 시작합니다...")
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
            f"✅ **스캔 완료!**\n\n"
            f"• 총 파일: **{stats.total_files:,}개**\n"
            f"• 삭제 반영: **{stats.deleted_files:,}개**\n"
            f"• 소요 시간: **{stats.duration_seconds}초**",
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

        status_msg = await update.message.reply_text("📄 텍스트 파일에서 링크를 추출하는 중...")
        try:
            tg_file = await doc.get_file()
            byte_array = await tg_file.download_as_bytearray()
            content = byte_array.decode("utf-8", errors="ignore")
            urls = extract_urls_from_text(content)

            if not urls:
                await status_msg.edit_text("⚠️ 파일 내에서 유효한 링크를 찾지 못했습니다.")
                return

            await status_msg.delete()
            await self._process_and_respond(update, urls)
        except Exception as e:
            logger.error(f"Error reading document: {e}", exc_info=True)
            await status_msg.edit_text(f"❌ 파일 처리 중 오류가 발생했습니다: {e}")

    async def _process_and_respond(self, update: Update, urls: list[str]):
        db = self._get_db()
        found, missing, invalid = analyze_urls(urls, db)

        # Case 1: Single URL
        if len(urls) == 1:
            if found:
                item = found[0]
                text = (
                    f"✅ **이미 소장 중인 영상입니다!**\n\n"
                    f"• 플랫폼: **{item['extractor'].upper()}**\n"
                    f"• ID: `{item['video_id']}`\n"
                    f"• 저장 위치:\n`{item['file_path']}`"
                )
                await update.message.reply_text(text, parse_mode="Markdown")
            elif missing:
                item = missing[0]
                action_id = str(uuid.uuid4())[:8]
                ACTION_CACHE[action_id] = [item["url"]]

                text = (
                    f"❌ **저장되어 있지 않은 영상입니다.**\n\n"
                    f"• 플랫폼: **{item['extractor'].upper()}**\n"
                    f"• ID: `{item['video_id']}`\n"
                    f"• URL: {item['url']}\n\n"
                    f"지금 MeTube로 다운로드할까요?"
                )
                keyboard = [
                    [
                        InlineKeyboardButton("⬇️ MeTube로 다운로드", callback_data=f"dl_single:{action_id}"),
                        InlineKeyboardButton("❌ 취소", callback_data=f"cancel:{action_id}"),
                    ]
                ]
                await update.message.reply_text(
                    text,
                    reply_markup=InlineKeyboardMarkup(keyboard),
                    parse_mode="Markdown",
                )
            else:
                await update.message.reply_text("⚠️ 해당 URL에서 비디오 ID를 추출할 수 없습니다.")
            return

        # Case 2: Bulk URLs (2 or more)
        report_lines = [f"📋 **총 {len(urls)}개의 링크 검사 결과**\n"]

        if found:
            report_lines.append(f"✅ **이미 소장 중 ({len(found)}개):**")
            for item in found:
                report_lines.append(f"• [{item['extractor'].upper()}] `{item['video_id']}`\n  └ `{item['file_path']}`")
            report_lines.append("")

        if missing:
            report_lines.append(f"❌ **미소장 영상 ({len(missing)}개):**")
            for item in missing:
                report_lines.append(f"• [{item['extractor'].upper()}] `{item['video_id']}`")
            report_lines.append("")

        if invalid:
            report_lines.append(f"⚠️ **인식 불가 ({len(invalid)}개):**")
            for u in invalid[:3]:
                report_lines.append(f"• `{u[:40]}...`" if len(u) > 40 else f"• `{u}`")
            if len(invalid) > 3:
                report_lines.append(f"• ...외 {len(invalid)-3}개")
            report_lines.append("")

        if not missing:
            report_lines.append("🎉 **확인된 모든 영상이 이미 소장되어 있습니다!**")
            await update.message.reply_text("\n".join(report_lines), parse_mode="Markdown")
            return

        # Missing videos exist -> Provide bulk download button
        action_id = str(uuid.uuid4())[:8]
        missing_urls = [m["url"] for m in missing]
        ACTION_CACHE[action_id] = missing_urls

        keyboard = [
            [
                InlineKeyboardButton(
                    f"⬇️ 미소장 영상 {len(missing)}개 모두 다운로드",
                    callback_data=f"dl_bulk:{action_id}",
                )
            ],
            [
                InlineKeyboardButton("❌ 취소", callback_data=f"cancel:{action_id}")
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
            await query.message.reply_text("❌ 다운로드가 취소되었습니다.")
            return

        if not urls:
            await query.edit_message_reply_markup(reply_markup=None)
            await query.message.reply_text("⚠️ 만료된 요청입니다. 링크를 다시 보내주세요.")
            return

        metube = self._get_metube()

        if action == "dl_single":
            url = urls[0]
            await query.edit_message_reply_markup(reply_markup=None)
            res = await metube.add_download(url)
            if res.get("success"):
                await query.message.reply_text("🚀 **MeTube에 다운로드 요청을 전송했습니다!**", parse_mode="Markdown")
            else:
                await query.message.reply_text(f"❌ 다운로드 요청 실패: {res.get('error', '알 수 없는 오류')}")

        elif action == "dl_bulk":
            await query.edit_message_reply_markup(reply_markup=None)
            status_msg = await query.message.reply_text(f"⏳ {len(urls)}개 영상을 MeTube 큐에 등록하는 중...")
            success_count, results = await metube.add_bulk_downloads(urls)

            if success_count == len(urls):
                await status_msg.edit_text(
                    f"🚀 **성공! {success_count}개 영상 모두 MeTube 큐에 등록 완료되었습니다!**",
                    parse_mode="Markdown",
                )
            else:
                fail_count = len(urls) - success_count
                await status_msg.edit_text(
                    f"⚠️ **{success_count}개 성공, {fail_count}개 실패**\nMeTube 상태를 확인해 주세요.",
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
