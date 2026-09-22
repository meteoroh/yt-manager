from pathlib import Path
from yt_manager.db import Database
from yt_manager.scanner import sync_disks_to_db_and_archive


def test_db_and_scanner_workflow(tmp_path: Path):
    downloads_dir = tmp_path / "downloads"
    media_dir = tmp_path / "media"
    downloads_dir.mkdir()
    media_dir.mkdir()

    person_dir = media_dir / "아이유"
    person_dir.mkdir()

    archive_path = tmp_path / "archive.txt"
    db_path = tmp_path / "test.db"

    db = Database(db_path)

    # 1. Create dummy files
    f1 = downloads_dir / "임시영상 [youtube-vid1].mp4"
    f1.write_text("dummy")

    # In-progress file that should be ignored
    f_part = downloads_dir / "다운로드중 [youtube-vid_part].mp4.part"
    f_part.write_text("dummy part")

    f2 = person_dir / "좋은날 [youtube-vid2].mkv"
    f2.write_text("dummy")

    f3 = person_dir / "릴스 [instagram_vid3].mp4"
    f3.write_text("dummy")

    # Run scan
    stats = sync_disks_to_db_and_archive(
        directories=[downloads_dir, media_dir],
        archive_file_path=archive_path,
        db=db,
    )

    assert stats.total_files == 3
    assert db.count() == 3

    # Check lookups
    path1 = db.find_by_id("youtube", "vid1")
    assert path1 is not None
    assert Path(path1).name == "임시영상 [youtube-vid1].mp4"
    assert Path(path1).parent.name == "downloads"

    path2 = db.find_by_id("youtube", "vid2")
    assert path2 is not None
    assert Path(path2).parent.name == "아이유"

    path3 = db.find_by_id("instagram", "vid3")
    assert path3 is not None
    assert Path(path3).parent.name == "아이유"

    # Verify archive.txt contents
    archive_lines = archive_path.read_text(encoding="utf-8").strip().splitlines()
    assert archive_lines == [
        "instagram vid3",
        "youtube vid1",
        "youtube vid2",
    ]

    # 2. Test file deletion and file move
    # Move f1 from downloads to media/아이유
    f1_new = person_dir / "임시영상 [youtube-vid1].mp4"
    f1.rename(f1_new)

    # Delete f3 (instagram vid3)
    f3.unlink()

    # Re-scan
    stats2 = sync_disks_to_db_and_archive(
        directories=[downloads_dir, media_dir],
        archive_file_path=archive_path,
        db=db,
    )

    assert stats2.total_files == 2
    assert stats2.deleted_files == 1
    assert db.count() == 2

    # f3 must be gone
    assert db.find_by_id("instagram", "vid3") is None

    # f1 path must be updated to media/아이유
    updated_path1 = db.find_by_id("youtube", "vid1")
    assert updated_path1 is not None
    assert Path(updated_path1).parent.name == "아이유"

    # archive.txt must no longer contain vid3
    new_archive_lines = archive_path.read_text(encoding="utf-8").strip().splitlines()
    assert new_archive_lines == [
        "youtube vid1",
        "youtube vid2",
    ]


