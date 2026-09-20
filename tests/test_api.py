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
