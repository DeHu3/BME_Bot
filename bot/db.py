# bot/db.py
from __future__ import annotations

from typing import List, Tuple
import json
import asyncpg


class SubscriberDB:
    """
    Postgres helper using asyncpg. Tables:
      - subscriptions(topic TEXT, chat_id BIGINT, PRIMARY KEY(topic, chat_id))
      - burns(signature TEXT PK, ts TIMESTAMPTZ, amount DOUBLE PRECISION, price_usd DOUBLE PRECISION, usd DOUBLE PRECISION)
      - app_state(key TEXT PRIMARY KEY, value JSONB NOT NULL)
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
                CREATE TABLE IF NOT EXISTS burns(
                  signature TEXT PRIMARY KEY,
                  ts        TIMESTAMPTZ NOT NULL,
                  amount    DOUBLE PRECISION NOT NULL,
                  price_usd DOUBLE PRECISION,
                  usd       DOUBLE PRECISION
                );
            """)
            await con.execute("""
                CREATE TABLE IF NOT EXISTS app_state(
                  key   TEXT PRIMARY KEY,
                  value JSONB NOT NULL
                );
            """)

    # ---------- subscriptions ----------
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

    # ---------- app state ----------
    async def get_state(self, key: str) -> dict:
        pool = await self.pool()
        async with pool.acquire() as con:
            row = await con.fetchrow("SELECT value FROM app_state WHERE key=$1;", key)
        if not row:
            return {}
        val = row["value"]
        if isinstance(val, dict):
            return val
        try:
            return json.loads(val)
        except Exception:
            return {}

    async def save_state(self, key: str, value: dict) -> None:
        payload = json.dumps(value, separators=(",", ":"), ensure_ascii=False)
        pool = await self.pool()
        async with pool.acquire() as con:
            await con.execute(
                """
                INSERT INTO app_state(key, value)
                VALUES($1, $2::jsonb)
                ON CONFLICT (key) DO UPDATE SET value=EXCLUDED.value;
                """,
                key, payload
            )

    # ---------- burns ----------
    async def record_burn(self, signature: str, ts: int, amount: float, price_usd: float | None) -> bool:
        """
        Insert and return True if a new row was inserted; False if it already existed.
        This lets cron/webhook avoid duplicate user messages while totals remain idempotent.
        """
        usd = (price_usd or 0.0) * amount
        pool = await self.pool()
        async with pool.acquire() as con:
            status = await con.execute(
                """
                INSERT INTO burns(signature, ts, amount, price_usd, usd)
                VALUES($1, to_timestamp($2), $3, $4, $5)
                ON CONFLICT (signature) DO NOTHING;
                """,
                signature, ts, amount, price_usd, usd
            )
        # asyncpg returns "INSERT 0 1" if inserted, "INSERT 0 0" if not
        try:
            return status.split()[-1] == "1"
        except Exception:
            return False

    async def _sums_since(self, seconds: int) -> Tuple[float, float]:
        pool = await self.pool()
        async with pool.acquire() as con:
            row = await con.fetchrow(
                """
                SELECT COALESCE(SUM(amount),0) AS a, COALESCE(SUM(usd),0) AS u
                FROM burns
                WHERE ts >= NOW() - make_interval(secs => $1);
                """,
                seconds
            )
        return float(row["a"]), float(row["u"])

    async def sums_24_7_30(self) -> Tuple[Tuple[float, float], Tuple[float, float], Tuple[float, float]]:
        s24 = await self._sums_since(24 * 3600)
        s7  = await self._sums_since(7 * 24 * 3600)
        s30 = await self._sums_since(30 * 24 * 3600)
        return s24, s7, s30
