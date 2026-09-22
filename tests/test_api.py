from pathlib import Path
import pytest
from fastapi.testclient import TestClient

from yt_manager.api import get_db, get_settings
from yt_manager.config import Settings
from yt_manager.db import Database
from yt_manager.main import app


@pytest.fixture
def test_env(tmp_path: Path):
    downloads_dir = tmp_path / "downloads"
    media_dir = tmp_path / "media"
    downloads_dir.mkdir()
    media_dir.mkdir()

    person_dir = media_dir / "아이유"
    person_dir.mkdir()

    f = person_dir / "콘서트 [youtube-dQw4w9WgXcQ].mp4"
    f.write_text("dummy")

    archive_path = tmp_path / "archive.txt"
    db_path = tmp_path / "test.db"

    test_settings = Settings(
        downloads_dir=downloads_dir,
        media_dir=media_dir,
        archive_file_path=archive_path,
        db_path=db_path,
        metube_url="http://mock-metube:8081",
        scan_interval_minutes=0,  # disable background scan in test
        auto_scan_on_startup=False,
    )

    test_db = Database(db_path)

    app.dependency_overrides[get_settings] = lambda: test_settings
    app.dependency_overrides[get_db] = lambda: test_db

    yield {
        "settings": test_settings,
        "db": test_db,
        "media_dir": media_dir,
        "person_dir": person_dir,
    }

    app.dependency_overrides.clear()


def test_api_check_video_and_scan(test_env):
    client = TestClient(app)

    # 1. Before scan: video not found in DB
    resp1 = client.post(
        "/check-video",
        json={"url": "https://www.youtube.com/watch?v=dQw4w9WgXcQ"},
    )
    assert resp1.status_code == 200
    data1 = resp1.json()
    assert data1["exists"] is False

    # 2. Trigger scan
    scan_resp = client.post("/scan")
    assert scan_resp.status_code == 200
    scan_data = scan_resp.json()
    assert scan_data["status"] == "ok"
    assert scan_data["total_files"] == 1

    # 3. After scan: video exists and located in "아이유"
    resp2 = client.post(
        "/check-video",
        json={"url": "https://www.youtube.com/watch?v=dQw4w9WgXcQ"},
    )
    assert resp2.status_code == 200
    data2 = resp2.json()
    assert data2["exists"] is True
    assert data2["extractor"] == "youtube"
    assert data2["video_id"] == "dQw4w9WgXcQ"
    assert data2["folder"] == str(test_env["person_dir"].resolve())
    assert data2["message"] == "Video already exists."

    # 4. Check status (including disk storage info)
    status_resp = client.get("/status")
    assert status_resp.status_code == 200
    status_data = status_resp.json()
    assert status_data["status"] == "ok"
    assert status_data["total_media_in_db"] == 1
    assert "disks" in status_data
    assert len(status_data["disks"]) >= 1
    assert any(d["path"] == str(test_env["media_dir"]) for d in status_data["disks"])

    # 4-1. Check dedicated /disk endpoint
    disk_resp = client.get("/disk")
    assert disk_resp.status_code == 200
    disk_data = disk_resp.json()
    assert isinstance(disk_data, list)
    assert len(disk_data) >= 1
    assert disk_data[0]["total_bytes"] > 0
    assert "free_human" in disk_data[0]

    # 5. Check health
    health_resp = client.get("/health")
    assert health_resp.status_code == 200
    assert health_resp.json() == {"status": "ok"}

    # Verify HealthCheckFilter silences /health log records
    import logging
    from yt_manager.main import HealthCheckFilter
    filt = HealthCheckFilter()
    rec_health = logging.LogRecord("uvicorn.access", logging.INFO, "", 0, '127.0.0.1 - "GET /health HTTP/1.1" 200 OK', (), None)
    rec_other = logging.LogRecord("uvicorn.access", logging.INFO, "", 0, '127.0.0.1 - "GET /status HTTP/1.1" 200 OK', (), None)
    assert filt.filter(rec_health) is False
    assert filt.filter(rec_other) is True

    # 6. Verify request history recorded from check-video calls
    hist_resp = client.get("/history")
    assert hist_resp.status_code == 200
    hist_data = hist_resp.json()
    assert hist_data["total_returned"] == 2
    # The most recent call was EXISTS (from step 3)
    assert hist_data["history"][0]["status"] == "EXISTS"
    assert hist_data["history"][0]["source"] == "api"
    assert hist_data["history"][0]["video_id"] == "dQw4w9WgXcQ"
    # The first call was MISSING (from step 1)
    assert hist_data["history"][1]["status"] == "MISSING"
    assert hist_data["history"][1]["source"] == "api"

    # 7. Test invalid URL recording
    resp_invalid = client.post("/check-video", json={"url": "https://unknown.site/page"})
    assert resp_invalid.status_code == 200
    hist_invalid = client.get("/history?status=INVALID")
    assert hist_invalid.status_code == 200
    assert hist_invalid.json()["total_returned"] == 1
    assert hist_invalid.json()["history"][0]["status"] == "INVALID"


