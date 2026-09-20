from pathlib import Path
import pytest

from yt_manager.disk import (
    format_bytes,
    get_configured_disks_usage,
    get_disk_usage,
)


def test_format_bytes():
    assert format_bytes(-10) == "0 B"
    assert format_bytes(0) == "0.0 B"
    assert format_bytes(512) == "512.0 B"
    assert format_bytes(1024) == "1.0 KB"
    assert format_bytes(1024 * 1024) == "1.0 MB"
    assert format_bytes(int(1.5 * 1024 * 1024 * 1024)) == "1.5 GB"
    assert format_bytes(2 * 1024 * 1024 * 1024 * 1024) == "2.0 TB"


def test_get_disk_usage_existing(tmp_path: Path):
    usage = get_disk_usage(tmp_path)
    assert usage is not None
    assert usage.path == str(tmp_path)
    assert usage.total_bytes > 0
    assert usage.free_bytes >= 0
    assert usage.used_bytes >= 0
    assert 0.0 <= usage.percent_used <= 100.0
    assert "B" in usage.total_human or "GB" in usage.total_human or "TB" in usage.total_human


def test_get_disk_usage_nonexistent(tmp_path: Path):
    nonexistent = tmp_path / "does_not_exist"
    assert get_disk_usage(nonexistent) is None


def test_get_configured_disks_usage(tmp_path: Path):
    media_dir = tmp_path / "media"
    downloads_dir = tmp_path / "downloads"
    media_dir.mkdir()

    # 1. downloads_dir does not exist yet -> only media_dir returned
    disks = get_configured_disks_usage(media_dir, downloads_dir)
    assert len(disks) == 1
    assert disks[0].path == str(media_dir)

    # 2. downloads_dir exists -> both returned
    downloads_dir.mkdir()
    disks2 = get_configured_disks_usage(media_dir, downloads_dir)
    assert len(disks2) == 2
    assert disks2[0].path == str(media_dir)
    assert disks2[1].path == str(downloads_dir)

    # 3. Same directory -> deduplicated
    disks3 = get_configured_disks_usage(media_dir, media_dir)
    assert len(disks3) == 1
    assert disks3[0].path == str(media_dir)
