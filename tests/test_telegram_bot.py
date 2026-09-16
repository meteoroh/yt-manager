from pathlib import Path
import pytest
import httpx

from yt_manager.config import Settings
from yt_manager.db import Database
from yt_manager.metube import MeTubeClient
from yt_manager.telegram_bot import (
    analyze_urls,
    extract_urls_from_text,
    is_user_allowed,
)


def test_extract_urls_from_text():
    sample_text = """
    이거 봐봐 https://www.youtube.com/watch?v=dQw4w9WgXcQ 완전 좋아!
    그리고 트위터 영상 https://x.com/user/status/1598765432109876543
    인스타 릴스도:
    https://www.instagram.com/reel/CW123abcXYZ/
    중복 링크 다시 올림: https://www.youtube.com/watch?v=dQw4w9WgXcQ
    """
    urls = extract_urls_from_text(sample_text)
    assert len(urls) == 3
    assert urls[0] == "https://www.youtube.com/watch?v=dQw4w9WgXcQ"
    assert urls[1] == "https://x.com/user/status/1598765432109876543"
    assert urls[2] == "https://www.instagram.com/reel/CW123abcXYZ/"


def test_analyze_urls_workflow(tmp_path: Path):
    db_path = tmp_path / "tg_test.db"
    db = Database(db_path)

    # Pre-populate 1 item in DB
    db.sync_all([
        ("youtube", "dQw4w9WgXcQ", "/media/아이유/좋은날 [youtube-dQw4w9WgXcQ].mp4")
    ])

    test_urls = [
        "https://www.youtube.com/watch?v=dQw4w9WgXcQ",  # Already in DB
        "https://x.com/user/status/1598765432109876543", # Missing
        "https://www.instagram.com/reel/CW123abcXYZ/",  # Missing
        "https://invalid-non-media.com/something",       # Invalid
    ]

    found, missing, invalid = analyze_urls(test_urls, db)

    assert len(found) == 1
    assert found[0]["video_id"] == "dQw4w9WgXcQ"
    assert found[0]["extractor"] == "youtube"
    assert found[0]["file_path"] == "/media/아이유/좋은날 [youtube-dQw4w9WgXcQ].mp4"

    assert len(missing) == 2
    assert missing[0]["video_id"] == "1598765432109876543"
    assert missing[1]["video_id"] == "CW123abcXYZ"

    assert len(invalid) == 1
    assert invalid[0] == "https://invalid-non-media.com/something"


def test_is_user_allowed():
    # When whitelist is empty -> all allowed
    empty_settings = Settings(telegram_allowed_users="")
    assert is_user_allowed(12345, empty_settings) is True
    assert is_user_allowed(99999, empty_settings) is True

    # When whitelist is set
    configured_settings = Settings(telegram_allowed_users="12345, 67890")
    assert is_user_allowed(12345, configured_settings) is True
    assert is_user_allowed(67890, configured_settings) is True
    assert is_user_allowed(11111, configured_settings) is False


@pytest.mark.asyncio
async def test_metube_add_bulk_downloads():
    metube = MeTubeClient("http://mock-metube:8081")

    # Mock httpx using custom transport or monkeypatch
    call_count = 0

    async def mock_post(client_self, url, *args, **kwargs):
        nonlocal call_count
        call_count += 1
        return httpx.Response(200, json={"status": "ok"}, request=httpx.Request("POST", url))

    # Test with custom client mocking
    urls = [
        "https://youtube.com/watch?v=1",
        "https://youtube.com/watch?v=2",
        "https://youtube.com/watch?v=3",
    ]

    # Temporarily monkeypatch httpx.AsyncClient.post
    import httpx
    original_post = httpx.AsyncClient.post
    try:
        httpx.AsyncClient.post = mock_post
        success_count, results = await metube.add_bulk_downloads(urls)
        assert success_count == 3
        assert len(results) == 3
        assert call_count == 3
    finally:
        httpx.AsyncClient.post = original_post


@pytest.mark.asyncio
async def test_metube_error_handling():
    metube = MeTubeClient("http://mock-metube:8081")

    # Case 1: MeTube returns HTTP 200 with status: error
    async def mock_post_error(client_self, url, *args, **kwargs):
        return httpx.Response(
            200,
            json={"status": "error", "msg": "Video is private or deleted"},
            request=httpx.Request("POST", url),
        )

    import httpx
    original_post = httpx.AsyncClient.post
    try:
        httpx.AsyncClient.post = mock_post_error
        res = await metube.add_download("https://youtube.com/watch?v=private")
        assert res["success"] is False
        assert "Video is private or deleted" in res["error"]

        # Bulk download should count it as failure
        success_cnt, results = await metube.add_bulk_downloads(["https://youtube.com/watch?v=private"])
        assert success_cnt == 0
        assert results[0]["success"] is False
    finally:
        httpx.AsyncClient.post = original_post