def test_api_download_and_history(test_env, monkeypatch):
    client = TestClient(app)
    from yt_manager.metube import MeTubeClient

    # Mock MeTube add_download success
    async def mock_add_download_success(self, url, **kwargs):
        return {"success": True, "status": "ok", "url": url}

    monkeypatch.setattr(MeTubeClient, "add_download", mock_add_download_success)

    dl_resp = client.post(
        "/download",
        json={"url": "https://www.youtube.com/watch?v=dQw4w9WgXcQ", "quality": "best"},
    )
    assert dl_resp.status_code == 200
    assert dl_resp.json()["success"] is True

    # Verify history recorded QUEUED
    hist = client.get("/history?status=QUEUED")
    assert hist.status_code == 200
    items = hist.json()["history"]
    assert len(items) == 1
    assert items[0]["source"] == "api"
    assert items[0]["status"] == "QUEUED"
    assert items[0]["video_id"] == "dQw4w9WgXcQ"

    # Verify history filter by video_id
    vid_hist = client.get("/history?video_id=dQw4w9WgXcQ")
    assert vid_hist.status_code == 200
    assert len(vid_hist.json()["history"]) >= 1
    assert all(h["video_id"] == "dQw4w9WgXcQ" for h in vid_hist.json()["history"])

    # Verify history filter by nonexistent video_id
    none_hist = client.get("/history?video_id=nonexistent")
    assert none_hist.status_code == 200
    assert len(none_hist.json()["history"]) == 0


