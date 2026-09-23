from pathlib import Path
from unittest.mock import MagicMock
import pytest
import yt_dlp

from yt_manager.db import Database
from yt_manager.playlist import (
    PlaylistItem,
    analyze_playlist,
    extract_playlist_info,
    is_playlist_url,
)


def test_is_playlist_url():
    # True cases: dedicated playlist, channels, feeds
    assert is_playlist_url("https://www.youtube.com/playlist?list=PLrAXtmErZgOdP_8GztsuKi9nhOM3yq44E") is True
    assert is_playlist_url("https://music.youtube.com/playlist?list=PLrAXtmErZgOdP_8GztsuKi9nhOM3yq44E") is True
    assert is_playlist_url("https://www.youtube.com/feed/history") is True
    assert is_playlist_url("https://www.youtube.com/@IU") is True
    assert is_playlist_url("https://www.youtube.com/channel/UCBo1hnzxV9rz3WVsv__Rn1g") is True

    # False cases: single videos (even with &list= parameter attached)
    assert is_playlist_url("https://www.youtube.com/watch?v=dQw4w9WgXcQ") is False
    assert is_playlist_url("https://www.youtube.com/watch?v=dQw4w9WgXcQ&list=RDdQw4w9WgXcQ") is False
    assert is_playlist_url("https://youtu.be/dQw4w9WgXcQ?list=RDdQw4w9WgXcQ") is False
    assert is_playlist_url("https://www.youtube.com/shorts/dQw4w9WgXcQ") is False
    assert is_playlist_url("https://x.com/user/status/1234567890") is False


