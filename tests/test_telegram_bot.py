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


@pytest.mark.asyncio
async def test_telegram_callback_query_auth():
    from unittest.mock import AsyncMock, MagicMock
    from yt_manager.telegram_bot import TelegramBotService

    settings = Settings(telegram_allowed_users="12345")
    service = TelegramBotService(settings)

    # Unauthorized user clicks button
    mock_query = AsyncMock()
    mock_user = MagicMock()
    mock_user.id = 99999  # Unauthorized

    mock_update = MagicMock()
    mock_update.effective_user = mock_user
    mock_update.message = None
    mock_update.callback_query = mock_query
    mock_query.data = "dl_single:test1234"

    await service.handle_callback_query(mock_update, MagicMock())

    # Must answer with alert and not proceed
    mock_query.answer.assert_awaited_once_with("Access denied. (ID: 99999)", show_alert=True)
    mock_query.edit_message_reply_markup.assert_not_awaited()


@pytest.mark.asyncio
async def test_process_and_respond_with_underscore_url(tmp_path: Path):
    from unittest.mock import AsyncMock, MagicMock
    from yt_manager.telegram_bot import TelegramBotService

    db_path = tmp_path / "tg_test_underscore.db"
    Database(db_path)  # initialize
    settings = Settings(db_path=db_path)
    service = TelegramBotService(settings)

    mock_update = MagicMock()
    mock_update.message = AsyncMock()

    url = "https://youtu.be/olpfpCoh3xw?si=fRSdGeD0_UQH1d8e"
    await service._process_and_respond(mock_update, [url])

    mock_update.message.reply_text.assert_awaited_once()
    called_args, called_kwargs = mock_update.message.reply_text.call_args
    assert called_kwargs.get("parse_mode") == "HTML"
    assert "olpfpCoh3xw" in called_args[0]
    assert "• Platform: <b>YOUTUBE</b>" in called_args[0]


@pytest.mark.asyncio
async def test_telegram_handle_history(tmp_path: Path):
    from unittest.mock import AsyncMock, MagicMock
    from yt_manager.telegram_bot import TelegramBotService

    db_path = tmp_path / "tg_test_hist.db"
    db = Database(db_path)
    settings = Settings(db_path=db_path)
    service = TelegramBotService(settings)

    mock_update = MagicMock()
    mock_update.effective_user = MagicMock(id=123)
    mock_update.message = AsyncMock()
    mock_context = MagicMock()
    mock_context.args = []

    # 1. When history is empty
    await service.handle_history(mock_update, mock_context)
    mock_update.message.reply_text.assert_awaited_with("No request history found.")

    # 2. When history has items
    db.record_request(
        source="telegram",
        url="https://youtube.com/watch?v=abc12345678",
        status="EXISTS",
        extractor="youtube",
        video_id="abc12345678",
        detail="/media/Music/artist/song.mp4",
    )
    db.record_request(
        source="api",
        url="https://x.com/status/999",
        status="FAILED",
        extractor="twitter",
        video_id="999",
        detail="Private or deleted video",
    )
    db.record_request(
        source="telegram",
        url="https://youtube.com/watch?v=queued12345",
        status="QUEUED",
        extractor="youtube",
        video_id="queued12345",
    )

    mock_update.message.reply_text.reset_mock()
    mock_context.args = ["5"]
    await service.handle_history(mock_update, mock_context)

    mock_update.message.reply_text.assert_awaited_once()
    called_text = mock_update.message.reply_text.call_args[0][0]
    assert "Recent Request History (3)" in called_text
    assert "EXISTS" in called_text
    assert "FAILED" in called_text
    assert "QUEUED" in called_text
    assert "Private or deleted video" in called_text
    assert "/media/Music/artist" in called_text
    # Verify newest item (QUEUED) is at the bottom (chronological order)
    assert called_text.find("EXISTS") < called_text.find("FAILED") < called_text.find("QUEUED")

    # 3. Search history by URL
    mock_update.message.reply_text.reset_mock()
    mock_context.args = ["https://youtube.com/watch?v=abc12345678"]
    await service.handle_history(mock_update, mock_context)
    mock_update.message.reply_text.assert_awaited_once()
    called_text = mock_update.message.reply_text.call_args[0][0]
    assert "Request History for [YOUTUBE] <code>abc12345678</code> (1)" in called_text
    assert "[EXISTS]" in called_text

    # 4. Search history by direct video_id
    mock_update.message.reply_text.reset_mock()
    mock_context.args = ["999"]
    await service.handle_history(mock_update, mock_context)
    mock_update.message.reply_text.assert_awaited_once()
    called_text = mock_update.message.reply_text.call_args[0][0]
    assert "999" in called_text
    assert "[FAILED]" in called_text

    # 5. Search history for unknown URL
    mock_update.message.reply_text.reset_mock()
    mock_context.args = ["https://youtube.com/watch?v=never_seen"]
    await service.handle_history(mock_update, mock_context)
    mock_update.message.reply_text.assert_awaited_once()
    called_text = mock_update.message.reply_text.call_args[0][0]
    assert "No request history found for:" in called_text

    # 6. Search history by playlist ID (without full URL)
    db.record_request(
        source="telegram",
        url="https://www.youtube.com/playlist?list=PL_TEST_PLAYLIST_ID",
        status="CHECKED",
        detail="Playlist: 10 found, 2 missing out of 12",
    )
    db.record_request(
        source="telegram",
        url="https://www.youtube.com/watch?v=vid_from_pl",
        status="QUEUED",
        extractor="youtube",
        video_id="vid_from_pl",
        detail="Playlist: PL_TEST_PLAYLIST_ID",
    )
    mock_update.message.reply_text.reset_mock()
    mock_context.args = ["PL_TEST_PLAYLIST_ID"]
    await service.handle_history(mock_update, mock_context)
    mock_update.message.reply_text.assert_awaited_once()
    called_text = mock_update.message.reply_text.call_args[0][0]
    assert "PL_TEST_PLAYLIST_ID" in called_text
    assert "[CHECKED]" in called_text
    assert "[QUEUED]" in called_text
    assert "vid_from_pl" in called_text

    # 7. Search history by full playlist URL
    mock_update.message.reply_text.reset_mock()
    mock_context.args = ["https://www.youtube.com/playlist?list=PL_TEST_PLAYLIST_ID"]
    await service.handle_history(mock_update, mock_context)
    mock_update.message.reply_text.assert_awaited_once()
    called_text2 = mock_update.message.reply_text.call_args[0][0]
    assert "[CHECKED]" in called_text2
    assert "[QUEUED]" in called_text2
    assert "vid_from_pl" in called_text2


