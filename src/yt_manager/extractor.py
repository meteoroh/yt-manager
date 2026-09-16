import re
from pathlib import Path
from typing import Optional
import yt_dlp

KNOWN_EXTRACTORS = {
    "youtube",
    "twitter",
    "x",
    "tiktok",
    "instagram",
    "facebook",
    "bilibili",
    "twitch",
    "soundcloud",
    "vimeo",
    "threads",
    "weibo",
}

# Regex patterns for fast URL matching
YOUTUBE_URL_PATTERNS = [
    re.compile(r"(?:https?://)?(?:www\.)?youtube\.com/watch\?.*v=([a-zA-Z0-9_-]{11})"),
    re.compile(r"(?:https?://)?youtu\.be/([a-zA-Z0-9_-]{11})"),
    re.compile(r"(?:https?://)?(?:www\.)?youtube\.com/shorts/([a-zA-Z0-9_-]{11})"),
    re.compile(r"(?:https?://)?(?:www\.)?youtube\.com/embed/([a-zA-Z0-9_-]{11})"),
    re.compile(r"(?:https?://)?(?:www\.)?youtube\.com/live/([a-zA-Z0-9_-]{11})"),
]

TWITTER_URL_PATTERN = re.compile(
    r"(?:https?://)?(?:www\.|mobile\.)?(?:twitter|x)\.com/(?:#!/)?\w+/status(?:es)?/(\d+)"
)

TIKTOK_URL_PATTERN = re.compile(
    r"(?:https?://)?(?:www\.)?tiktok\.com/@[^/]+/video/(\d+)"
)

INSTAGRAM_URL_PATTERN = re.compile(
    r"(?:https?://)?(?:www\.)?instagram\.com/(?:p|reel|reels|tv)/([a-zA-Z0-9_-]+)"
)


def normalize_extractor(extractor: str) -> str:
    """Normalize extractor names to match yt-dlp archive conventions."""
    ext = extractor.lower().strip()
    if ext == "x":
        return "twitter"
    return ext


def extract_from_filename(filename_or_path: str | Path) -> Optional[tuple[str, str]]:
    """
    Extract (extractor, video_id) from filename.
    Supports:
      1. Explicit extractor prefix:
         - [extractor-id]   e.g., "Song [youtube-dQw4w9WgXcQ].mp4"
         - [extractor id]   e.g., "Song [youtube dQw4w9WgXcQ].mp4"
         - [extractor_id]   e.g., "Song [youtube_dQw4w9WgXcQ].mp4"
      2. Single bracket id:
         - [id] where id is 11 chars (YouTube) -> ('youtube', id)
      3. Fallback standard yt-dlp:
         - "-<11-char-id>.ext" at end of filename -> ('youtube', id)
    """
    stem = Path(filename_or_path).stem

    # Pattern 1: [extractor<sep>id]
    bracket_match = re.search(r"\[([a-zA-Z0-9_-]+?)[- _]([a-zA-Z0-9_-]+?)\]$", stem)
    if bracket_match:
        cand_ext, cand_id = bracket_match.group(1), bracket_match.group(2)
        norm_ext = normalize_extractor(cand_ext)
        if norm_ext in KNOWN_EXTRACTORS or cand_ext.isalpha():
            return norm_ext, cand_id

    # Pattern 2: [id]
    single_bracket_match = re.search(r"\[([a-zA-Z0-9_-]+)\]$", stem)
    if single_bracket_match:
        cand_id = single_bracket_match.group(1)
        # If 11 characters (standard YouTube ID), assume youtube
        if len(cand_id) == 11 and re.match(r"^[a-zA-Z0-9_-]{11}$", cand_id):
            return "youtube", cand_id

    # Pattern 3: yt-dlp default suffix "-<id>"
    dash_match = re.search(r"-([a-zA-Z0-9_-]{11})$", stem)
    if dash_match:
        return "youtube", dash_match.group(1)

    return None


def extract_from_url(url: str, use_ytdlp_fallback: bool = True) -> Optional[tuple[str, str]]:
    """
    Extract (extractor, video_id) from a given media URL.
    Tries high-speed regex matching first, and falls back to yt-dlp extract_info.
    """
    clean_url = url.strip()

    # 1. YouTube
    for pat in YOUTUBE_URL_PATTERNS:
        m = pat.search(clean_url)
        if m:
            return "youtube", m.group(1)

    # 2. Twitter / X
    m = TWITTER_URL_PATTERN.search(clean_url)
    if m:
        return "twitter", m.group(1)

    # 3. TikTok
    m = TIKTOK_URL_PATTERN.search(clean_url)
    if m:
        return "tiktok", m.group(1)

    # 4. Instagram
    m = INSTAGRAM_URL_PATTERN.search(clean_url)
    if m:
        return "instagram", m.group(1)

    # 5. yt-dlp fallback (for shortlinks like vt.tiktok.com, t.co, etc.)
    if use_ytdlp_fallback:
        try:
            ydl_opts = {
                "quiet": True,
                "skip_download": True,
                "extract_flat": True,
                "no_warnings": True,
            }
            with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                info = ydl.extract_info(clean_url, download=False, process=False)
                if info:
                    ext = info.get("ie_key") or info.get("extractor") or ""
                    vid = info.get("id")
                    if ext and vid:
                        return normalize_extractor(ext), str(vid)
        except Exception:
            pass

    return None
