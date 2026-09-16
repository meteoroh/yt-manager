import fnmatch
import os
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from yt_manager.db import Database
from yt_manager.extractor import extract_from_filename

MEDIA_EXTENSIONS = {
    ".mp4",
    ".mkv",
    ".webm",
    ".mov",
    ".avi",
    ".flv",
    ".ts",
    ".m4v",
}

IGNORED_EXTENSIONS = {
    ".part",
    ".ytdl",
    ".temp",
    ".tmp",
}

# Standard NAS and system directories to always exclude
DEFAULT_EXCLUDE_DIR_NAMES = {
    "#recycle",
    "@eaDir",
    ".recycle",
    ".Trash",
    ".Trash-1000",
    "@SynoResource",
    "@SynoEAStream",
    ".git",
    ".idea",
    ".vscode",
}

# If either of these marker files exists in a folder, skip it and all subfolders
IGNORE_MARKER_FILES = {
    ".nomedia",
    ".ytignore",
}


@dataclass
class ScanStats:
    last_scanned_at: Optional[str] = None
    total_files: int = 0
    deleted_files: int = 0
    duration_seconds: float = 0.0


# In-memory latest scan stats for /status reporting
current_stats = ScanStats()


def get_scan_stats() -> ScanStats:
    return current_stats


def is_directory_excluded(dir_path: Path, exclude_dirs: list[str]) -> bool:
    """Check if a directory should be skipped."""
    dir_name = dir_path.name

    # 1. Built-in NAS / system folders
    if dir_name in DEFAULT_EXCLUDE_DIR_NAMES:
        return True

    # 2. Check for ignore marker files inside the directory (.nomedia, .ytignore)
    for marker in IGNORE_MARKER_FILES:
        try:
            if (dir_path / marker).exists():
                return True
        except (PermissionError, FileNotFoundError):
            pass

    # 3. User-defined exclude_dirs
    abs_dir_str = str(dir_path.resolve())
    for item in exclude_dirs:
        item = item.strip()
        if not item:
            continue

        if "/" in item:
            # Path matching (specific path or glob path)
            abs_target = str(Path(item).resolve())
            if (
                abs_dir_str == abs_target
                or abs_dir_str.startswith(abs_target + os.sep)
                or fnmatch.fnmatch(abs_dir_str, item)
            ):
                return True
        else:
            # Directory name matching across all levels
            if fnmatch.fnmatch(dir_name.lower(), item.lower()):
                return True

    return False


def is_file_excluded(file_name: str, exclude_patterns: list[str]) -> bool:
    """Check if a filename matches user-defined exclusion patterns."""
    for pat in exclude_patterns:
        pat = pat.strip()
        if pat and fnmatch.fnmatch(file_name, pat):
            return True
    return False


def _scan_dir_recursive(
    path: Path,
    exclude_dirs: list[str],
    exclude_patterns: list[str],
) -> list[tuple[str, str, str]]:
    """Scan directory recursively using os.scandir with exclusion filtering."""
    results = []
    if not path.is_dir():
        return results

    if is_directory_excluded(path, exclude_dirs):
        return results

    try:
        with os.scandir(path) as it:
            for entry in it:
                try:
                    entry_path = Path(entry.path)
                    if entry.is_dir(follow_symlinks=False):
                        if not is_directory_excluded(entry_path, exclude_dirs):
                            results.extend(
                                _scan_dir_recursive(
                                    entry_path,
                                    exclude_dirs=exclude_dirs,
                                    exclude_patterns=exclude_patterns,
                                )
                            )
                    elif entry.is_file(follow_symlinks=False):
                        name = entry.name
                        suffix = Path(name).suffix.lower()

                        if suffix in IGNORED_EXTENSIONS:
                            continue
                        if suffix not in MEDIA_EXTENSIONS:
                            continue
                        if is_file_excluded(name, exclude_patterns):
                            continue

                        extracted = extract_from_filename(name)
                        if extracted:
                            extractor, video_id = extracted
                            results.append((extractor, video_id, os.path.abspath(entry.path)))
                except (PermissionError, FileNotFoundError):
                    continue
    except (PermissionError, FileNotFoundError):
        pass

    return results


def sync_disks_to_db_and_archive(
    directories: list[Path],
    archive_file_path: Path,
    db: Database,
    exclude_dirs: Optional[list[str]] = None,
    exclude_patterns: Optional[list[str]] = None,
) -> ScanStats:
    """
    1. Scan all given directories for media files (respecting exclusions).
    2. Synchronize SQLite database with found files.
    3. Atomically overwrite archive.txt with current valid records.
    """
    start_time = time.perf_counter()
    dirs_to_exclude = exclude_dirs or []
    patterns_to_exclude = exclude_patterns or []

    # 1. Scan files
    all_records: list[tuple[str, str, str]] = []
    for d in directories:
        if d.exists():
            all_records.extend(
                _scan_dir_recursive(
                    d,
                    exclude_dirs=dirs_to_exclude,
                    exclude_patterns=patterns_to_exclude,
                )
            )

    # 2. Sync to DB
    total_count, deleted_count = db.sync_all(all_records)

    # 3. Atomically dump archive.txt
    archive_pairs = db.get_all_archives()
    archive_file_path = Path(archive_file_path)
    archive_file_path.parent.mkdir(parents=True, exist_ok=True)

    temp_archive = archive_file_path.with_name(f"{archive_file_path.name}.tmp")
    with open(temp_archive, "w", encoding="utf-8") as f:
        for ext, vid in archive_pairs:
            f.write(f"{ext} {vid}\n")

    os.replace(temp_archive, archive_file_path)

    elapsed = round(time.perf_counter() - start_time, 4)

    # 4. Update memory stats
    current_stats.last_scanned_at = datetime.now(timezone.utc).isoformat()
    current_stats.total_files = total_count
    current_stats.deleted_files = deleted_count
    current_stats.duration_seconds = elapsed

    return current_stats