@pytest.mark.asyncio
async def test_process_and_respond_records_history(tmp_path: Path):
    from unittest.mock import AsyncMock, MagicMock
    from yt_manager.telegram_bot import TelegramBotService

    db_path = tmp_path / "tg_test_proc.db"
    db = Database(db_path)
    db.sync_all([("youtube", "existing123", "/media/folder/existing123.mp4")])

    settings = Settings(db_path=db_path)
    service = TelegramBotService(settings)

    mock_update = MagicMock()
    mock_update.message = AsyncMock()

    urls = [
        "https://www.youtube.com/watch?v=existing123",
        "https://www.youtube.com/watch?v=missing4567",
        "https://invalid-domain.com/test",
    ]

    await service._process_and_respond(mock_update, urls)

    history = db.get_history(limit=10)
    assert len(history) == 3
    # All sources should be 'telegram'
    assert all(h["source"] == "telegram" for h in history)

    statuses = {h["status"]: h for h in history}
    assert "EXISTS" in statuses
    assert statuses["EXISTS"]["video_id"] == "existing123"

    assert "MISSING" in statuses
    assert statuses["MISSING"]["video_id"] == "missing4567"

    assert "INVALID" in statuses
    assert statuses["INVALID"]["url"] == "https://invalid-domain.com/test"


@pytest.mark.asyncio
async def test_handle_callback_query_records_history(tmp_path: Path, monkeypatch):
    from unittest.mock import AsyncMock, MagicMock
    from yt_manager.telegram_bot import TelegramBotService, ACTION_CACHE
    from yt_manager.metube import MeTubeClient

    db_path = tmp_path / "tg_test_cb.db"
    db = Database(db_path)
    settings = Settings(db_path=db_path)
    service = TelegramBotService(settings)

    # Mock MeTubeClient.add_download
    async def mock_download_success(self, url, **kwargs):
        return {"success": True, "status": "ok"}

    monkeypatch.setattr(MeTubeClient, "add_download", mock_download_success)

    # 1. Successful download via dl_single
    action_id = "test_act1"
    ACTION_CACHE[action_id] = ["https://www.youtube.com/watch?v=downloadMe1"]

    mock_query = AsyncMock()
    mock_query.data = f"dl_single:{action_id}"
    mock_query.message = AsyncMock()
    mock_user = MagicMock(id=123)

    mock_update = MagicMock()
    mock_update.effective_user = mock_user
    mock_update.message = None
    mock_update.callback_query = mock_query

    await service.handle_callback_query(mock_update, MagicMock())

    hist = db.get_history(limit=5)
    assert len(hist) == 1
    assert hist[0]["source"] == "telegram"
    assert hist[0]["status"] == "QUEUED"
    assert hist[0]["video_id"] == "downloadMe1"

    # 2. Failed download via dl_single
    async def mock_download_failed(self, url, **kwargs):
        return {"success": False, "error": "Video unavailable"}

    monkeypatch.setattr(MeTubeClient, "add_download", mock_download_failed)

    action_id2 = "test_act2"
    ACTION_CACHE[action_id2] = ["https://www.youtube.com/watch?v=failMe12345"]
    mock_query.data = f"dl_single:{action_id2}"

    await service.handle_callback_query(mock_update, MagicMock())

    hist = db.get_history(limit=5)
    assert len(hist) == 2
    assert hist[0]["status"] == "FAILED"
    assert hist[0]["source"] == "telegram"
    assert hist[0]["video_id"] == "failMe12345"
    assert hist[0]["detail"] == "Video unavailable"


