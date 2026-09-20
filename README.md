# yt-manager

A lightweight media archive manager and verification service for NAS. It uses **actual files on disk as the Single Source of Truth (SSOT)** to track saved videos across platforms (YouTube, TikTok, Twitter/X, Instagram, etc.), automatically synchronizing a 3-column SQLite DB and yt-dlp's `archive.txt`.

---

## Key Features

- **SSOT on Disk**: SQLite DB (`extractor`, `video_id`, `file_path`) automatically tracks file additions, moves, and deletions.
- **Safe `archive.txt` Sync**: Atomic file replacement via `tempfile` and `os.replace` prevents race conditions with MeTube / yt-dlp.
- **Smart Exclusions**: Automatically ignores NAS system folders (`#recycle`, `@eaDir`), `.nomedia` / `.ytignore` markers, and custom glob patterns.
- **Fast Verification API**: FastAPI endpoints responding in < 100ms with ownership status and storage locations.
- **Telegram Bot with Bulk Support**: Long-polling bot supporting multi-link checking, one-click parallel bulk downloads, and request history.
- **Request URL Logging**: Logs all incoming video queries with 30-day auto-retention cleanup.

---

## Recommended Downloader Settings

Configure downloaders to include `[extractor-id]` in filenames so `yt-manager` can identify them:

```text
%(title)s [%(extractor)s-%(id)s].%(ext)s
```

- **MeTube Docker**: Set `OUTPUT_TEMPLATE` to the string above and `YTDL_OPTIONS={"download_archive": "/media/archive.txt"}`.
- **yt-dlp CLI**: Add `-o "%(title)s [%(extractor)s-%(id)s].%(ext)s"` and `--download-archive "/Volumes/media/archive.txt"`.

---

## Quick Start

### Docker / NAS Deployment (Recommended)

```bash
cp .env.example .env   # Adjust volume paths and settings in .env
docker compose up -d --build
```

### Local Development

```bash
uv sync
uv run pytest          # Run test suite
uv run yt-manager      # Starts on http://0.0.0.0:8000
```

---

## Integrations

### 📱 iOS Shortcut
Verify ownership and trigger downloads directly from the iOS Share Sheet:  
👉 **[Download Official iOS Shortcut (iCloud)](https://www.icloud.com/shortcuts/d7f73ccd0af945c8993fea6514146167)**

### 🤖 Telegram Bot
Works over LTE/5G via Long Polling (no port forwarding or VPN needed).
- **URL / Text / `.txt` file**: Automatically extracts links, checks ownership, and provides an inline `[Download]` button for missing videos.
- **Commands**:
  - `/history [N | URL | video_id]`: View recent requests (default: 20, max: 50) or trace history of a specific link/ID.
  - `/scan`: Trigger an instant disk scan & `archive.txt` sync.
  - `/status`: View total saved videos and scan stats.
  - `/start`: Display user ID (use with `TELEGRAM_ALLOWED_USERS` whitelist).

---

## API Summary

| Endpoint | Method | Description | Key Parameters / Payload |
| :--- | :--- | :--- | :--- |
| `/check-video` | `POST` | Check video ownership | `{"url": "...", "auto_download": false}` |
| `/download` | `POST` | Forward download to MeTube | `{"url": "...", "quality": "best"}` |
| `/history` | `GET` | Query request history | `limit`, `offset`, `source`, `status`, `video_id`, `url` |
| `/scan` | `POST` | Trigger immediate disk scan | - |
| `/status` | `GET` | Get total media count & scan info | - |
| `/health` | `GET` | Healthcheck (`{"status": "ok"}`) | - |

<details>
<summary><b>View /check-video JSON Response Example</b></summary>

```json
{
  "exists": true,
  "extractor": "youtube",
  "video_id": "dQw4w9WgXcQ",
  "folder": "/media/IU",
  "file_name": "Good Day [youtube-dQw4w9WgXcQ].mp4",
  "file_path": "/media/IU/Good Day [youtube-dQw4w9WgXcQ].mp4",
  "message": "Video already exists."
}
```
</details>

---

## Appendix: Filename Migration Tool (`yt-migrate`)

Safely standardizes legacy yt-dlp filenames (`[id]` $\rightarrow$ `[extractor-id]`):

```bash
docker exec -it yt-manager yt-migrate /media           # Dry-run preview (safe)
docker exec -it yt-manager yt-migrate /media --apply   # Apply renaming on disk
```