def test_extract_playlist_info(monkeypatch):
    mock_info = {
        "_type": "playlist",
        "id": "PL_TEST_123",
        "title": "My Favorite Songs",
        "entries": [
            {
                "id": "vid1",
                "title": "Song 1",
                "url": "https://www.youtube.com/watch?v=vid1",
                "ie_key": "Youtube",
            },
            {
                "id": "vid2",
                "title": "Song 2",
                "url": "https://www.youtube.com/watch?v=vid2",
                "ie_key": "Youtube",
            },
            None,  # test resilience against None entries
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

    info = extract_playlist_info("https://www.youtube.com/playlist?list=PL_TEST_123")
    assert info is not None
    assert info.playlist_id == "PL_TEST_123"
    assert info.title == "My Favorite Songs"
    assert info.total_items == 2
    assert info.items[0].video_id == "vid1"
    assert info.items[1].video_id == "vid2"


def test_analyze_playlist(tmp_path: Path, monkeypatch):
    db_path = tmp_path / "test.db"
    db = Database(db_path)

    # Pre-populate 1 video in DB: vid1 is saved, vid2 is missing
    db.sync_all([
        ("youtube", "vid1", "/media/Artist/Song 1 [youtube-vid1].mp4")
    ])

    mock_info = {
        "_type": "playlist",
        "id": "PL_ANALYZE",
        "title": "Test Playlist",
        "entries": [
            {
                "id": "vid1",
                "title": "Song 1",
                "url": "https://www.youtube.com/watch?v=vid1",
                "ie_key": "Youtube",
            },
            {
                "id": "vid2",
                "title": "Song 2",
                "url": "https://www.youtube.com/watch?v=vid2",
                "ie_key": "Youtube",
            },
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

    analysis = analyze_playlist("https://www.youtube.com/playlist?list=PL_ANALYZE", db)
    assert analysis is not None
    assert analysis.total_count == 2
    assert analysis.found_count == 1
    assert analysis.missing_count == 1
    assert analysis.found_items[0].video_id == "vid1"
    assert analysis.found_items[0].file_path == "/media/Artist/Song 1 [youtube-vid1].mp4"
    assert analysis.missing_items[0].video_id == "vid2"


def test_playlist_cache(monkeypatch):
    from yt_manager.playlist import clear_playlist_cache

    clear_playlist_cache()
    call_count = 0

    mock_info = {
        "_type": "playlist",
        "id": "PL_CACHE_TEST",
        "title": "Cache Test Playlist",
        "entries": [
            {
                "id": "vid1",
                "title": "Song 1",
                "url": "https://www.youtube.com/watch?v=vid1",
            }
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
            nonlocal call_count
            call_count += 1
            return mock_info

    monkeypatch.setattr(yt_dlp, "YoutubeDL", MockYoutubeDL)

    url = "https://www.youtube.com/playlist?list=PL_CACHE_TEST"

    # 1st call: cache miss -> calls yt-dlp
    info1 = extract_playlist_info(url)
    assert info1 is not None
    assert call_count == 1

    # 2nd call: cache hit -> returns cached, call_count stays 1
    info2 = extract_playlist_info(url)
    assert info2 is not None
    assert info2.playlist_id == "PL_CACHE_TEST"
    assert call_count == 1

    # 3rd call with use_cache=False -> forces refresh, call_count becomes 2
    info3 = extract_playlist_info(url, use_cache=False)
    assert info3 is not None
    assert call_count == 2

    # Clear cache -> call_count becomes 3
    clear_playlist_cache()
    info4 = extract_playlist_info(url)
    assert info4 is not None
    assert call_count == 3


def test_playlist_cache_ttl_expiration(monkeypatch):
    import time
    from yt_manager.playlist import clear_playlist_cache, extract_playlist_info

    clear_playlist_cache()
    call_count = 0
    current_time = 1000.0

    monkeypatch.setattr(time, "time", lambda: current_time)

    mock_info = {
        "_type": "playlist",
        "id": "PL_TTL_TEST",
        "title": "TTL Test",
        "entries": [{"id": "v1", "title": "V1", "url": "https://youtube.com/watch?v=v1"}],
    }

    class MockYoutubeDL:
        def __init__(self, *args, **kwargs):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *args):
            pass

        def extract_info(self, url, download=False, process=False):
            nonlocal call_count
            call_count += 1
            return mock_info

    monkeypatch.setattr(yt_dlp, "YoutubeDL", MockYoutubeDL)
    url = "https://www.youtube.com/playlist?list=PL_TTL_TEST"

    # Initial call at t=1000
    extract_playlist_info(url)
    assert call_count == 1

    # Within 300s (e.g. t=1200) -> cache hit
    current_time = 1200.0
    extract_playlist_info(url)
    assert call_count == 1

    # Expired after 300s (e.g. t=1301) -> cache miss, re-extracts
    current_time = 1301.0
    extract_playlist_info(url)
    assert call_count == 2


def test_playlist_cache_isolation_and_db_update(tmp_path: Path, monkeypatch):
    from yt_manager.playlist import clear_playlist_cache, analyze_playlist

    clear_playlist_cache()
    db_path = tmp_path / "test_cache_db.db"
    db = Database(db_path)

    mock_info = {
        "_type": "playlist",
        "id": "PL_DB_TEST",
        "title": "DB Test",
        "entries": [
            {"id": "v1", "title": "Video 1", "url": "https://youtube.com/watch?v=v1"},
            {"id": "v2", "title": "Video 2", "url": "https://youtube.com/watch?v=v2"},
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
    url = "https://www.youtube.com/playlist?list=PL_DB_TEST"

    # Call 1: v1 and v2 are both missing
    res1 = analyze_playlist(url, db)
    assert res1.total_count == 2
    assert res1.found_count == 0
    assert res1.missing_count == 2

    # Now simulate downloading v1 into storage & updating DB
    db.sync_all([("youtube", "v1", "/media/Artist/Video 1 [youtube-v1].mp4")])

    # Call 2: Uses cached playlist info, but evaluates DB fresh!
    res2 = analyze_playlist(url, db)
    assert res2.total_count == 2
    assert res2.found_count == 1
    assert res2.missing_count == 1
    assert res2.found_items[0].video_id == "v1"
    assert res2.missing_items[0].video_id == "v2"

    # Verify that modifying returned objects does not corrupt subsequent calls
    res2.found_items[0].file_path = "CORRUPTED"
    res3 = analyze_playlist(url, db)
    assert res3.found_items[0].file_path == "/media/Artist/Video 1 [youtube-v1].mp4"


def test_playlist_cache_max_items_separation(monkeypatch):
    from yt_manager.playlist import clear_playlist_cache, extract_playlist_info

    clear_playlist_cache()
    call_count = 0

    mock_entries = [
        {"id": f"v{i}", "title": f"V{i}", "url": f"https://youtube.com/watch?v=v{i}"}
        for i in range(1, 10)
    ]

    class MockYoutubeDL:
        def __init__(self, opts, *args, **kwargs):
            self.opts = opts

        def __enter__(self):
            return self

        def __exit__(self, *args):
            pass

        def extract_info(self, url, download=False, process=False):
            nonlocal call_count
            call_count += 1
            return {
                "_type": "playlist",
                "id": "PL_MULTI_MAX",
                "title": "Multi Max",
                "entries": mock_entries,
            }

    monkeypatch.setattr(yt_dlp, "YoutubeDL", MockYoutubeDL)
    url = "https://www.youtube.com/playlist?list=PL_MULTI_MAX"

    # Call with max_items=2
    info_2 = extract_playlist_info(url, max_items=2)
    assert info_2.total_items == 2
    assert call_count == 1

    # Call with max_items=5 -> should NOT hit cache for max_items=2
    info_5 = extract_playlist_info(url, max_items=5)
    assert info_5.total_items == 5
    assert call_count == 2

    # Call with max_items=2 again -> should hit cache!
    info_2_again = extract_playlist_info(url, max_items=2)
    assert info_2_again.total_items == 2
    assert call_count == 2


def test_playlist_unavailable_detection(tmp_path: Path, monkeypatch):
    from yt_manager.playlist import clear_playlist_cache

    clear_playlist_cache()
    db = Database(tmp_path / "unavail.db")

    mock_entries = [
        {"id": "pub1", "title": "Public Video", "url": "https://youtube.com/watch?v=pub1", "availability": "public"},
        {"id": "priv1", "title": "Secret Video", "url": "https://youtube.com/watch?v=priv1", "availability": "private"},
        {"id": "priv2", "title": "[Private video]", "url": "https://youtube.com/watch?v=priv2"},
        {"id": "priv3", "title": "[비공개 동영상]", "url": "https://youtube.com/watch?v=priv3"},
        {"id": "del1", "title": "[Deleted video]", "url": "https://youtube.com/watch?v=del1"},
        {"id": "auth1", "title": "Needs Login", "url": "https://youtube.com/watch?v=auth1", "is_private": True},
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
                "id": "PL_UNAVAIL",
                "title": "Unavail Test",
                "entries": mock_entries,
            }

    monkeypatch.setattr(yt_dlp, "YoutubeDL", MockYoutubeDL)

    url = "https://www.youtube.com/playlist?list=PL_UNAVAIL"
    info = extract_playlist_info(url)
    assert info is not None
    assert info.total_items == 6

    # Verify downloadable flag per item
    items_by_id = {it.video_id: it for it in info.items}
    assert items_by_id["pub1"].downloadable is True
    assert items_by_id["priv1"].downloadable is False
    assert items_by_id["priv2"].downloadable is False
    assert items_by_id["priv3"].downloadable is False
    assert items_by_id["del1"].downloadable is False
    assert items_by_id["auth1"].downloadable is False

    # Analysis 1: None in DB
    analysis1 = analyze_playlist(url, db)
    assert analysis1 is not None
    assert analysis1.total_count == 6
    assert analysis1.found_count == 0
    assert analysis1.missing_count == 6
    assert analysis1.downloadable_count == 1  # Only pub1 is downloadable!

    # Analysis 2: priv1 was previously saved in DB!
    db.sync_all([("youtube", "priv1", "/media/Artist/Secret [youtube-priv1].mp4")])
    analysis2 = analyze_playlist(url, db)
    assert analysis2 is not None
    assert analysis2.total_count == 6
    assert analysis2.found_count == 1
    assert analysis2.missing_count == 5
    assert analysis2.downloadable_count == 1  # pub1 is still the only downloadable missing video


def test_playlist_deduplication(tmp_path: Path, monkeypatch):
    from yt_manager.playlist import clear_playlist_cache

    clear_playlist_cache()
    db = Database(tmp_path / "dedup.db")
    db.sync_all([("youtube", "vid1", "/media/Artist/Vid1 [youtube-vid1].mp4")])

    mock_entries = [
        {"id": "vid1", "title": "Video 1", "url": "https://youtube.com/watch?v=vid1"},
        {"id": "vid2", "title": "Video 2", "url": "https://youtube.com/watch?v=vid2"},
        {"id": "vid1", "title": "Video 1 (Duplicate)", "url": "https://youtube.com/watch?v=vid1"},
        {"id": "vid3", "title": "Video 3", "url": "https://youtube.com/watch?v=vid3"},
        {"id": "vid2", "title": "Video 2 (Duplicate)", "url": "https://youtube.com/watch?v=vid2"},
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
                "id": "PL_DEDUP",
                "title": "Dedup Test",
                "entries": mock_entries,
            }

    monkeypatch.setattr(yt_dlp, "YoutubeDL", MockYoutubeDL)

    url = "https://www.youtube.com/playlist?list=PL_DEDUP"
    info = extract_playlist_info(url)
    assert info is not None
    # 5 entries in raw, but only 3 unique
    assert info.total_items == 3
    assert [it.video_id for it in info.items] == ["vid1", "vid2", "vid3"]

    analysis = analyze_playlist(url, db)
    assert analysis is not None
    assert analysis.total_count == 3
    assert analysis.found_count == 1   # vid1
    assert analysis.missing_count == 2 # vid2, vid3
    assert analysis.downloadable_count == 2
    assert [it.video_id for it in analysis.missing_items] == ["vid2", "vid3"]



