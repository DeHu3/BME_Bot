# bot/db.py
from __future__ import annotations

import asyncpg
from typing import Any, Optional, Sequence


class SubscriberDB:
    """
    Very small helper around asyncpg.
    Tables:
      - subscribers(chat_id BIGINT PRIMARY KEY, tags TEXT[] NOT NULL DEFAULT '{}'::TEXT[])
      - kv_state(k TEXT PRIMARY KEY, v JSONB NOT NULL)
    """

    def __init__(self, dsn: str) -> None:
        self._dsn = dsn
        self._pool: Optional[asyncpg.Pool] = None

    async def _pool(self) -> asyncpg.Pool:
        if self._pool is None:
            self._pool = await asyncpg.create_pool(self._dsn, min_size=1, max_size=5)
        return self._pool

    async def ensure_schema(self) -> None:
        pool = await self._pool()
        async with pool.acquire() as con:
            await con.execute(
                """
                CREATE TABLE IF NOT EXISTS subscribers (
                    chat_id BIGINT PRIMARY KEY,
                    tags TEXT[] NOT NULL DEFAULT '{}'::TEXT[]
                );

                CREATE TABLE IF NOT EXISTS kv_state (
                    k TEXT PRIMARY KEY,
                    v JSONB NOT NULL
                );
                """
            )

    # ---------- subscriptions ----------

    async def add_sub(self, chat_id: int, tag: str) -> None:
        pool = await self._pool()
        async with pool.acquire() as con:
            await con.execute(
                """
                INSERT INTO subscribers (chat_id, tags)
                VALUES ($1, ARRAY[$2]::TEXT[])
                ON CONFLICT (chat_id) DO UPDATE
                SET tags = (
                    SELECT ARRAY(SELECT DISTINCT e
                                 FROM unnest(subscribers.tags || EXCLUDED.tags) AS e)
                );
                """,
                chat_id,
                tag,
            )

    async def del_sub(self, chat_id: int, tag: str) -> None:
        pool = await self._pool()
        async with pool.acquire() as con:
            await con.execute(
                """
                UPDATE subscribers
                SET tags = ARRAY(
                    SELECT e FROM unnest(tags) AS e WHERE e <> $2
                )
                WHERE chat_id = $1;
                """,
                chat_id,
                tag,
            )

    async def get_subs(self, tag: str) -> list[int]:
        pool = await self._pool()
        async with pool.acquire() as con:
            rows: Sequence[asyncpg.Record] = await con.fetch(
                "SELECT chat_id FROM subscribers WHERE $1 = ANY(tags);",
                tag,
            )
        return [r["chat_id"] for r in rows]

    # ---------- state cursor ----------

    async def get_state(self, key: str) -> dict[str, Any]:
        pool = await self._pool()
        async with pool.acquire() as con:
            row = await con.fetchrow("SELECT v FROM kv_state WHERE k = $1;", key)
        return dict(row["v"]) if row else {}

    async def save_state(self, key: str, value: dict[str, Any]) -> None:
        pool = await self._pool()
        async with pool.acquire() as con:
            await con.execute(
                """
                INSERT INTO kv_state(k, v)
                VALUES ($1, $2::jsonb)
                ON CONFLICT (k) DO UPDATE SET v = EXCLUDED.v;
                """,
                key,
                value,
            )
