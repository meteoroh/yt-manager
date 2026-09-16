#!/usr/bin/env python3
"""
1회성 파일명 마이그레이션 스크립트 (Dry-Run 기본 지원)

기존 기본 yt-dlp 포맷의 파일명을 신규 표준 규격 [%(extractor)s-%(id)s] 형태로 안전하게 일괄 변환합니다.
- YouTube:   "제목 [id].mp4"                -> "제목 [youtube-id].mp4"
- Twitter:   "Username - 제목 [id].mp4"     -> "Username - 제목 [twitter-id].mp4"
- Instagram: "Video by username [id].mp4"   -> "Video by username [instagram-id].mp4"
- TikTok:    "제목 [id].mp4"                -> "제목 [tiktok-id].mp4"

사용법:
  # 1. 변경 예정 내역만 안전하게 미리보기 (기본 Dry-run)
  uv run python tools/migrate_filenames.py /path/to/media

  # 2. 실제 디스크 파일 이름 변경 적용
  uv run python tools/migrate_filenames.py /path/to/media --apply

  # 3. 기존 archive.txt를 대조하여 100% 정확하게 판별하기
  uv run python tools/migrate_filenames.py /path/to/media --archive-file /path/to/archive.txt
"""

import argparse
import os
import re
from pathlib import Path
from typing import Optional

MEDIA_EXTENSIONS = {
    ".mp4",
    ".mkv",
    ".webm",
    ".mov",
    ".avi",
    ".flv",
    ".ts",
    ".m4v",
}

IGNORED_DIR_NAMES = {
    "#recycle",
    "@eaDir",
    ".recycle",
    ".Trash",
    ".Trash-1000",
    "@SynoResource",
    "@SynoEAStream",
    ".git",
}

KNOWN_EXTRACTORS = {
    "youtube",
    "twitter",
    "x",
    "tiktok",
    "instagram",
    "facebook",
    "bilibili",
    "twitch",
    "soundcloud",
    "vimeo",
    "threads",
}


def load_archive_lookup(archive_path: Optional[Path]) -> dict[str, str]:
    """Load existing archive.txt into a {video_id: extractor} lookup dictionary."""
    lookup = {}
    if not archive_path or not archive_path.is_file():
        return lookup

    try:
        with open(archive_path, "r", encoding="utf-8", errors="ignore") as f:
            for line in f:
                parts = line.strip().split(maxsplit=1)
                if len(parts) == 2:
                    extractor, video_id = parts[0].lower(), parts[1]
                    if extractor == "x":
                        extractor = "twitter"
                    lookup[video_id] = extractor
    except Exception as e:
        print(f"[경고] archive.txt 로드 실패 ({e})")

    return lookup


def detect_platform(
    prefix: str,
    cand_id: str,
    ext: str,
    archive_lookup: dict[str, str],
) -> Optional[str]:
    """Detect platform based on archive lookup or filename fingerprinting."""
    # 1. Archive lookup has the highest priority (100% ground truth)
    if cand_id in archive_lookup:
        return archive_lookup[cand_id]

    norm_ext = ext.lower()
    clean_prefix = prefix.strip()

    # 2. Instagram: starts with 'Video by ', 'Post by ', 'Photo by ', 'Reel by '
    if clean_prefix.lower().startswith(
        ("video by ", "post by ", "photo by ", "reel by ")
    ):
        return "instagram"

    # 3. Twitter: 18-20 digit numeric ID with ' - ' separator
    if cand_id.isdigit() and (17 <= len(cand_id) <= 20) and (" - " in prefix):
        return "twitter"

    # 4. TikTok: 18-20 digit numeric ID without ' - ' separator
    if cand_id.isdigit() and (17 <= len(cand_id) <= 20) and (" - " not in prefix):
        return "tiktok"

    # 5. YouTube: webm extension OR standard 11-char base64url ID
    if norm_ext == ".webm":
        return "youtube"

    if len(cand_id) == 11 and re.match(r"^[a-zA-Z0-9_-]{11}$", cand_id):
        return "youtube"

    return None


def get_new_filename(
    filename: str,
    archive_lookup: dict[str, str],
) -> Optional[tuple[str, str, str]]:
    """
    Check if filename needs migration.
    Returns: (new_filename, platform, video_id) or None if skipped/already normalized.
    """
    # Check if already normalized [extractor-id]
    already_m = re.search(r"\[([a-zA-Z0-9_-]+?)[- _]([a-zA-Z0-9_-]+?)\]\.[a-zA-Z0-9]+$", filename)
    if already_m:
        cand_ext = already_m.group(1).lower()
        if cand_ext in KNOWN_EXTRACTORS:
            return None  # Already normalized

    # Match single bracket ID: prefix[id].ext
    single_m = re.search(r"^(.*)\[([a-zA-Z0-9_-]+)\](\.[a-zA-Z0-9]+)$", filename)
    if not single_m:
        return None

    prefix = single_m.group(1)
    cand_id = single_m.group(2)
    ext = single_m.group(3)

    if ext.lower() not in MEDIA_EXTENSIONS:
        return None

    platform = detect_platform(prefix, cand_id, ext, archive_lookup)
    if not platform:
        return None

    new_filename = f"{prefix}[{platform}-{cand_id}]{ext}"
    if new_filename == filename:
        return None

    return new_filename, platform, cand_id


