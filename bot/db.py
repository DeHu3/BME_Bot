# bot/db.py
import os
import asyncpg
from typing import Iterable

class SubscriberDB:
    """Asynchronous DB helper for Render Postgres."""

    def __init__(self) -> None:
        # Read connection parameters from environment variables
        # Example: postgres://user:password@hostname:5432/dbname
        self.database_url = os.environ.get("DATABASE_URL")
        self._pool: asyncpg.Pool | None = None

    async def get_pool(self) -> asyncpg.Pool:
        """Initialize (if necessary) and return the connection pool."""
        if self._pool is None:
            self._pool = await asyncpg.create_pool(self.database_url, min_size=1, max_size=5)
        return self._pool

    async def ensure_schema(self) -> None:
        """Ensure that required tables exist."""
        pool = await self.get_pool()
        async with pool.acquire() as conn:
            await conn.execute("""
            CREATE TABLE IF NOT EXISTS subs (
                list_name TEXT PRIMARY KEY,
                subs BIGINT[] NOT NULL
            );
            CREATE TABLE IF NOT EXISTS state (
                key TEXT PRIMARY KEY,
                value JSONB
            );
            """)

    async def get_subs(self, list_name: str) -> set[int]:
        """Return the subscriber IDs for a given list."""
        pool = await self.get_pool()
        async with pool.acquire() as conn:
            row = await conn.fetchrow("SELECT subs FROM subs WHERE list_name=$1", list_name)
            return set(row["subs"]) if row else set()

    async def save_subs(self, list_name: str, subs: Iterable[int]) -> None:
        """Persist subscriber IDs for a given list."""
        pool = await self.get_pool()
        async with pool.acquire() as conn:
            await conn.execute("""
                INSERT INTO subs (list_name, subs)
                VALUES ($1, $2)
                ON CONFLICT (list_name)
                DO UPDATE SET subs = EXCLUDED.subs
            """, list_name, list(subs))

    async def get_state(self, key: str) -> dict:
        """Get stored state (cursor) for cron jobs."""
        pool = await self.get_pool()
        async with pool.acquire() as conn:
            row = await conn.fetchrow("SELECT value FROM state WHERE key=$1", key)
            return row["value"] if row else {}

    async def save_state(self, key: str, value: dict) -> None:
        """Persist state (cursor) between cron runs."""
        pool = await self.get_pool()
        async with pool.acquire() as conn:
            await conn.execute("""
                INSERT INTO state (key, value)
                VALUES ($1, $2)
                ON CONFLICT (key)
                DO UPDATE SET value = EXCLUDED.value
            """, key, value)
