import os
from dataclasses import dataclass
from pathlib import Path
import shutil
from typing import Optional


@dataclass
class DiskUsageInfo:
    path: str
    total_bytes: int
    used_bytes: int
    free_bytes: int
    total_human: str
    used_human: str
    free_human: str
    percent_used: float


def format_bytes(bytes_count: int) -> str:
    """Format bytes into human-readable string (e.g., 500.0 MB, 1.5 GB, 3.8 TB)."""
    if bytes_count < 0:
        return "0 B"
    units = ["B", "KB", "MB", "GB", "TB", "PB"]
    value = float(bytes_count)
    unit_index = 0
    while value >= 1024.0 and unit_index < len(units) - 1:
        value /= 1024.0
        unit_index += 1
    return f"{value:.1f} {units[unit_index]}"


def get_disk_usage(path: Path | str) -> Optional[DiskUsageInfo]:
    """
    Get disk usage for a single directory path.
    Returns None if the directory does not exist, is not a directory, or fails to query.
    """
    p = Path(path)
    if not p.exists() or not p.is_dir():
        return None

    try:
        usage = shutil.disk_usage(str(p))
        total = usage.total
        used = usage.used
        free = usage.free
        percent = round((used / total * 100), 1) if total > 0 else 0.0

        return DiskUsageInfo(
            path=str(p),
            total_bytes=total,
            used_bytes=used,
            free_bytes=free,
            total_human=format_bytes(total),
            used_human=format_bytes(used),
            free_human=format_bytes(free),
            percent_used=percent,
        )
    except OSError:
        return None


def get_configured_disks_usage(
    media_dir: Path | str,
    downloads_dir: Optional[Path | str] = None,
) -> list[DiskUsageInfo]:
    """
    Get disk usage for MEDIA_DIR and DOWNLOADS_DIR (if it exists).
    Ensures MEDIA_DIR is always included if it exists, followed by DOWNLOADS_DIR
    if it exists and is not the same directory.
    """
    results: list[DiskUsageInfo] = []
    seen_paths = set()

    # 1. MEDIA_DIR
    media_p = Path(media_dir)
    media_info = get_disk_usage(media_p)
    if media_info:
        results.append(media_info)
        try:
            seen_paths.add(str(media_p.resolve()))
        except Exception:
            seen_paths.add(str(media_p))

    # 2. DOWNLOADS_DIR (only if it exists and resolves to a different directory)
    if downloads_dir:
        dl_p = Path(downloads_dir)
        if dl_p.exists() and dl_p.is_dir():
            try:
                resolved_dl = str(dl_p.resolve())
            except Exception:
                resolved_dl = str(dl_p)

            if resolved_dl not in seen_paths:
                dl_info = get_disk_usage(dl_p)
                if dl_info:
                    results.append(dl_info)

    return results
