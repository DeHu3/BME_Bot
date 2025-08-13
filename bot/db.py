# bot/db.py
from __future__ import annotations

from typing import Optional, List, Dict
import asyncpg

class SubscriberDB:
    """
    Postgres helper using asyncpg. Two tables:
      - subscriptions(topic TEXT, chat_id BIGINT, PK(topic, chat_id))
      - kv_state(k TEXT PRIMARY KEY, v JSONB NOT NULL)
    """
    _pools: dict[str, asyncpg.Pool] = {}

    def __init__(self, dsn: str):
        self._dsn = dsn

    async def pool(self) -> asyncpg.Pool:
        pool = self._pools.get(self._dsn)
        if pool is None:
            pool = await asyncpg.create_pool(self._dsn, min_size=1, max_size=5)
            self._pools[self._dsn] = pool
        return pool

    async def ensure_schema(self) -> None:
        pool = await self.pool()
        async with pool.acquire() as con:
            await con.execute("""
                CREATE TABLE IF NOT EXISTS subscriptions(
                    topic   TEXT   NOT NULL,
                    chat_id BIGINT NOT NULL,
                    PRIMARY KEY (topic, chat_id)
                );
            """)
            await con.execute("""
                CREATE TABLE IF NOT EXISTS kv_state(
                    k TEXT PRIMARY KEY,
                    v JSONB NOT NULL
                );
            """)

    # subscriptions
    async def add_sub(self, topic: str, chat_id: int) -> None:
        pool = await self.pool()
        async with pool.acquire() as con:
            await con.execute(
                "INSERT INTO subscriptions(topic, chat_id) VALUES($1,$2) ON CONFLICT DO NOTHING;",
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
            rows = await con.fetch("SELECT chat_id FROM subscriptions WHERE topic=$1;", topic)
        return [r["chat_id"] for r in rows]

    # state (cursor)
    async def get_state(self, key: str) -> Dict:
        pool = await self.pool()
        async with pool.acquire() as con:
            row = await con.fetchrow("SELECT v FROM kv_state WHERE k=$1;", key)
        return {} if row is None else row["v"]

    async def save_state(self, key: str, value: Dict) -> None:
        pool = await self.pool()
        async with pool.acquire() as con:
            await con.execute(
                "INSERT INTO kv_state(k, v) VALUES($1, $2::jsonb) "
                "ON CONFLICT (k) DO UPDATE SET v=EXCLUDED.v;",
                key, value
            )
