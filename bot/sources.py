# bot/sources.py
from __future__ import annotations

import time
from typing import Any, Dict, List, Optional, Tuple

import httpx

from bot.db import SubscriberDB


# ---------- Formatting helpers ----------

def _fmt_amount(n: float) -> str:
    # 1,234.56
    return f"{n:,.2f}"

def _fmt_usd(n: float) -> str:
    # $1,234.56
    return f"${n:,.2f}"

def format_burn(ev: Dict[str, Any]) -> str:
    """
    Build the exact message you asked for, using only `ev` fields.
    `ev` is expected to contain:
      amount (float), usd (float), signature (str),
      s24, s7, s30 – each is a tuple (amount_sum, usd_sum)
    """
    amt = float(ev.get("amount", 0.0))
    usd = float(ev.get("usd", 0.0))
    sig = ev.get("signature", "")

    s24a, s24u = ev.get("s24", (0.0, 0.0))
    s7a,  s7u  = ev.get("s7",  (0.0, 0.0))
    s30a, s30u = ev.get("s30", (0.0, 0.0))

    solscan = f"https://solscan.io/tx/{sig}" if sig else ""

    lines = [
        f"🔥 {_fmt_amount(amt)} RENDER ({_fmt_usd(usd)}) · View on Solscan: {solscan}",
        f"📊 24 hours: {_fmt_amount(s24a)} RENDER ({_fmt_usd(s24u)})",
        f"📊 7 days:   {_fmt_amount(s7a)} RENDER ({_fmt_usd(s7u)})",
        f"📊 30 days:  {_fmt_amount(s30a)} RENDER ({_fmt_usd(s30u)})",
    ]
    return "\n".join(lines)


# ---------- External APIs ----------

async def _coingecko_price_at(ts_unix: int,
                              client: httpx.AsyncClient,
                              coingecko_id: str = "render-token") -> Optional[float]:
    """
    Price of RNDR (USD) *at* the approximate time of the burn.
    Uses a ±15min window and picks the sample closest to ts_unix.
    """
    frm = max(0, ts_unix - 900)
    to  = ts_unix + 900
    url = (
        f"https://api.coingecko.com/api/v3/coins/{coingecko_id}"
        f"/market_chart/range?vs_currency=usd&from={frm}&to={to}"
    )
    try:
        r = await client.get(url, timeout=20)
        r.raise_for_status()
        data = r.json()
        prices = data.get("prices") or []
        if not prices:
            return None
        # prices is [[ms, price], ...]
        best_price = None
        best_diff = 10**18
        for ms, price in prices:
            diff = abs(ms // 1000 - ts_unix)
            if diff < best_diff:
                best_diff = diff
                best_price = float(price)
        return best_price
    except Exception:
        return None


async def _hel_adr_txs(
    api_key: str,
    address: str,
    limit: int = 50,
    before: Optional[str] = None,
) -> List[Dict[str, Any]]:
    """
    Helius Enhanced API: newest → oldest transactions involving `address`.
    https://api.helius.xyz/v0/addresses/{address}/transactions?api-key=...&limit=...&before=...
    """
    base = f"https://api.helius.xyz/v0/addresses/{address}/transactions?api-key={api_key}&limit={limit}"
    if before:
        base += f"&before={before}"
    async with httpx.AsyncClient() as client:
        try:
            r = await client.get(base, timeout=30)
            r.raise_for_status()
            data = r.json()
            return data if isinstance(data, list) else []
        except Exception:
            return []


# ---------- Public entry used by webhook_app.run_burn_once ----------

async def get_new_burns(cfg, state: Dict[str, Any]) -> List[Dict[str, Any]]:
    """
    Returns a list of *new* burn deposit events since the last cursor in `state`.
    Each returned dict is ready for `format_burn(ev)` and looks like:
      {
        "signature": str,
        "ts": int,
        "amount": float,        # RNDR amount
        "price_usd": float|None,
        "usd": float,           # amount * price_usd (0 if price missing)
        "s24": (amount_sum, usd_sum),
        "s7":  (amount_sum, usd_sum),
        "s30": (amount_sum, usd_sum),
      }

    Logic:
      • Query Helius Enhanced API for transactions to the burn address.
      • Filter tokenTransfers where mint == RENDER_MINT and toUserAccount == burn address.
      • For each, fetch price-at-time from CoinGecko, record in DB, compute running sums.
      • Mutate `state["cursor"]` so next run only sees newer txs.
    """
    api_key   = (getattr(cfg, "HELIUS_API_KEY", "") or "").strip()
    mint      = (getattr(cfg, "RENDER_MINT", "") or "").strip()
    burn_to   = (getattr(cfg, "RENDER_BURN_ADDRESS", "") or "").strip()
    cg_id     = (getattr(cfg, "COINGECKO_ID", "render-token") or "render-token").strip()
    database  = getattr(cfg, "DATABASE_URL")

    if not api_key or not mint or not burn_to:
        # Without these, we can't detect events – return empty so the bot stays healthy.
        return []

    # Pull newest set; if there’s a saved cursor, we’ll stop when we reach it.
    saved_cursor = (state or {}).get("cursor")
    txs = await _hel_adr_txs(api_key, burn_to, limit=50, before=None)

    # Build "new" list by walking until the saved cursor (newest-first array).
    new_txs: List[Dict[str, Any]] = []
    for tx in txs:
        sig = tx.get("signature")
        if saved_cursor and sig == saved_cursor:
            break
        new_txs.append(tx)

    # Process oldest → newest so alerts are in chronological order.
    new_txs.reverse()

    if txs:
        # Update cursor to newest we saw in this batch
        state["cursor"] = txs[0].get("signature")

    if not new_txs:
        return []

    db = SubscriberDB(database)
    out_events: List[Dict[str, Any]] = []

    async with httpx.AsyncClient() as client:
        for tx in new_txs:
            sig = tx.get("signature", "")
            ts  = int(tx.get("timestamp") or time.time())

            token_transfers = tx.get("tokenTransfers") or []
            for tt in token_transfers:
                try:
                    if tt.get("mint") != mint:
                        continue

                    # For deposits to the burn address (incinerator or your burn vault),
                    # Helius' "toUserAccount" should be that burn address
                    to_user = (tt.get("toUserAccount") or "").strip()
                    if to_user != burn_to:
                        continue

                    # amount can be in 'tokenAmount' (decimal string) or 'amount' (float-like)
                    raw_amt = tt.get("tokenAmount") or tt.get("amount") or "0"
                    amount  = float(raw_amt)
                except Exception:
                    continue

                price = await _coingecko_price_at(ts, client, cg_id)
                usd   = (price or 0.0) * amount

                # Persist this burn (ON CONFLICT DO NOTHING prevents dupes).
                await db.record_burn(sig, ts, amount, price)

                # Pull updated rolling sums AFTER recording the burn.
                s24, s7, s30 = await db.sums_24_7_30()

                out_events.append({
                    "signature": sig,
                    "ts": ts,
                    "amount": amount,
                    "price_usd": price,
                    "usd": usd,
                    "s24": s24,
                    "s7":  s7,
                    "s30": s30,
                })

    return out_events
