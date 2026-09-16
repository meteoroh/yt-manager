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

    # X normalized to twitter
    res4 = extract_from_filename("X 영상 [x-1598765432109876543].mp4")
    assert res4 == ("twitter", "1598765432109876543")

    # Instagram bracket with underscore
    res5 = extract_from_filename("인스타 릴스 [instagram_CW123abcXYZ].mp4")
    assert res5 == ("instagram", "CW123abcXYZ")


def test_extract_from_filename_fallback():
    # 11-char YouTube ID single bracket
    res1 = extract_from_filename("뮤직비디오 [dQw4w9WgXcQ].mp4")
    assert res1 == ("youtube", "dQw4w9WgXcQ")

    # yt-dlp default trailing dash ID
    res2 = extract_from_filename("뮤직비디오-dQw4w9WgXcQ.mp4")
    assert res2 == ("youtube", "dQw4w9WgXcQ")

    # Non-matching normal filenames
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
    assert extract_from_url("https://www.instagram.com/p/CW123abcXYZ/") == (
        "instagram",
        "CW123abcXYZ",
    )

    # Invalid URL
    assert extract_from_url("https://google.com/search?q=test", use_ytdlp_fallback=False) is None
