# bot/db.py
import os
import asyncpg
from typing import Set, Optional

class SubscriberDB:
    def __init__(self):
        self._pool: Optional[asyncpg.pool.Pool] = None

    async def connect(self):
        if self._pool is None:
            # Use a single connection pool for performance; DB_URL must be provided via env
            self._pool = await asyncpg.create_pool(os.environ["DATABASE_URL"])

            # Create tables if they don’t exist
            async with self._pool.acquire() as conn:
                await conn.execute("""
                    CREATE TABLE IF NOT EXISTS subscriber_lists (
                        list_name TEXT PRIMARY KEY,
                        subs      BIGINT[] NOT NULL
                    );
                    CREATE TABLE IF NOT EXISTS bot_state (
                        key   TEXT PRIMARY KEY,
                        value JSONB
                    );
                """)

    async def close(self):
        if self._pool is not None:
            await self._pool.close()

    async def get_subs(self, list_name: str) -> Set[int]:
        await self.connect()
        async with self._pool.acquire() as conn:
            row = await conn.fetchrow(
                "SELECT subs FROM subscriber_lists WHERE list_name=$1", list_name
            )
            return set(row["subs"]) if row else set()

    async def save_subs(self, list_name: str, subs: Set[int]) -> None:
        await self.connect()
        async with self._pool.acquire() as conn:
            await conn.execute("""
                INSERT INTO subscriber_lists (list_name, subs)
                VALUES ($1, $2)
                ON CONFLICT (list_name)
                DO UPDATE SET subs = EXCLUDED.subs
            """, list_name, list(subs))

    async def get_state(self, key: str):
        """Retrieve JSON state for burn cursor or other state."""
        await self.connect()
        async with self._pool.acquire() as conn:
            row = await conn.fetchrow("SELECT value FROM bot_state WHERE key=$1", key)
            return row["value"] if row else {}

    async def save_state(self, key: str, value):
        """Persist JSON state."""
        await self.connect()
        async with self._pool.acquire() as conn:
            await conn.execute("""
                INSERT INTO bot_state (key, value)
                VALUES ($1, $2::jsonb)
                ON CONFLICT (key) DO UPDATE SET value = EXCLUDED.value
            """, key, value)