def test_scanner_exclusions(tmp_path: Path):
    media_dir = tmp_path / "media"
    media_dir.mkdir()
    archive_path = tmp_path / "archive.txt"
    db_path = tmp_path / "test_ex.db"
    db = Database(db_path)

    # 1. Normal media file (should be included)
    normal_dir = media_dir / "normal"
    normal_dir.mkdir()
    (normal_dir / "정상영상 [youtube-vid_normal].mp4").write_text("dummy")

    # 2. Built-in excluded folder (#recycle)
    recycle_dir = media_dir / "#recycle"
    recycle_dir.mkdir()
    (recycle_dir / "휴지통영상 [youtube-vid_recycle].mp4").write_text("dummy")

    # 3. .nomedia marker in directory
    nomedia_dir = media_dir / "nomedia_folder"
    nomedia_dir.mkdir()
    (nomedia_dir / ".nomedia").touch()
    (nomedia_dir / "숨김영상 [youtube-vid_nomedia].mp4").write_text("dummy")

    # 4. Folder name match (e.g., 'backup')
    backup_dir = media_dir / "sub" / "backup"
    backup_dir.mkdir(parents=True)
    (backup_dir / "백업영상 [youtube-vid_backup].mp4").write_text("dummy")

    # 5. Specific absolute path match
    private_dir = media_dir / "private"
    private_dir.mkdir()
    (private_dir / "비공개영상 [youtube-vid_private].mp4").write_text("dummy")

    # 6. File pattern match (*sample*)
    (normal_dir / "샘플영상_sample [youtube-vid_sample].mp4").write_text("dummy")

    stats = sync_disks_to_db_and_archive(
        directories=[media_dir],
        archive_file_path=archive_path,
        db=db,
        exclude_dirs=["backup", str(private_dir.resolve())],
        exclude_patterns=["*sample*"],
    )

    # Only 1 file should be indexed (vid_normal)
    assert stats.total_files == 1
    assert db.count() == 1
    assert db.find_by_id("youtube", "vid_normal") is not None

    # Excluded files must not exist in DB
    assert db.find_by_id("youtube", "vid_recycle") is None
    assert db.find_by_id("youtube", "vid_nomedia") is None
    assert db.find_by_id("youtube", "vid_backup") is None
    assert db.find_by_id("youtube", "vid_private") is None
    assert db.find_by_id("youtube", "vid_sample") is None


def test_scanner_dirty_check_skips_disk_writes(tmp_path: Path):
    media_dir = tmp_path / "media"
    media_dir.mkdir()
    archive_path = tmp_path / "archive.txt"
    db_path = tmp_path / "test_dirty.db"
    db = Database(db_path)

    # 1. First scan with 1 file
    f1 = media_dir / "Video 1 [youtube-v1].mp4"
    f1.write_text("dummy")

    stats1 = sync_disks_to_db_and_archive(
        directories=[media_dir],
        archive_file_path=archive_path,
        db=db,
    )
    assert stats1.has_changes is True
    assert stats1.total_files == 1
    assert stats1.added_files == 1
    assert stats1.deleted_files == 0
    assert archive_path.exists()

    archive_mtime_before = archive_path.stat().st_mtime_ns

    # 2. Second scan with NO file changes
    stats2 = sync_disks_to_db_and_archive(
        directories=[media_dir],
        archive_file_path=archive_path,
        db=db,
    )
    assert stats2.has_changes is False
    assert stats2.total_files == 1
    assert stats2.added_files == 0
    assert stats2.deleted_files == 0

    # archive.txt must NOT have been touched/overwritten
    archive_mtime_after = archive_path.stat().st_mtime_ns
    assert archive_mtime_before == archive_mtime_after

    # 3. Third scan: Add new file -> changes detected
    f2 = media_dir / "Video 2 [youtube-v2].mp4"
    f2.write_text("dummy 2")

    stats3 = sync_disks_to_db_and_archive(
        directories=[media_dir],
        archive_file_path=archive_path,
        db=db,
    )
    assert stats3.has_changes is True
    assert stats3.total_files == 2
    assert stats3.added_files == 1
    assert stats3.deleted_files == 0
    assert archive_path.stat().st_mtime_ns != archive_mtime_before