def scan_and_migrate(
    target_dir: Path,
    apply: bool,
    archive_lookup: dict[str, str],
) -> None:
    if not target_dir.exists():
        print(f"[오류] 대상 디렉토리가 존재하지 않습니다: {target_dir}")
        return

    print("=" * 70)
    print("  yt-manager 파일명 마이그레이션 도구")
    print(f"  대상 디렉토리 : {target_dir.resolve()}")
    print(f"  동작 모드     : {'[APPLY] 실제 파일명 변경 실행' if apply else '[DRY-RUN] 미리보기 모드 (파일 변경 없음)'}")
    if archive_lookup:
        print(f"  archive.txt   : {len(archive_lookup)}개 ID 매핑 로드 완료")
    print("=" * 70)

    total_scanned = 0
    migrated_count = 0
    skipped_count = 0
    error_count = 0

    plan = []

    for root, dirs, files in os.walk(target_dir):
        # Exclude system/NAS dirs in place
        dirs[:] = [
            d for d in dirs
            if d not in IGNORED_DIR_NAMES
            and not (Path(root) / d / ".nomedia").exists()
            and not (Path(root) / d / ".ytignore").exists()
        ]

        for fname in files:
            suffix = Path(fname).suffix.lower()
            if suffix not in MEDIA_EXTENSIONS:
                continue

            total_scanned += 1
            result = get_new_filename(fname, archive_lookup)

            if result:
                new_fname, platform, vid = result
                src_path = Path(root) / fname
                dst_path = Path(root) / new_fname
                plan.append((src_path, dst_path, platform, vid))
            else:
                skipped_count += 1

    if not plan:
        print(f"\n변환 대상 파일이 없습니다. (총 {total_scanned}개 파일 확인, 모두 이미 규격화되었거나 제외됨)")
        return

    print(f"\n[변환 대상 파일: {len(plan)}개]")
    print("-" * 70)

    for src_path, dst_path, platform, vid in plan:
        rel_dir = src_path.parent.relative_to(target_dir) if src_path.parent != target_dir else Path(".")
        print(f"폴더: {rel_dir}")
        print(f"  - 기존: {src_path.name}")
        print(f"  + 변경: {dst_path.name}  [{platform.upper()}]")

        if apply:
            if dst_path.exists():
                print(f"  [건너뜀] 대상 파일명이 이미 존재합니다: {dst_path.name}")
                error_count += 1
                continue
            try:
                src_path.rename(dst_path)
                migrated_count += 1
            except Exception as e:
                print(f"  [오류] 이름 변경 실패: {e}")
                error_count += 1
        print()

    print("=" * 70)
    print("처리 결과 요약:")
    print(f"  - 총 검사 파일 수 : {total_scanned}개")
    print(f"  - 변환 대상       : {len(plan)}개")
    if apply:
        print(f"  - 성공 변경       : {migrated_count}개")
        if error_count:
            print(f"  - 실패/건너뜀     : {error_count}개")
        print("\n[완료] 파일명 일괄 변경이 완료되었습니다. 백엔드 스캐너를 돌려 DB를 동기화하세요.")
    else:
        print(f"\n[안내] 현재는 Dry-run(미리보기) 모드였습니다. 실제로 파일명을 변경하려면 다음 명령어를 실행하세요:")
        print(f'  uv run python tools/migrate_filenames.py "{target_dir}" --apply')
    print("=" * 70)


def main():
    parser = argparse.ArgumentParser(
        description="yt-manager 파일명 일괄 마이그레이션 도구 ([id] -> [extractor-id])"
    )
    parser.add_argument(
        "target_dir",
        type=Path,
        help="검사 및 변환할 디렉토리 경로 (예: /media 또는 /downloads)",
    )
    parser.add_argument(
        "--apply",
        action="store_true",
        default=False,
        help="실제 디스크 파일명 변경을 수행합니다. (지정하지 않으면 Dry-Run 모드로 미리보기만 실행)",
    )
    parser.add_argument(
        "--archive-file",
        type=Path,
        default=None,
        help="선택사항: 기존 yt-dlp archive.txt 경로 (ID 기반 100%% 정확한 플랫폼 매핑용)",
    )

    args = parser.parse_args()
    archive_lookup = load_archive_lookup(args.archive_file)
    scan_and_migrate(args.target_dir, args.apply, archive_lookup)


if __name__ == "__main__":
    main()
