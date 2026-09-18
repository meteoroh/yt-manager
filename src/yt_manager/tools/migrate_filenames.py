#!/usr/bin/env python3
"""
One-off filename migration script (Dry-Run by default).

Safely batch-converts legacy yt-dlp filenames into the standard [%(extractor)s-%(id)s] format:
- YouTube:   "Title [id].mp4"                -> "Title [youtube-id].mp4"
- Twitter:   "Username - Title [id].mp4"     -> "Username - Title [twitter-id].mp4"
- Instagram: "Video by username [id].mp4"   -> "Video by username [instagram-id].mp4"
- TikTok:    "Title [id].mp4"                -> "Title [tiktok-id].mp4"
- Rednote:   "Title [id].mp4"                -> "Title [xiaohongshu-id].mp4"

Usage:
  # 1. Preview changes safely (Dry-run by default)
  uv run yt-migrate /path/to/media

  # 2. Apply rename on disk
  uv run yt-migrate /path/to/media --apply

  # 3. Use existing archive.txt for 100% accurate platform mapping
  uv run yt-migrate /path/to/media --archive-file /path/to/archive.txt
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
    "tiktok",
    "instagram",
    "facebook",
    "bilibili",
    "twitch",
    "soundcloud",
    "vimeo",
    "threads",
    "weibo",
    "xiaohongshu",
}

_EXTRACTOR_REGEX = "|".join(re.escape(e) for e in sorted(KNOWN_EXTRACTORS, key=len, reverse=True))
ALREADY_NORMALIZED_PATTERN = re.compile(
    rf"\[({_EXTRACTOR_REGEX})[- _]([a-zA-Z0-9_-]+)\]\.[a-zA-Z0-9]+$",
    re.IGNORECASE,
)


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
        print(f"[WARNING] Failed to load archive.txt ({e})")

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

    # 2. Instagram: starts with 'Video by ', 'Post by ', 'Photo by ', 'Reel by ', or 'Video <number>' (carousel)
    if (
        clean_prefix.lower().startswith(("video by ", "post by ", "photo by ", "reel by "))
        or re.match(r"^video\s+\d+", clean_prefix, re.IGNORECASE)
    ):
        return "instagram"

    # 3. Twitter: 18-20 digit numeric ID with ' - ' separator
    if cand_id.isdigit() and (17 <= len(cand_id) <= 20) and (" - " in prefix):
        return "twitter"

    # 4. TikTok: 18-20 digit numeric ID without ' - ' separator
    if cand_id.isdigit() and (17 <= len(cand_id) <= 20) and (" - " not in prefix):
        return "tiktok"

    # 5. Xiaohongshu / Rednote: 24-char hex ID
    if len(cand_id) == 24 and re.match(r"^[0-9a-f]{24}$", cand_id):
        return "xiaohongshu"

    # 6. YouTube: standard 11-char base64url ID (webm extension alone is not enough, must validate ID)
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
    if ALREADY_NORMALIZED_PATTERN.search(filename):
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
        print(f"[ERROR] Target directory does not exist: {target_dir}")
        return

    print("=" * 70)
    print("  yt-manager Filename Migration Tool")
    print(f"  Target Directory : {target_dir.resolve()}")
    print(f"  Execution Mode   : {'[APPLY] Renaming files on disk' if apply else '[DRY-RUN] Preview mode (No files modified)'}")
    if archive_lookup:
        print(f"  Archive Mappings : {len(archive_lookup)} entries loaded from archive.txt")
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
        print(f"\nNo files to migrate. (Checked {total_scanned} files; all already normalized or skipped)")
        return

    print(f"\n[Files to migrate: {len(plan)}]")
    print("-" * 70)

    for src_path, dst_path, platform, vid in plan:
        rel_dir = src_path.parent.relative_to(target_dir) if src_path.parent != target_dir else Path(".")
        print(f"Folder: {rel_dir}")
        print(f"  - Old: {src_path.name}")
        print(f"  + New: {dst_path.name}  [{platform.upper()}]")

        if apply:
            if dst_path.exists():
                print(f"  [SKIPPED] Destination file already exists: {dst_path.name}")
                error_count += 1
                continue
            try:
                src_path.rename(dst_path)
                migrated_count += 1
            except Exception as e:
                print(f"  [ERROR] Failed to rename: {e}")
                error_count += 1
        print()

    print("=" * 70)
    print("Summary:")
    print(f"  - Total files scanned  : {total_scanned}")
    print(f"  - Files to migrate     : {len(plan)}")
    if apply:
        print(f"  - Successfully renamed : {migrated_count}")
        if error_count:
            print(f"  - Failed / Skipped     : {error_count}")
        print("\n[COMPLETE] Filename migration finished. Run the backend scanner to resync the database.")
    else:
        print(f"\n[NOTICE] This was a Dry-Run preview. To apply the changes to disk, run:")
        print(f'  uv run yt-migrate "{target_dir}" --apply')
    print("=" * 70)


def main():
    parser = argparse.ArgumentParser(
        description="yt-manager filename migration tool ([id] -> [extractor-id])"
    )
    parser.add_argument(
        "target_dir",
        type=Path,
        help="Target directory path to inspect and migrate (e.g. /media or /downloads)",
    )
    parser.add_argument(
        "--apply",
        action="store_true",
        default=False,
        help="Apply renaming directly on disk (defaults to dry-run preview if omitted)",
    )
    parser.add_argument(
        "--archive-file",
        type=Path,
        default=None,
        help="Optional: Path to existing yt-dlp archive.txt for 100%% accurate platform mapping",
    )

    args = parser.parse_args()
    archive_lookup = load_archive_lookup(args.archive_file)
    scan_and_migrate(args.target_dir, args.apply, archive_lookup)


if __name__ == "__main__":
    main()