@pytest.mark.asyncio
async def test_send_chunked_reply_splits_properly():
    from unittest.mock import AsyncMock
    from yt_manager.telegram_bot import send_chunked_reply

    mock_msg = AsyncMock()
    # Create 10 lines of 500 chars each (total 5000 chars)
    lines = [f"Line {i:02d}: " + "x" * 490 for i in range(10)]

    # Max 1200 chars per chunk -> should split into ~5 messages
    await send_chunked_reply(mock_msg, lines, parse_mode="HTML", max_chars=1200, reply_markup="dummy_markup")

    # Check calls
    assert mock_msg.reply_text.await_count > 1
    # Only the final chunk should receive the reply_markup
    calls = mock_msg.reply_text.await_args_list
    assert calls[0].kwargs.get("reply_markup") is None
    assert calls[-1].kwargs.get("reply_markup") == "dummy_markup"
    for call in calls:
        assert len(call.args[0]) <= 1200


@pytest.mark.asyncio
async def test_handle_status_and_disk(tmp_path: Path):
    from unittest.mock import AsyncMock, MagicMock
    from yt_manager.telegram_bot import TelegramBotService

    media_dir = tmp_path / "media"
    downloads_dir = tmp_path / "downloads"
    media_dir.mkdir()
    downloads_dir.mkdir()
    db_path = tmp_path / "tg.db"

    settings = Settings(
        media_dir=media_dir,
        downloads_dir=downloads_dir,
        db_path=db_path,
        archive_file_path=tmp_path / "archive.txt",
    )
    service = TelegramBotService(settings)

    # 1. Test handle_status
    mock_update = MagicMock()
    mock_user = MagicMock()
    mock_user.id = 12345
    mock_update.effective_user = mock_user
    mock_update.message = AsyncMock()

    await service.handle_status(mock_update, MagicMock())
    mock_update.message.reply_text.assert_awaited_once()
    status_text = mock_update.message.reply_text.call_args[0][0]
    assert "System Status" in status_text
    assert f"Disk ({media_dir})" in status_text
    assert f"Disk ({downloads_dir})" in status_text

    # 2. Test handle_disk
    mock_update.message.reset_mock()
    await service.handle_disk(mock_update, MagicMock())
    mock_update.message.reply_text.assert_awaited_once()
    disk_text = mock_update.message.reply_text.call_args[0][0]
    assert "Disk Storage Status" in disk_text
    assert str(media_dir) in disk_text
    assert str(downloads_dir) in disk_text


