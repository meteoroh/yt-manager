from pathlib import Path
from yt_manager.tools.migrate_filenames import (
    get_new_filename,
    load_archive_lookup,
    scan_and_migrate,
)


def test_get_new_filename_detection():
    lookup = {}

    # 1. YouTube
    yt_res = get_new_filename("영상 제목 [dQw4w9WgXcQ].webm", lookup)
    assert yt_res is not None
    assert yt_res[0] == "영상 제목 [youtube-dQw4w9WgXcQ].webm"
    assert yt_res[1] == "youtube"

    # 2. Twitter
    tw_res = get_new_filename("아이유 - 콘서트 직캠 [1598765432109876543].mp4", lookup)
    assert tw_res is not None
    assert tw_res[0] == "아이유 - 콘서트 직캠 [twitter-1598765432109876543].mp4"
    assert tw_res[1] == "twitter"

    # 3. Instagram
    ig_res = get_new_filename("Video by iu_official [CW123abcXYZ].mp4", lookup)
    assert ig_res is not None
    assert ig_res[0] == "Video by iu_official [instagram-CW123abcXYZ].mp4"
    assert ig_res[1] == "instagram"

    # 4. TikTok
    tt_res = get_new_filename("챌린지 댄스 영상 [7123456789012345678].mp4", lookup)
    assert tt_res is not None
    assert tt_res[0] == "챌린지 댄스 영상 [tiktok-7123456789012345678].mp4"
    assert tt_res[1] == "tiktok"

    # 5. Already normalized (should be skipped)
    assert get_new_filename("정상 [youtube-dQw4w9WgXcQ].mp4", lookup) is None
    assert get_new_filename("정상 [tiktok-7123456789012345678].mp4", lookup) is None

    # 6. Archive lookup priority
    archive_lookup = {"7123456789012345678": "custom_platform"}
    custom_res = get_new_filename("영상 [7123456789012345678].mp4", archive_lookup)
    assert custom_res is not None
    assert custom_res[0] == "영상 [custom_platform-7123456789012345678].mp4"


def test_dry_run_vs_apply(tmp_path: Path):
    media_dir = tmp_path / "media"
    media_dir.mkdir()

    # Create test file
    yt_file = media_dir / "노래 [dQw4w9WgXcQ].mp4"
    yt_file.write_text("dummy")

    # 1. Test dry-run: file should NOT be renamed
    scan_and_migrate(media_dir, apply=False, archive_lookup={})
    assert yt_file.exists()
    assert not (media_dir / "노래 [youtube-dQw4w9WgXcQ].mp4").exists()

    # 2. Test apply: file SHOULD be renamed
    scan_and_migrate(media_dir, apply=True, archive_lookup={})
    assert not yt_file.exists()
    assert (media_dir / "노래 [youtube-dQw4w9WgXcQ].mp4").exists()
