from dataclasses import dataclass, field
import logging
from pathlib import Path
import re
from threading import Lock
import time
from typing import Any, Optional
import yt_dlp

from yt_manager.db import Database

logger = logging.getLogger("yt_manager.playlist")

# In-memory TTL cache for playlist metadata
CACHE_TTL_SECONDS = 300  # 5 minutes
CACHE_MAX_ENTRIES = 50

_playlist_cache: dict[tuple[str, int], tuple[float, "PlaylistInfo"]] = {}
_cache_lock = Lock()


def clear_playlist_cache() -> None:
    """Clear in-memory playlist cache."""
    with _cache_lock:
        _playlist_cache.clear()


def _clone_playlist_info(info: "PlaylistInfo") -> "PlaylistInfo":
    """Deep clone PlaylistInfo with fresh PlaylistItem instances."""
    return PlaylistInfo(
        playlist_id=info.playlist_id,
        title=info.title,
        url=info.url,
        total_items=info.total_items,
        items=[
            PlaylistItem(
                video_id=it.video_id,
                title=it.title,
                url=it.url,
                extractor=it.extractor,
                file_path=it.file_path,
                folder=it.folder,
                downloadable=it.downloadable,
            )
            for it in info.items
        ],
    )


UNAVAILABLE_TITLE_PATTERNS = [
    re.compile(r"^\[?(?:private video|deleted video|unavailable video)\]?$", re.IGNORECASE),
    re.compile(r"^\[?(?:비공개 동영상|삭제된 동영상|사용할 수 없는 동영상)\]?$", re.IGNORECASE),
]


def _is_entry_unavailable(entry: dict, title: str) -> bool:
    """
    Check if a playlist video entry is private, deleted, or otherwise unplayable.
    """
    if not entry:
        return True

    # 1. Check yt-dlp availability flags
    availability = str(entry.get("availability") or "").lower()
    if availability in ("private", "needs_auth"):
        return True
    if entry.get("is_private") is True:
        return True

    # 2. Check title placeholders or missing title (YouTube returns None/empty title for private/deleted videos)
    clean_title = title.strip()
    if not clean_title:
        return True

    for pat in UNAVAILABLE_TITLE_PATTERNS:
        if pat.search(clean_title):
            return True

    return False


PLAYLIST_URL_PATTERNS = [
    # YouTube playlist URL: youtube.com/playlist?list=... or music.youtube.com/playlist?list=...
    re.compile(r"(?:https?://)?(?:www\.|music\.)?youtube\.com/playlist\?.*list=([a-zA-Z0-9_-]+)"),
    # YouTube feed/tab URLs
    re.compile(r"(?:https?://)?(?:www\.)?youtube\.com/feed/([a-zA-Z0-9_-]+)"),
    # YouTube channel URLs
    re.compile(r"(?:https?://)?(?:www\.)?youtube\.com/(?:channel|c|user)/([a-zA-Z0-9_-]+)"),
    re.compile(r"(?:https?://)?(?:www\.)?youtube\.com/@([a-zA-Z0-9_.-]+)"),
]

# Patterns for single videos that happen to include &list= (should be treated as single video)
SINGLE_VIDEO_WITH_LIST_PATTERNS = [
    re.compile(r"(?:https?://)?(?:www\.|music\.)?youtube\.com/watch\?.*v=([a-zA-Z0-9_-]{11})"),
    re.compile(r"(?:https?://)?youtu\.be/([a-zA-Z0-9_-]{11})"),
]


def is_playlist_url(url: str) -> bool:
    """
    Check if the URL is a dedicated playlist, channel, or feed URL.
    Returns False if it is a single video URL with an attached playlist parameter.
    """
    clean_url = url.strip()

    # If it contains a single video pattern, prioritize single video
    for pat in SINGLE_VIDEO_WITH_LIST_PATTERNS:
        if pat.search(clean_url):
            return False

    # Check playlist/channel/feed patterns
    for pat in PLAYLIST_URL_PATTERNS:
        if pat.search(clean_url):
            return True

    return False


@dataclass
class PlaylistItem:
    video_id: str
    title: str
    url: str
    extractor: str = "youtube"
    file_path: Optional[str] = None
    folder: Optional[str] = None
    downloadable: bool = True


@dataclass
class PlaylistInfo:
    playlist_id: str
    title: str
    url: str
    total_items: int
    items: list[PlaylistItem] = field(default_factory=list)


@dataclass
class PlaylistAnalysis:
    playlist_id: str
    title: str
    url: str
    total_count: int
    found_count: int
    missing_count: int
    downloadable_count: int = 0
    found_items: list[PlaylistItem] = field(default_factory=list)
    missing_items: list[PlaylistItem] = field(default_factory=list)



