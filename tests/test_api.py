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
    assert data2["folder"] == "아이유"
    assert "이미 저장된 영상입니다" in data2["message"]

    # 4. Check status
    status_resp = client.get("/status")
    assert status_resp.status_code == 200
    status_data = status_resp.json()
    assert status_data["status"] == "ok"
    assert status_data["total_media_in_db"] == 1

    # 5. Check health
    health_resp = client.get("/health")
    assert health_resp.status_code == 200
    assert health_resp.json() == {"status": "ok"}
