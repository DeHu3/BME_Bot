# bot/db.py
from __future__ import annotations

from typing import List, Tuple, Dict, Any
import asyncpg


class SubscriberDB:
    """
    Postgres helper using asyncpg.

    Tables:
      - subscriptions(topic TEXT, chat_id BIGINT, PRIMARY KEY(topic, chat_id))
      - app_state(key TEXT PRIMARY KEY, value JSONB NOT NULL)
      - burns(signature TEXT PRIMARY KEY, ts TIMESTAMPTZ, amount DOUBLE PRECISION,
              price_usd DOUBLE PRECISION, usd DOUBLE PRECISION)
    """

    # simple per-DSN pool cache to avoid making multiple pools
    _pools: Dict[str, asyncpg.Pool] = {}

    def __init__(self, dsn: str):
        self._dsn = dsn

    async def pool(self) -> asyncpg.Pool:
        pool = self._pools.get(self._dsn)
        if pool is None:
            pool = await asyncpg.create_pool(self._dsn, min_size=1, max_size=5)
            self._pools[self._dsn] = pool
        return pool

    # ---------------------------------------------------------------------
    # Schema
    # ---------------------------------------------------------------------
    async def ensure_schema(self) -> None:
        """Create/upgrade the minimal schema. Idempotent and safe to call repeatedly."""
        pool = await self.pool()
        async with pool.acquire() as con:
            # Subscriptions table
            await con.execute(
                """
                CREATE TABLE IF NOT EXISTS subscriptions(
                    topic   TEXT   NOT NULL,
                    chat_id BIGINT NOT NULL,
                    PRIMARY KEY (topic, chat_id)
                );
                """
            )

            # App state (JSONB) for cursors/settings
            await con.execute(
                """
                CREATE TABLE IF NOT EXISTS app_state(
                    key   TEXT PRIMARY KEY,
                    value JSONB NOT NULL
                );
                """
            )

            # Burns table for individual burn events + USD value at time
            await con.execute(
                """
                CREATE TABLE IF NOT EXISTS burns(
                    signature TEXT PRIMARY KEY,
                    ts        TIMESTAMPTZ NOT NULL,
                    amount    DOUBLE PRECISION NOT NULL,
                    price_usd DOUBLE PRECISION,
                    usd       DOUBLE PRECISION
                );
                """
            )

    # ---------------------------------------------------------------------
    # Subscriptions
    # ---------------------------------------------------------------------
    async def add_sub(self, topic: str, chat_id: int) -> None:
        pool = await self.pool()
        async with pool.acquire() as con:
            await con.execute(
                """
                INSERT INTO subscriptions(topic, chat_id)
                VALUES ($1, $2)
                ON CONFLICT DO NOTHING;
                """,
                topic,
                chat_id,
            )

    async def remove_sub(self, topic: str, chat_id: int) -> None:
        pool = await self.pool()
        async with pool.acquire() as con:
            await con.execute(
                "DELETE FROM subscriptions WHERE topic=$1 AND chat_id=$2;",
                topic,
                chat_id,
            )

    async def get_subs(self, topic: str) -> List[int]:
        pool = await self.pool()
        async with pool.acquire() as con:
            rows = await con.fetch(
                "SELECT chat_id FROM subscriptions WHERE topic=$1;",
                topic,
            )
        return [int(r["chat_id"]) for r in rows]

    # ---------------------------------------------------------------------
    # App state (JSONB)
    # ---------------------------------------------------------------------
    async def get_state(self, key: str) -> Dict[str, Any]:
        pool = await self.pool()
        async with pool.acquire() as con:
            row = await con.fetchrow(
                "SELECT value FROM app_state WHERE key=$1;",
                key,
            )
            return dict(row["value"]) if row else {}

    async def save_state(self, key: str, value: Dict[str, Any]) -> None:
        pool = await self.pool()
        async with pool.acquire() as con:
            await con.execute(
                """
                INSERT INTO app_state(key, value)
                VALUES ($1, $2::jsonb)
                ON CONFLICT (key) DO UPDATE SET value = EXCLUDED.value;
                """,
                key,
                value,
            )

    # ---------------------------------------------------------------------
    # Burns storage & aggregations
    # ---------------------------------------------------------------------
    async def record_burn(
        self,
        signature: str,
        ts_seconds: int,
        amount: float,
        price_usd: float | None,
    ) -> None:
        """Record a single burn deposit with price at time; ignore duplicates."""
        usd = (price_usd or 0.0) * amount
        pool = await self.pool()
        async with pool.acquire() as con:
            await con.execute(
                """
                INSERT INTO burns(signature, ts, amount, price_usd, usd)
                VALUES($1, to_timestamp($2), $3, $4, $5)
                ON CONFLICT (signature) DO NOTHING;
                """,
                signature,
                ts_seconds,
                amount,
                price_usd,
                usd,
            )

    async def sums_since(self, seconds: int) -> Tuple[float, float]:
        """Return (amount_sum, usd_sum) since now - seconds."""
        pool = await self.pool()
        async with pool.acquire() as con:
            row = await con.fetchrow(
                """
                SELECT
                  COALESCE(SUM(amount), 0) AS a,
                  COALESCE(SUM(usd),    0) AS u
                FROM burns
                WHERE ts >= NOW() - make_interval(secs => $1);
                """,
                seconds,
            )
            return float(row["a"]), float(row["u"])

    async def sums_24_7_30(
        self,
    ) -> Tuple[Tuple[float, float], Tuple[float, float], Tuple[float, float]]:
        s24 = await self.sums_since(24 * 3600)
        s7 = await self.sums_since(7 * 24 * 3600)
        s30 = await self.sums_since(30 * 24 * 3600)
        return s24, s7, s30
