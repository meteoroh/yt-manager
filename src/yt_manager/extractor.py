import re
from pathlib import Path
from typing import Optional
import yt_dlp

KNOWN_EXTRACTORS = {
    "youtube",
    "twitter",
    "tiktok",
    "instagram",
    "facebook",
    "bilibili",
    "twitch",
    "soundcloud",
    "vimeo",
    "threads",
    "weibo",
    "xiaohongshu",
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

XIAOHONGSHU_URL_PATTERNS = [
    re.compile(
        r"(?:https?://)?(?:www\.)?(?:rednote|xiaohongshu)\.com/(?:explore|discovery/item)/([0-9a-f]{24})"
    ),
    re.compile(
        r"(?:https?://)?(?:www\.)?(?:rednote|xiaohongshu)\.com/user/profile/[0-9a-f]{24}/([0-9a-f]{24})"
    ),
]


def normalize_extractor(extractor: str) -> str:
    """Normalize extractor names to match yt-dlp archive conventions."""
    return extractor.lower().strip()


_EXTRACTOR_REGEX = "|".join(re.escape(e) for e in sorted(KNOWN_EXTRACTORS, key=len, reverse=True))
EXTRACTOR_PATTERN = re.compile(
    rf"\[({_EXTRACTOR_REGEX})[- _]([a-zA-Z0-9_-]+)\]$",
    re.IGNORECASE,
)


def extract_from_filename(filename_or_path: str | Path) -> Optional[tuple[str, str]]:
    """
    Extract (extractor, video_id) from filename.
    Strictly supports explicit extractor prefixes:
      - [extractor-id]   e.g., "Song [youtube-dQw4w9WgXcQ].mp4"
      - [extractor id]   e.g., "Song [youtube dQw4w9WgXcQ].mp4"
      - [extractor_id]   e.g., "Song [youtube_dQw4w9WgXcQ].mp4"
    Returns None if no explicit valid extractor tag is present.
    """
    stem = Path(filename_or_path).stem

    m = EXTRACTOR_PATTERN.search(stem)
    if not m:
        return None

    cand_ext = m.group(1).lower()
    cand_id = m.group(2)
    return cand_ext, cand_id


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

    # 5. Xiaohongshu / Rednote
    for pat in XIAOHONGSHU_URL_PATTERNS:
        m = pat.search(clean_url)
        if m:
            return "xiaohongshu", m.group(1)

    # 6. yt-dlp fallback (for shortlinks like vt.tiktok.com, t.co, etc.)
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
                    # Ignore playlists, channels, feeds in single video extractor
                    if info.get("_type") == "playlist" or "entries" in info:
                        return None

                    ext = info.get("ie_key") or info.get("extractor") or ""
                    if "tab" in ext.lower():
                        return None

                    vid = info.get("id")
                    if ext and vid:
                        norm_ext = normalize_extractor(ext.split(":")[0])
                        return norm_ext, str(vid)
        except Exception:
            pass

    return None
