# bot/db.py
from __future__ import annotations

from typing import Optional, List, Dict
import asyncpg


class SubscriberDB:
    def __init__(self, dsn: str):
        self._dsn = dsn
        self._pool: Optional[asyncpg.Pool] = None

    async def pool(self) -> asyncpg.Pool:
        """Lazily create/get an asyncpg pool."""
        if self._pool is None:
            self._pool = await asyncpg.create_pool(self._dsn, min_size=1, max_size=4)
        return self._pool

    async def ensure_schema(self) -> None:
        pool = await self.pool()
        async with pool.acquire() as con:
            # subscriber groups (topic + chat_id)
            await con.execute("""
            CREATE TABLE IF NOT EXISTS subscriptions(
                topic   TEXT   NOT NULL,
                chat_id BIGINT NOT NULL,
                PRIMARY KEY (topic, chat_id)
            );
            """)
            # simple KV state storage (cursor/counters, etc.)
            await con.execute("""
            CREATE TABLE IF NOT EXISTS kv_state(
                key   TEXT  PRIMARY KEY,
                value JSONB NOT NULL
            );
            """)

    # ----- subscriptions -----

    async def add_sub(self, topic: str, chat_id: int) -> None:
        pool = await self.pool()
        async with pool.acquire() as con:
            await con.execute(
                "INSERT INTO subscriptions(topic, chat_id) VALUES($1, $2) "
                "ON CONFLICT DO NOTHING;",
                topic, chat_id
            )

    async def remove_sub(self, topic: str, chat_id: int) -> None:
        pool = await self.pool()
        async with pool.acquire() as con:
            await con.execute(
                "DELETE FROM subscriptions WHERE topic=$1 AND chat_id=$2;",
                topic, chat_id
            )

    async def get_subs(self, topic: str) -> List[int]:
        pool = await self.pool()
        async with pool.acquire() as con:
            rows = await con.fetch(
                "SELECT chat_id FROM subscriptions WHERE topic=$1;",
                topic
            )
        return [r["chat_id"] for r in rows]

    # ----- state -----

    async def get_state(self, key: str) -> Dict:
        pool = await self.pool()
        async with pool.acquire() as con:
            row = await con.fetchrow("SELECT value FROM kv_state WHERE key=$1;", key)
        return {} if row is None else row["value"]

    async def save_state(self, key: str, value: Dict) -> None:
        pool = await self.pool()
        async with pool.acquire() as con:
            await con.execute(
                "INSERT INTO kv_state(key, value) VALUES($1, $2) "
                "ON CONFLICT (key) DO UPDATE SET value=EXCLUDED.value;",
                key, value
            )
