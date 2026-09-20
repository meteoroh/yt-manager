from pathlib import Path
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    # NAS directories
    downloads_dir: Path = Path("/downloads")
    media_dir: Path = Path("/media")
    archive_file_path: Path = Path("/media/archive.txt")
    db_path: Path = Path("/media/yt_manager.db")

    # MeTube instance
    metube_url: str = "http://localhost:8081"

    # Background scanner
    scan_interval_minutes: int = 120
    auto_scan_on_startup: bool = True

    # Exclusion settings (comma-separated strings)
    # e.g., EXCLUDE_DIRS="backup,temp,/media/private"
    # e.g., EXCLUDE_PATTERNS="*sample*,*.temp.mp4"
    exclude_dirs: str = ""
    exclude_patterns: str = ""

    # Telegram Bot
    telegram_bot_token: str | None = None
    # Comma-separated allowed Telegram user IDs (e.g., "12345678,87654321"). If empty, all users allowed.
    telegram_allowed_users: str = ""

    # Request history logging retention (in days)
    request_history_retention_days: int = 30

    @property
    def parsed_exclude_dirs(self) -> list[str]:
        if not self.exclude_dirs:
            return []
        return [d.strip() for d in self.exclude_dirs.split(",") if d.strip()]

    @property
    def parsed_exclude_patterns(self) -> list[str]:
        if not self.exclude_patterns:
            return []
        return [p.strip() for p in self.exclude_patterns.split(",") if p.strip()]

    @property
    def parsed_telegram_allowed_users(self) -> set[int]:
        if not self.telegram_allowed_users:
            return set()
        user_ids = set()
        for u in self.telegram_allowed_users.split(","):
            u = u.strip()
            if u.isdigit():
                user_ids.add(int(u))
        return user_ids

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )


settings = Settings()


def get_settings() -> Settings:
    return settings
