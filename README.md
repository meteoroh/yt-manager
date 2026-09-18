# yt-manager

A lightweight media archive manager and verification service for NAS. It uses **actual files on disk as the Single Source of Truth (SSOT)** to keep track of collected videos across multiple platforms (YouTube, TikTok, Twitter/X, Instagram, etc.), synchronizing a 3-column SQLite DB and yt-dlp's `archive.txt` automatically.

---

## Key Features

1. **Ultra-Lightweight SQLite Database (3 Columns)**:
   - Stores only `(extractor, video_id, file_path)` without unnecessary overhead.
2. **Atomic `archive.txt` Synchronization**:
   - Generates and replaces `archive.txt` atomically via `tempfile` and `os.replace` to prevent race conditions with MeTube or yt-dlp.
3. **Automatic Deletion & Move Tracking**:
   - Moving files to organized folders (e.g. `/media/Artist/`) or deleting unwanted videos updates the DB and `archive.txt` on the next scan, allowing re-downloads if needed.
4. **Smart Scan Exclusions**:
   - Built-in ignoring of NAS system folders (`#recycle`, `@eaDir`, `.recycle`, `.Trash`, `.git`).
   - `.nomedia` / `.ytignore` marker files skip parent folders and all subfolders.
   - Configurable path-based and folder-name exclusions (`EXCLUDE_DIRS`).
   - Filename glob pattern exclusions (`EXCLUDE_PATTERNS`).
5. **Mobile Verification API (FastAPI)**:
   - Responds in < 100ms with ownership status and full destination path for iOS Shortcuts.
6. **Telegram Bot with Bulk Actions**:
   - Long-polling architecture: works on LTE/5G without port forwarding or VPN.
   - Bulk URL recognition: analyzes multiple links in a single message or `.txt` file attachments.
   - One-click bulk download: queues missing videos to MeTube in parallel via `asyncio.gather`.
   - Security whitelist: restrict access by Telegram User ID (`TELEGRAM_ALLOWED_USERS`).
7. **One-Time Filename Migration Tool**:
   - Safely standardizes legacy yt-dlp filenames (`[id]` $\rightarrow$ `[extractor-id]`) with `--dry-run` preview.

---

## Recommended Downloader Settings

To ensure the scanner reliably extracts the platform (`extractor`) and video ID (`id`), set your output template across downloaders:

```text
%(title)s [%(extractor)s-%(id)s].%(ext)s
```

### MeTube Docker Configuration Example
```yaml
environment:
  - OUTPUT_TEMPLATE=%(title)s [%(extractor)s-%(id)s].%(ext)s
  - YTDL_OPTIONS={"download_archive": "/media/archive.txt"}
```

### Local PC yt-dlp Configuration (`~/.config/yt-dlp/config`)
```text
-o "%(title)s [%(extractor)s-%(id)s].%(ext)s"
--download-archive "/Volumes/media/archive.txt"
```
*(Note: For legacy files containing only `[id]`, use `tools/migrate_filenames.py` to standardize them to `[extractor-id]`.)*

---

## Configuration (`.env`)

Copy `.env.example` to `.env` and adjust the paths to match your NAS storage:

```bash
cp .env.example .env
```

```env
# Storage directories
DOWNLOADS_DIR=/downloads
MEDIA_DIR=/media
ARCHIVE_FILE_PATH=/media/archive.txt
DB_PATH=/media/yt_manager.db

# MeTube instance
METUBE_URL=http://localhost:8081

# Background scanner
SCAN_INTERVAL_MINUTES=120
AUTO_SCAN_ON_STARTUP=true

# Scan exclusions (comma-separated)
# - Names without slash: matches any folder of that name (e.g. backup, temp)
# - Paths with slash: matches specific absolute/relative paths (e.g. /media/private)
EXCLUDE_DIRS=backup,temp,/media/private
EXCLUDE_PATTERNS=*sample*,test_*,*.temp.mp4

# Telegram Bot (Optional, auto-starts if token is provided)
TELEGRAM_BOT_TOKEN=123456789:ABCdefGhIJKlmNoPQRsTUVwxyZ
TELEGRAM_ALLOWED_USERS=12345678
```

---

## Getting Started

### Local / Development

```bash
# Install dependencies with uv
uv sync

# Run test suite (12 tests)
uv run pytest

# Start the server (default port 8000)
uv run yt-manager
# or
uv run uvicorn yt_manager.main:app --host 0.0.0.0 --port 8000 --reload
```

### Docker / NAS Deployment

```bash
# 1. Update host volume paths in docker-compose.yml
# 2. Build and launch container in background
docker compose up -d --build

# Inspect real-time logs
docker compose logs -f yt-manager
```

---

## API Reference

### 1. `POST /check-video` (Check Ownership)
- **Request**:
  ```json
  {
    "url": "https://www.youtube.com/watch?v=dQw4w9WgXcQ",
    "auto_download": false
  }
  ```
