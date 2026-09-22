import pytest
from yt_manager.extractor import extract_from_filename, extract_from_url


def test_extract_from_filename_explicit_extractor():
    # YouTube bracket with dash
    res1 = extract_from_filename("아이유 직캠 [youtube-dQw4w9WgXcQ].mp4")
    assert res1 == ("youtube", "dQw4w9WgXcQ")

    # TikTok bracket with space
    res2 = extract_from_filename("댄스 챌린지 [tiktok 7123456789012345678].webm")
    assert res2 == ("tiktok", "7123456789012345678")

    # Twitter bracket with dash
    res3 = extract_from_filename("트윗 영상 [twitter-1598765432109876543].mkv")
    assert res3 == ("twitter", "1598765432109876543")

    # YouTube with underscore/hyphen in ID
    res4 = extract_from_filename("전소미 DUMB DUMB [youtube-X_JFHg2T30o].webm")
    assert res4 == ("youtube", "X_JFHg2T30o")

    # Instagram bracket with underscore
    res5 = extract_from_filename("인스타 릴스 [instagram_CW123abcXYZ].mp4")
    assert res5 == ("instagram", "CW123abcXYZ")

    # Xiaohongshu / Rednote
    res6 = extract_from_filename("Rockstar Kaohsiung [xiaohongshu-68f6240d000000000700e5ad].mp4")
    assert res6 == ("xiaohongshu", "68f6240d000000000700e5ad")


def test_extract_from_filename_non_prefixed():
    # Without explicit extractor prefix, all single bracket IDs must return None
    assert extract_from_filename("뮤직비디오 [dQw4w9WgXcQ].mp4") is None
    assert extract_from_filename("뮤직비디오-dQw4w9WgXcQ.mp4") is None
    assert extract_from_filename("영상 [dp-0kWDkTj4].mp4") is None
    assert extract_from_filename("영상 [dm_DIzMTbsQ].mp4") is None
    assert extract_from_filename("[4K] 전소미 직캠 [X_JFHg2T30o].webm") is None
    assert extract_from_filename("무제_문서.mp4") is None
    assert extract_from_filename("family_photo.jpg") is None


def test_extract_from_url():
    # YouTube watch
    assert extract_from_url("https://www.youtube.com/watch?v=dQw4w9WgXcQ") == (
        "youtube",
        "dQw4w9WgXcQ",
    )

    # YouTube youtu.be
    assert extract_from_url("https://youtu.be/dQw4w9WgXcQ?t=43") == (
        "youtube",
        "dQw4w9WgXcQ",
    )

    # YouTube shorts
    assert extract_from_url("https://www.youtube.com/shorts/dQw4w9WgXcQ") == (
        "youtube",
        "dQw4w9WgXcQ",
    )

    # Twitter / X
    assert extract_from_url("https://x.com/user/status/1598765432109876543") == (
        "twitter",
        "1598765432109876543",
    )
    assert extract_from_url("https://twitter.com/user/status/1598765432109876543") == (
        "twitter",
        "1598765432109876543",
    )

    # TikTok
    assert extract_from_url(
        "https://www.tiktok.com/@creator/video/7123456789012345678"
    ) == ("tiktok", "7123456789012345678")

    # Instagram
    assert extract_from_url("https://www.instagram.com/reel/CW123abcXYZ/") == (
        "instagram",
        "CW123abcXYZ",
    )

    # Xiaohongshu / Rednote
    assert extract_from_url(
        "https://www.rednote.com/discovery/item/6aabdd60000000000d027fde?xsec_token=ABgZCUEncbM64txD3kxPPMmHTIwIy4l__t1POmrsqdhcI=&xsec_source="
    ) == ("xiaohongshu", "6aabdd60000000000d027fde")

    assert extract_from_url(
        "https://www.rednote.com/user/profile/631db090000000002302781f/6aabdd60000000000d027fde?xsec_token=ABVL2IsUYg9sjW9rU4TkodDGxdqXRgfma3WVh7ARSe-kg=&xsec_source=pc_user"
    ) == ("xiaohongshu", "6aabdd60000000000d027fde")

    assert extract_from_url(
        "https://www.xiaohongshu.com/explore/6aabdd60000000000d027fde"
    ) == ("xiaohongshu", "6aabdd60000000000d027fde")
    assert extract_from_url("https://www.instagram.com/p/CW123abcXYZ/") == (
        "instagram",
        "CW123abcXYZ",
    )

    # Invalid URL
    assert extract_from_url("https://google.com/search?q=test", use_ytdlp_fallback=False) is None


def test_extract_from_url_filters_out_playlists(monkeypatch):
    import yt_dlp

    class MockYoutubeDL:
        def __init__(self, *args, **kwargs):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *args):
            pass

        def extract_info(self, url, download=False, process=False):
            if "playlist" in url:
                return {"_type": "playlist", "id": "PL123", "extractor": "youtube:tab"}
            if "feed" in url:
                return {"_type": "playlist", "id": "history", "extractor": "youtube:tab"}
            return None

    monkeypatch.setattr(yt_dlp, "YoutubeDL", MockYoutubeDL)

    assert extract_from_url("https://www.youtube.com/playlist?list=PL123") is None
    assert extract_from_url("https://www.youtube.com/feed/history") is None