@pytest.mark.asyncio
async def test_handle_playlist_message(tmp_path: Path, monkeypatch):
    from unittest.mock import AsyncMock, MagicMock
    import yt_dlp
    from yt_manager.telegram_bot import TelegramBotService

    db_path = tmp_path / "tg.db"
    db = Database(db_path)
    db.sync_all([
        ("youtube", "vidA", "/media/Artist/Song A [youtube-vidA].mp4")
    ])

    settings = Settings(
        media_dir=tmp_path / "media",
        db_path=db_path,
        archive_file_path=tmp_path / "archive.txt",
    )
    service = TelegramBotService(settings)

    mock_info = {
        "_type": "playlist",
        "id": "PL_TG_TEST",
        "title": "Telegram Playlist",
        "entries": [
            {"id": "vidA", "title": "Song A", "url": "https://www.youtube.com/watch?v=vidA", "ie_key": "Youtube"},
            {"id": "vidB", "title": "Song B", "url": "https://www.youtube.com/watch?v=vidB", "ie_key": "Youtube"},
        ],
    }

    class MockYoutubeDL:
        def __init__(self, *args, **kwargs):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *args):
            pass

        def extract_info(self, url, download=False, process=False):
            return mock_info

    monkeypatch.setattr(yt_dlp, "YoutubeDL", MockYoutubeDL)

    mock_update = MagicMock()
    mock_user = MagicMock()
    mock_user.id = 12345
    mock_update.effective_user = mock_user

    mock_status_msg = AsyncMock()
    mock_update.message.reply_text = AsyncMock(return_value=mock_status_msg)
    mock_update.message.text = "Check this https://www.youtube.com/playlist?list=PL_TG_TEST"

    await service.handle_text_message(mock_update, MagicMock())

    # Verify "Analyzing playlist..." was sent
    mock_update.message.reply_text.assert_awaited_with("Analyzing playlist...")
    # Verify status_msg was edited with playlist stats
    mock_status_msg.edit_text.assert_awaited_once()
    edited_text = mock_status_msg.edit_text.call_args[0][0]
    assert "Telegram Playlist" in edited_text
    assert "Total Videos: <b>2</b>" in edited_text
    assert "Already Saved: <b>1</b>" in edited_text
    assert "Missing Videos: <b>1</b>" in edited_text

    # Verify inline keyboard has "Download (1)"
    reply_markup = mock_status_msg.edit_text.call_args[1].get("reply_markup")
    assert reply_markup is not None
    assert reply_markup.inline_keyboard[0][0].text == "Download (1)"


@pytest.mark.asyncio
async def test_telegram_playlist_with_unavailable_videos(tmp_path: Path, monkeypatch):
    from unittest.mock import AsyncMock, MagicMock
    import yt_dlp
    from yt_manager.playlist import clear_playlist_cache
    from yt_manager.telegram_bot import TelegramBotService

    clear_playlist_cache()
    db_path = tmp_path / "tg_unavail.db"
    db = Database(db_path)
    db.sync_all([("youtube", "vidA", "/media/Artist/Song A [youtube-vidA].mp4")])

    settings = Settings(media_dir=tmp_path / "media", db_path=db_path)
    service = TelegramBotService(settings)

    mock_entries = [
        {"id": "vidA", "title": "Song A", "url": "https://www.youtube.com/watch?v=vidA"},
        {"id": "vidB", "title": "Song B", "url": "https://www.youtube.com/watch?v=vidB"},
        {"id": "privC", "title": "[비공개 동영상]", "url": "https://www.youtube.com/watch?v=privC"},
    ]

    class MockYoutubeDL:
        def __init__(self, *args, **kwargs):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *args):
            pass

        def extract_info(self, url, download=False, process=False):
            return {
                "_type": "playlist",
                "id": "PL_TG_UNAVAIL",
                "title": "TG Unavail",
                "entries": mock_entries,
            }

    monkeypatch.setattr(yt_dlp, "YoutubeDL", MockYoutubeDL)

    mock_update = MagicMock()
    mock_user = MagicMock()
    mock_user.id = 12345
    mock_update.effective_user = mock_user

    mock_status_msg = AsyncMock()
    mock_update.message.reply_text = AsyncMock(return_value=mock_status_msg)
    mock_update.message.text = "https://www.youtube.com/playlist?list=PL_TG_UNAVAIL"

    # Case 1: 1 saved, 1 downloadable, 1 private
    await service.handle_text_message(mock_update, MagicMock())
    mock_status_msg.edit_text.assert_awaited_once()
    edited_text = mock_status_msg.edit_text.call_args[0][0]
    assert "Unavailable/Private: <b>1</b>" in edited_text
    assert "1</b> downloadable" in edited_text

    # Button must be Download (1) (only the downloadable one)
    reply_markup = mock_status_msg.edit_text.call_args[1].get("reply_markup")
    assert reply_markup is not None
    assert reply_markup.inline_keyboard[0][0].text == "Download (1)"

    # Case 2: Now vidB is also saved in DB, only private video left
    db.sync_all([
        ("youtube", "vidA", "/media/Artist/Song A [youtube-vidA].mp4"),
        ("youtube", "vidB", "/media/Artist/Song B [youtube-vidB].mp4"),
    ])
    mock_status_msg.edit_text.reset_mock()
    await service.handle_text_message(mock_update, MagicMock())

    mock_status_msg.edit_text.assert_awaited_once()
    edited_text2 = mock_status_msg.edit_text.call_args[0][0]
    assert "All downloadable videos in this playlist are already saved!" in edited_text2
    # No download button should be rendered when downloadable_count == 0
    reply_markup2 = mock_status_msg.edit_text.call_args[1].get("reply_markup")
    assert reply_markup2 is None