def test_simultaneous_add_and_delete(tmp_path: Path):
    media_dir = tmp_path / "media"
    media_dir.mkdir()
    archive_path = tmp_path / "archive.txt"
    db_path = tmp_path / "test_simultaneous.db"
    db = Database(db_path)

    f1 = media_dir / "Song 1 [youtube-s1].mp4"
    f2 = media_dir / "Song 2 [youtube-s2].mp4"
    f1.write_text("s1")
    f2.write_text("s2")

    # Initial scan: 2 files added
    stats1 = sync_disks_to_db_and_archive(
        directories=[media_dir],
        archive_file_path=archive_path,
        db=db,
    )
    assert stats1.total_files == 2
    assert stats1.added_files == 2
    assert stats1.deleted_files == 0

    # Delete 1 file and add 1 new file simultaneously
    # Total count stays at 2, but added=1, deleted=1
    f1.unlink()
    f3 = media_dir / "Song 3 [youtube-s3].mp4"
    f3.write_text("s3")

    stats2 = sync_disks_to_db_and_archive(
        directories=[media_dir],
        archive_file_path=archive_path,
        db=db,
    )
    assert stats2.total_files == 2
    assert stats2.added_files == 1
    assert stats2.deleted_files == 1
    assert stats2.has_changes is True

    assert db.find_by_id("youtube", "s1") is None
    assert db.find_by_id("youtube", "s2") is not None
    assert db.find_by_id("youtube", "s3") is not None


def test_database_request_history(tmp_path: Path):
    db_path = tmp_path / "test_history.db"
    db = Database(db_path)

    # 1. Record requests from api and telegram
    id1 = db.record_request(
        source="api",
        url="https://youtube.com/watch?v=vid1",
        status="EXISTS",
        extractor="youtube",
        video_id="vid1",
        detail="/media/vid1.mp4",
    )
    id2 = db.record_request(
        source="telegram",
        url="https://x.com/user/status/123",
        status="QUEUED",
        extractor="twitter",
        video_id="123",
    )
    id3 = db.record_request(
        source="api",
        url="https://invalid-url.com",
        status="INVALID",
        detail="Could not extract video ID",
    )

    assert id1 == 1
    assert id2 == 2
    assert id3 == 3

    # 2. Get history with default limit
    all_history = db.get_history()
    assert len(all_history) == 3
    # Ordered by id DESC
    assert all_history[0]["id"] == 3
    assert all_history[0]["status"] == "INVALID"
    assert all_history[1]["id"] == 2
    assert all_history[1]["source"] == "telegram"
    assert all_history[2]["id"] == 1
    assert all_history[2]["detail"] == "/media/vid1.mp4"

    # 3. Filter by source
    api_history = db.get_history(source="api")
    assert len(api_history) == 2
    assert all(h["source"] == "api" for h in api_history)

    tg_history = db.get_history(source="telegram")
    assert len(tg_history) == 1
    assert tg_history[0]["video_id"] == "123"

    # 4. Filter by status
    queued_history = db.get_history(status="QUEUED")
    assert len(queued_history) == 1
    assert queued_history[0]["id"] == 2

    # 5. Filter by video_id
    vid_history = db.get_history(video_id="vid1")
    assert len(vid_history) == 1
    assert vid_history[0]["video_id"] == "vid1"

    # 6. Filter by url
    url_history = db.get_history(url="https://youtube.com/watch?v=vid1")
    assert len(url_history) == 1
    assert url_history[0]["url"] == "https://youtube.com/watch?v=vid1"

    # 7. Filter by url_contains (e.g. partial URL or playlist ID)
    partial_history = db.get_history(url_contains="watch?v=vid1")
    assert len(partial_history) == 1
    assert partial_history[0]["video_id"] == "vid1"


def test_database_cleanup_old_history(tmp_path: Path):
    db_path = tmp_path / "test_cleanup.db"
    db = Database(db_path)

    # Record 1 old request (40 days ago) and 1 recent request
    old_time = "2026-01-01T00:00:00+00:00"
    db.record_request(
        source="api",
        url="https://youtube.com/watch?v=old",
        status="EXISTS",
        created_at=old_time,
    )
    db.record_request(
        source="telegram",
        url="https://youtube.com/watch?v=new",
        status="QUEUED",
    )

    assert len(db.get_history()) == 2

    # Cleanup with 30 days retention
    deleted = db.cleanup_old_history(retention_days=30)
    assert deleted == 1

    remaining = db.get_history()
    assert len(remaining) == 1
    assert remaining[0]["url"] == "https://youtube.com/watch?v=new"