- **Response (Already Saved)**:
  ```json
  {
    "exists": true,
    "extractor": "youtube",
    "video_id": "dQw4w9WgXcQ",
    "folder": "IU",
    "file_name": "Good Day [youtube-dQw4w9WgXcQ].mp4",
    "file_path": "/media/IU/Good Day [youtube-dQw4w9WgXcQ].mp4",
    "message": "Video already exists. (Location: /media/IU/Good Day [youtube-dQw4w9WgXcQ].mp4)",
    "download_triggered": false,
    "download_result": null
  }
  ```
- **Response (Not Saved)**:
  ```json
  {
    "exists": false,
    "extractor": "youtube",
    "video_id": "dQw4w9WgXcQ",
    "folder": null,
    "file_name": null,
    "file_path": null,
    "message": "Video not found.",
    "download_triggered": false,
    "download_result": null
  }
  ```
  *(Setting `auto_download: true` will immediately forward the download to MeTube if not found.)*

### 2. `POST /download` (Forward to MeTube)
```json
{
  "url": "https://www.youtube.com/watch?v=dQw4w9WgXcQ",
  "quality": "best"
}
```

### 3. `POST /scan` (Trigger Disk Scan)
Scans disks immediately, syncs the DB, and dumps `archive.txt`.
- **Response**:
  ```json
  {
    "status": "ok",
    "total_files": 1250,
    "added_files": 2,
    "deleted_files": 1,
    "duration_seconds": 0.0452,
    "last_scanned_at": "2026-09-17T15:00:00.000000+00:00",
    "has_changes": true
  }
  ```

### 4. `GET /status` (Server Status)
Returns total media count, last scan timestamp, and scan duration.

### 5. `GET /health`
Healthcheck endpoint (`{"status": "ok"}`).

---

## iOS Shortcut Integration

You can install the ready-to-use iOS Shortcut directly via iCloud:

👉 **[Download Official iOS Shortcut (iCloud)](https://www.icloud.com/shortcuts/d7f73ccd0af945c8993fea6514146167)**

### Shortcut Workflow
1. **Share Sheet Trigger**: Share any video link from YouTube, Safari, Twitter/X, Instagram, or TikTok.
2. **Verify Ownership (`POST /check-video`)**:
   - Sends the video URL to `https://<YOUR_DOMAIN>/check-video`.
   - If already saved: Displays notification with folder name and file name.
3. **Interactive Download (`POST /download`)**:
   - If not saved: Prompts to download via MeTube.
   - If confirmed: Submits download to MeTube and reports real-time success or MeTube error details.

---

## Telegram Bot & Bulk Action Guide

The Telegram bot uses **Long Polling**, which requires no router port-forwarding, dynamic DNS, or VPN when accessing from LTE/5G.

### Key Capabilities
1. **Single & Bulk URL Detection**:
   - Send multiple links in a single message or upload a `.txt` file containing URLs line by line.
2. **One-Click Bulk Download**:
   - If missing videos are detected, the bot provides a `[Download (N)]` inline button to queue all missing URLs to MeTube simultaneously.
3. **Commands**:
   - `/scan`: Instantly triggers a NAS disk rescan and `archive.txt` sync.
   - `/status`: Displays current total media count and system health.
   - `/start`: Shows welcome message and displays your Telegram User ID.
4. **Access Control**:
   - Set `TELEGRAM_ALLOWED_USERS=12345678` in `.env` to restrict bot usage to authorized accounts only.

---

## Appendix: One-Time Filename Migration Tool (`yt-migrate`)

A safe utility to standardize legacy yt-dlp filenames (`[id]` $\rightarrow$ `[extractor-id]`):

- **Rules Applied**:
  - YouTube: `Title [id].mp4` $\rightarrow$ `Title [youtube-id].mp4` (11-character base64url or `.webm`)
  - Twitter: `Username - Title [id].mp4` $\rightarrow$ `Username - Title [twitter-id].mp4` (18-20 digit number with ` - `)
  - Instagram: `Video by user [id].mp4` $\rightarrow$ `Video by user [instagram-id].mp4` (starts with `Video by `)
  - TikTok: `Title [id].mp4` $\rightarrow$ `Title [tiktok-id].mp4` (18-20 digit number)

- **Usage**:
  ```bash
  # Inside Docker Container
  # 1. Preview changes (Default Dry-Run: no files changed)
  docker exec -it yt-manager yt-migrate /media

  # 2. Apply rename on actual disk
  docker exec -it yt-manager yt-migrate /media --apply

  # 3. (Optional) Match against existing archive.txt for 100% exact mapping
  docker exec -it yt-manager yt-migrate /media --archive-file /media/archive.txt --apply

  # Or locally via uv
  uv run yt-migrate /path/to/media
  ```