def extract_playlist_info(
    url: str,
    max_items: int = 200,
    use_cache: bool = True,
) -> Optional[PlaylistInfo]:
    """
    Extract playlist metadata and entry list using yt-dlp flat extraction.
    Does not download any video media.
    Uses in-memory cache to avoid duplicate yt-dlp calls if requested within TTL.
    """
    clean_url = url.strip()
    cache_key = (clean_url, max_items)
    now = time.time()

    if use_cache:
        with _cache_lock:
            if cache_key in _playlist_cache:
                timestamp, cached_info = _playlist_cache[cache_key]
                if now - timestamp < CACHE_TTL_SECONDS:
                    logger.debug(f"Playlist cache hit for {clean_url}")
                    return _clone_playlist_info(cached_info)
                else:
                    del _playlist_cache[cache_key]

    try:
        ydl_opts = {
            "quiet": True,
            "skip_download": True,
            "extract_flat": True,
            "no_warnings": True,
            "playlist_items": f"1-{max_items}",
        }
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(clean_url, download=False, process=False)
            if not info:
                return None

            # Must be a playlist type
            if info.get("_type") != "playlist" and "entries" not in info:
                return None

            title = info.get("title") or "Playlist"
            playlist_id = info.get("id") or ""
            raw_entries = info.get("entries") or []

            items: list[PlaylistItem] = []
            seen_video_ids: set[str] = set()
            for entry in raw_entries:
                if len(items) >= max_items:
                    break
                if not entry:
                    continue
                v_id = entry.get("id")
                if not v_id:
                    continue
                str_vid = str(v_id)
                if str_vid in seen_video_ids:
                    continue
                seen_video_ids.add(str_vid)

                v_title = entry.get("title") or ""
                v_url = entry.get("url") or f"https://www.youtube.com/watch?v={str_vid}"
                if not v_url.startswith("http"):
                    v_url = f"https://www.youtube.com/watch?v={str_vid}"
                raw_ie = entry.get("ie_key") or entry.get("extractor") or "youtube"
                extractor = raw_ie.lower().split(":")[0]

                is_unavail = _is_entry_unavailable(entry, v_title)

                items.append(
                    PlaylistItem(
                        video_id=str_vid,
                        title=v_title,
                        url=v_url,
                        extractor=extractor,
                        downloadable=not is_unavail,
                    )
                )

            result = PlaylistInfo(
                playlist_id=str(playlist_id),
                title=title,
                url=clean_url,
                total_items=len(items),
                items=items,
            )

            if use_cache:
                with _cache_lock:
                    if len(_playlist_cache) >= CACHE_MAX_ENTRIES:
                        expired = [
                            k for k, (t, _) in _playlist_cache.items()
                            if now - t >= CACHE_TTL_SECONDS
                        ]
                        for k in expired:
                            del _playlist_cache[k]
                        if len(_playlist_cache) >= CACHE_MAX_ENTRIES:
                            oldest_key = min(
                                _playlist_cache.keys(),
                                key=lambda k: _playlist_cache[k][0],
                            )
                            del _playlist_cache[oldest_key]
                    _playlist_cache[cache_key] = (now, _clone_playlist_info(result))

            return result
    except Exception as e:
        logger.error(f"Error extracting playlist from {url}: {e}")
        return None


def analyze_playlist(
    url: str,
    db: Database,
    max_items: int = 200,
    use_cache: bool = True,
) -> Optional[PlaylistAnalysis]:
    """
    Extract playlist items and compare against SQLite Database.
    Separates into found_items and missing_items.
    """
    info = extract_playlist_info(url, max_items=max_items, use_cache=use_cache)
    if not info:
        return None

    found_items: list[PlaylistItem] = []
    missing_items: list[PlaylistItem] = []

    for item in info.items:
        # Check in DB
        file_path = db.find_by_id(item.extractor, item.video_id)
        if not file_path:
            fb = db.find_by_video_id_only(item.video_id)
            if fb:
                _, file_path = fb

        if file_path:
            item.file_path = file_path
            item.folder = str(Path(file_path).parent)
            found_items.append(item)
        else:
            missing_items.append(item)

    downloadable_count = sum(1 for it in missing_items if it.downloadable)

    return PlaylistAnalysis(
        playlist_id=info.playlist_id,
        title=info.title,
        url=info.url,
        total_count=len(info.items),
        found_count=len(found_items),
        missing_count=len(missing_items),
        downloadable_count=downloadable_count,
        found_items=found_items,
        missing_items=missing_items,
    )
