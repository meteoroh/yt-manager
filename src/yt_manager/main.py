import logging
from contextlib import asynccontextmanager
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from fastapi import FastAPI
import uvicorn

from yt_manager.api import router
from yt_manager.config import get_settings
from yt_manager.db import Database
from yt_manager.scanner import sync_disks_to_db_and_archive

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger("yt_manager")

scheduler = AsyncIOScheduler()


def run_scheduled_scan():
    settings = get_settings()
    logger.info("Starting scheduled disk scan...")
    try:
        db = Database(settings.db_path)
        stats = sync_disks_to_db_and_archive(
            directories=[settings.downloads_dir, settings.media_dir],
            archive_file_path=settings.archive_file_path,
            db=db,
            exclude_dirs=settings.parsed_exclude_dirs,
            exclude_patterns=settings.parsed_exclude_patterns,
        )
        if stats.has_changes:
            logger.info(
                f"Scheduled scan completed in {stats.duration_seconds}s. Total files: {stats.total_files} (+{stats.added_files}, -{stats.deleted_files})"
            )
        else:
            logger.info(
                f"Scheduled scan completed in {stats.duration_seconds}s. Total files: {stats.total_files} (no changes)"
            )
    except Exception as e:
        logger.error(f"Error during scheduled disk scan: {e}", exc_info=True)


@asynccontextmanager
async def lifespan(app: FastAPI):
    settings = get_settings()
    logger.info("yt-manager service starting...")

    # Startup scan if enabled
    if settings.auto_scan_on_startup:
        run_scheduled_scan()

    # Configure background scheduler
    if settings.scan_interval_minutes > 0:
        scheduler.add_job(
            run_scheduled_scan,
            "interval",
            minutes=settings.scan_interval_minutes,
            id="periodic_disk_scan",
            replace_existing=True,
        )
        scheduler.start()
        logger.info(
            f"Background scan scheduler started (interval: {settings.scan_interval_minutes}m)"
        )

    # Start telegram bot if configured
    from yt_manager.telegram_bot import get_telegram_service
    tg_service = get_telegram_service()
    await tg_service.start()

    yield

    # Shutdown telegram bot
    await tg_service.stop()

    # Shutdown scheduler
    if scheduler.running:
        scheduler.shutdown()
        logger.info("Background scan scheduler stopped.")
    logger.info("yt-manager service shut down.")


from fastapi.middleware.cors import CORSMiddleware

app = FastAPI(
    title="yt-manager",
    description="NAS Media Archive & Download Manager for MeTube and yt-dlp",
    version="0.1.0",
    lifespan=lifespan,
)

# Enable CORS for cross-origin requests (e.g., iOS Shortcuts, web dashboards)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(router)


def main():
    uvicorn.run(
        "yt_manager.main:app",
        host="0.0.0.0",
        port=8000,
        reload=False,
        proxy_headers=True,
        forwarded_allow_ips="*",
    )


if __name__ == "__main__":
    main()