def test_api_check_unified_and_playlist(test_env, monkeypatch):
    client = TestClient(app)
    import yt_dlp

    mock_playlist_info = {
        "_type": "playlist",
        "id": "PL_API_TEST",
        "title": "API Playlist",
        "entries": [
            {"id": "dQw4w9WgXcQ", "title": "Saved Song", "url": "https://www.youtube.com/watch?v=dQw4w9WgXcQ", "ie_key": "Youtube"},
            {"id": "newSong123", "title": "New Song", "url": "https://www.youtube.com/watch?v=newSong123", "ie_key": "Youtube"},
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
            if "playlist" in url:
                return mock_playlist_info
            return None

    monkeypatch.setattr(yt_dlp, "YoutubeDL", MockYoutubeDL)

    # 1. First trigger a scan so dQw4w9WgXcQ is in DB
    client.post("/scan")

    # 2. Test POST /check with single video (already exists)
    resp_v = client.post("/check", json={"url": "https://www.youtube.com/watch?v=dQw4w9WgXcQ"})
    assert resp_v.status_code == 200
    data_v = resp_v.json()
    assert data_v["type"] == "video"
    assert data_v["video"]["exists"] is True
    assert data_v["video"]["video_id"] == "dQw4w9WgXcQ"

    # 3. Test POST /check with playlist URL
    resp_pl = client.post("/check", json={"url": "https://www.youtube.com/playlist?list=PL_API_TEST"})
    assert resp_pl.status_code == 200
    data_pl = resp_pl.json()
    assert data_pl["type"] == "playlist"
    pl_detail = data_pl["playlist"]
    assert pl_detail["total_count"] == 2
    assert pl_detail["found_count"] == 1
    assert pl_detail["missing_count"] == 1
    assert pl_detail["missing_items"][0]["video_id"] == "newSong123"

    # 4. Test POST /check with invalid URL
    resp_inv = client.post("/check", json={"url": "https://completely-unknown.org/whatever"})
    assert resp_inv.status_code == 200
    assert resp_inv.json()["type"] == "invalid"

    # 5. Test POST /check-video with playlist URL (should guard and return message)
    resp_guard = client.post("/check-video", json={"url": "https://www.youtube.com/playlist?list=PL_API_TEST"})
    assert resp_guard.status_code == 200
    assert resp_guard.json()["exists"] is False
    assert "playlist" in resp_guard.json()["message"].lower()

    # 6. Test dedicated POST /check-playlist
    resp_pl_ded = client.post("/check-playlist", json={"url": "https://www.youtube.com/playlist?list=PL_API_TEST"})
    assert resp_pl_ded.status_code == 200
    assert resp_pl_ded.json()["playlist_id"] == "PL_API_TEST"
    assert resp_pl_ded.json()["found_count"] == 1
    assert resp_pl_ded.json()["missing_count"] == 1

    # 7. Test max_items override
    resp_capped = client.post(
        "/check-playlist",
        json={"url": "https://www.youtube.com/playlist?list=PL_API_TEST", "max_items": 1},
    )
    assert resp_capped.status_code == 200
    assert resp_capped.json()["total_count"] == 1

    # 8. Test GET /history?url_contains=PL_API_TEST
    hist_pl = client.get("/history?url_contains=PL_API_TEST")
    assert hist_pl.status_code == 200
    assert len(hist_pl.json()["history"]) >= 1
    assert hist_pl.json()["history"][0]["status"] == "CHECKED"



def test_api_download_playlist_and_bulk(test_env, monkeypatch):
    client = TestClient(app)
    import yt_dlp
    from yt_manager.metube import MeTubeClient

    mock_playlist_info = {
        "_type": "playlist",
        "id": "PL_DL_TEST",
        "title": "Download Playlist",
        "entries": [
            {"id": "dQw4w9WgXcQ", "title": "Saved Song", "url": "https://www.youtube.com/watch?v=dQw4w9WgXcQ", "ie_key": "Youtube"},
            {"id": "dlSong456", "title": "To Download", "url": "https://www.youtube.com/watch?v=dlSong456", "ie_key": "Youtube"},
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
            if "playlist" in url:
                return mock_playlist_info
            return None

    monkeypatch.setattr(yt_dlp, "YoutubeDL", MockYoutubeDL)

    # Mock MeTube bulk download
    queued_urls = []
    async def mock_bulk(self, urls, **kwargs):
        queued_urls.extend(urls)
        return len(urls), [{"success": True, "url": u} for u in urls]

    monkeypatch.setattr(MeTubeClient, "add_bulk_downloads", mock_bulk)

    # Scan so dQw4w9WgXcQ is marked as exists
    client.post("/scan")

    # 1. Download playlist URL: only dlSong456 should be queued (dQw4w9WgXcQ skipped)
    dl_pl = client.post("/download", json={"url": "https://www.youtube.com/playlist?list=PL_DL_TEST"})
    assert dl_pl.status_code == 200
    pl_res = dl_pl.json()
    assert pl_res["success"] is True
    assert pl_res["queued_count"] == 1
    assert pl_res["skipped_count"] == 1
    assert "dlSong456" in queued_urls[0]

    # Check history recorded summary 1 record
    # Check history recorded individual QUEUED video record
    hist = client.get("/history?status=QUEUED")
    assert hist.status_code == 200
    items = hist.json()["history"]
    assert items[0]["video_id"] == "dlSong456"
    assert "dlSong456" in items[0]["url"]
    assert items[0]["status"] == "QUEUED"

    # 2. Bulk download with explicit urls list
    queued_urls.clear()
    bulk_resp = client.post(
        "/download",
        json={"urls": ["https://www.youtube.com/watch?v=songA123456", "https://www.youtube.com/watch?v=songB123456"]},
    )
    assert bulk_resp.status_code == 200
    assert bulk_resp.json()["queued_count"] == 2
    assert len(queued_urls) == 2

    # Check that both bulk URLs were recorded individually as QUEUED
    hist_all = client.get("/history?status=QUEUED")
    assert hist_all.status_code == 200
    all_queued = hist_all.json()["history"]
    assert len(all_queued) == 3  # 1 from playlist + 2 from bulk
    queued_vids = {h["video_id"] for h in all_queued}
    assert "dlSong456" in queued_vids
    assert "songA123456" in queued_vids
    assert "songB123456" in queued_vids

    # 3. Test GET /history?url_contains=songA123456
    hist_part = client.get("/history?url_contains=songA123456")
    assert hist_part.status_code == 200
    assert len(hist_part.json()["history"]) == 1
    assert hist_part.json()["history"][0]["video_id"] == "songA123456"


def test_api_ios_shortcut_check_then_download_cache_hit(test_env, monkeypatch):
    """
    Simulates iOS Shortcut workflow:
    1. Call POST /check with playlist URL.
    2. User confirms alert.
    3. Call POST /download with same playlist URL.
    Verifies yt-dlp flat extraction is only executed ONCE due to in-memory caching.
    """
    from yt_manager.playlist import clear_playlist_cache
    from yt_manager.metube import MeTubeClient
    import yt_dlp

    clear_playlist_cache()
    client = TestClient(app)

    extract_call_count = 0
    mock_playlist_info = {
        "_type": "playlist",
        "id": "PL_SHORTCUT",
        "title": "Shortcut Playlist",
        "entries": [
            {"id": "songOne1111", "title": "Song 1", "url": "https://www.youtube.com/watch?v=songOne1111", "ie_key": "Youtube"},
            {"id": "songTwo2222", "title": "Song 2", "url": "https://www.youtube.com/watch?v=songTwo2222", "ie_key": "Youtube"},
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
            nonlocal extract_call_count
            extract_call_count += 1
            return mock_playlist_info

    monkeypatch.setattr(yt_dlp, "YoutubeDL", MockYoutubeDL)

    # Mock MeTube client
    async def mock_bulk(self, urls, **kwargs):
        return len(urls), [{"success": True, "url": u} for u in urls]

    monkeypatch.setattr(MeTubeClient, "add_bulk_downloads", mock_bulk)

    playlist_url = "https://www.youtube.com/playlist?list=PL_SHORTCUT"

    # Step 1: iOS Shortcut calls /check
    resp_check = client.post("/check", json={"url": playlist_url})
    assert resp_check.status_code == 200
    data_check = resp_check.json()
    assert data_check["type"] == "playlist"
    assert data_check["playlist"]["total_count"] == 2
    assert data_check["playlist"]["missing_count"] == 2
    assert extract_call_count == 1

    # Step 2 & 3: User confirms in iOS Shortcut, Shortcut calls /download with same URL
    resp_dl = client.post("/download", json={"url": playlist_url})
    assert resp_dl.status_code == 200
    data_dl = resp_dl.json()
    assert data_dl["success"] is True
    assert data_dl["queued_count"] == 2

    # CRITICAL: extract_call_count MUST still be 1 (cache hit, no duplicate yt-dlp call!)
    assert extract_call_count == 1


