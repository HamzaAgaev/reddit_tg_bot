from pathlib import Path

import aiosqlite

_SCHEMA = """
CREATE TABLE IF NOT EXISTS seen_posts (
    post_id TEXT PRIMARY KEY,
    status TEXT NOT NULL,
    source TEXT NOT NULL,
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
);
"""


class Repo:
    def __init__(self, db_path: str):
        self._db_path = db_path
        self._conn: aiosqlite.Connection | None = None

    async def connect(self) -> None:
        Path(self._db_path).parent.mkdir(parents=True, exist_ok=True)
        self._conn = await aiosqlite.connect(self._db_path)
        await self._conn.execute(_SCHEMA)
        await self._conn.commit()

    async def close(self) -> None:
        if self._conn:
            await self._conn.close()

    async def is_seen(self, post_id: str) -> bool:
        """True if this post was already handled to a final outcome. ``error``
        doesn't count — it means our own processing failed (timeout, bug,
        transient network issue), so it should be retried on the next attempt
        rather than permanently skipped."""
        assert self._conn
        cursor = await self._conn.execute(
            "SELECT status FROM seen_posts WHERE post_id = ?", (post_id,)
        )
        row = await cursor.fetchone()
        if row is None:
            return False
        return row[0] != "error"

    async def mark(self, post_id: str, status: str, source: str) -> None:
        assert self._conn
        await self._conn.execute(
            "INSERT OR REPLACE INTO seen_posts (post_id, status, source) "
            "VALUES (?, ?, ?)",
            (post_id, status, source),
        )
        await self._conn.commit()

    async def stats(self) -> dict[str, int]:
        assert self._conn
        cursor = await self._conn.execute(
            "SELECT status, COUNT(*) FROM seen_posts GROUP BY status"
        )
        rows = await cursor.fetchall()
        return {status: count for status, count in rows}
