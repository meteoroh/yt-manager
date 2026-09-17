import sqlite3
from pathlib import Path
from typing import NamedTuple, Optional


class SyncResult(NamedTuple):
    total_count: int
    added_count: int
    deleted_count: int
    has_changes: bool


class Database:
    def __init__(self, db_path: Path | str):
        self.db_path = Path(db_path) if isinstance(db_path, str) else db_path
        if str(self.db_path) != ":memory:":
            self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._init_db()

    def _get_connection(self) -> sqlite3.Connection:
        conn = sqlite3.connect(str(self.db_path))
        conn.row_factory = sqlite3.Row
        return conn

    def _init_db(self) -> None:
        with self._get_connection() as conn:
            # Optimize for low disk I/O and prevent heavy rollback journals
            conn.execute("PRAGMA journal_mode = WAL")
            conn.execute("PRAGMA synchronous = NORMAL")
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS media (
                    extractor TEXT NOT NULL,
                    video_id  TEXT NOT NULL,
                    file_path TEXT NOT NULL,
                    PRIMARY KEY (extractor, video_id)
                )
                """
            )
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_media_file_path ON media (file_path)"
            )
            conn.commit()

    def find_by_id(self, extractor: str, video_id: str) -> Optional[str]:
        """Look up file_path by extractor and video_id."""
        with self._get_connection() as conn:
            cursor = conn.execute(
                "SELECT file_path FROM media WHERE extractor = ? AND video_id = ?",
                (extractor.lower(), video_id),
            )
            row = cursor.fetchone()
            return row["file_path"] if row else None

    def find_by_video_id_only(self, video_id: str) -> Optional[tuple[str, str]]:
        """Fallback lookup when extractor is uncertain."""
        with self._get_connection() as conn:
            cursor = conn.execute(
                "SELECT extractor, file_path FROM media WHERE video_id = ?",
                (video_id,),
            )
            row = cursor.fetchone()
            return (row["extractor"], row["file_path"]) if row else None

    def get_all_records_map(self) -> dict[tuple[str, str], str]:
        """Look up all (extractor, video_id) -> file_path mappings currently in DB."""
        with self._get_connection() as conn:
            cursor = conn.execute("SELECT extractor, video_id, file_path FROM media")
            return {
                (row["extractor"], row["video_id"]): row["file_path"]
                for row in cursor.fetchall()
            }

    def sync_all(self, records: list[tuple[str, str, str]]) -> SyncResult:
        """
        Synchronize the database with the current disk state atomically.
        records: list of (extractor, video_id, file_path)
        Returns: SyncResult(total_count, added_count, deleted_count, has_changes)
        """
        # Normalize extractor to lowercase and map by (extractor, video_id)
        new_map: dict[tuple[str, str], str] = {
            (ext.lower(), vid): path for ext, vid, path in records
        }

        # Check existing records in DB (pure read-only query)
        current_map = self.get_all_records_map()

        # Dirty check: if disk state matches DB state exactly, skip ALL disk writes!
        if current_map == new_map:
            return SyncResult(
                total_count=len(current_map),
                added_count=0,
                deleted_count=0,
                has_changes=False,
            )

        added_count = len(new_map.keys() - current_map.keys())
        deleted_count = len(current_map.keys() - new_map.keys())

        norm_records = [(ext, vid, path) for (ext, vid), path in new_map.items()]

        with self._get_connection() as conn:
            conn.execute(
                """
                CREATE TEMPORARY TABLE current_scan (
                    extractor TEXT NOT NULL,
                    video_id  TEXT NOT NULL,
                    file_path TEXT NOT NULL,
                    PRIMARY KEY (extractor, video_id)
                )
                """
            )

            conn.executemany(
                "INSERT OR REPLACE INTO current_scan (extractor, video_id, file_path) VALUES (?, ?, ?)",
                norm_records,
            )

            # Delete records from media that are not in current_scan
            conn.execute(
                """
                DELETE FROM media
                WHERE (extractor, video_id) NOT IN (
                    SELECT extractor, video_id FROM current_scan
                )
                """
            )

            # Insert or replace from current_scan to media
            conn.execute(
                """
                INSERT OR REPLACE INTO media (extractor, video_id, file_path)
                SELECT extractor, video_id, file_path FROM current_scan
                """
            )

            conn.execute("DROP TABLE current_scan")
            conn.commit()

            after_cursor = conn.execute("SELECT COUNT(*) FROM media")
            after_count = after_cursor.fetchone()[0]

            return SyncResult(
                total_count=after_count,
                added_count=added_count,
                deleted_count=deleted_count,
                has_changes=True,
            )

    def get_all_archives(self) -> list[tuple[str, str]]:
        """Return all (extractor, video_id) pairs sorted for archive.txt."""
        with self._get_connection() as conn:
            cursor = conn.execute(
                "SELECT extractor, video_id FROM media ORDER BY extractor, video_id"
            )
            return [(row["extractor"], row["video_id"]) for row in cursor.fetchall()]

    def count(self) -> int:
        with self._get_connection() as conn:
            cursor = conn.execute("SELECT COUNT(*) FROM media")
            return cursor.fetchone()[0]
