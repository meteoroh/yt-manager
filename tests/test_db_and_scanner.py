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

