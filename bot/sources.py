# bot/sources.py
from __future__ import annotations

import os
from typing import List, Dict, Any, Tuple, Optional
import httpx

HELIUS_BASE = "https://api.helius.xyz"


# -------- price helpers --------
async def _usd_price_at(ts: int, coingecko_id: str = "render-token") -> Optional[float]:
    """
    Historical USD price near timestamp ts (seconds since epoch).
    Uses CoinGecko market_chart/range with a small +/- 30m window
    and picks the closest datapoint.
    """
    start = max(0, ts - 1800)
    end = ts + 1800
    url = f"https://api.coingecko.com/api/v3/coins/{coingecko_id}/market_chart/range"
    params = {"vs_currency": "usd", "from": start, "to": end}
    try:
        async with httpx.AsyncClient(timeout=15) as client:
            r = await client.get(url, params=params)
            r.raise_for_status()
            data = r.json()
            prices = data.get("prices") or []
            if not prices:
                return None
            target_ms = ts * 1000
            closest = min(prices, key=lambda p: abs((p[0] or 0) - target_ms))
            return float(closest[1])
    except Exception:
        return None


async def _usd_price_now(coingecko_id: str = "render-token") -> Optional[float]:
    """Fallback: current USD price."""
    url = "https://api.coingecko.com/api/v3/simple/price"
    params = {"ids": coingecko_id, "vs_currencies": "usd"}
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            r = await client.get(url, params=params)
            r.raise_for_status()
            data = r.json()
            v = data.get(coingecko_id, {}).get("usd")
            return float(v) if v is not None else None
    except Exception:
        return None


# -------- parsing helpers --------
def _extract_amount(tr: Dict[str, Any]) -> float:
    """
    Convert a Helius token transfer amount to decimal RNDR.
    Handles a few different shapes Helius may return.
    """
    # Already-decimal fields commonly seen
    for key in ("tokenAmount", "amount", "uiAmount"):
        val = tr.get(key)
        if isinstance(val, (int, float, str)):
            try:
                return float(val)
            except Exception:
                pass

    # raw path: rawTokenAmount { tokenAmount: int-string, decimals: int }
    raw = tr.get("rawTokenAmount")
    if isinstance(raw, dict):
        tok = raw.get("tokenAmount")
        dec = raw.get("decimals")
        try:
            if tok is not None and dec is not None:
                return float(int(tok)) / (10 ** int(dec))
        except Exception:
            pass

    return 0.0


# -------- main fetcher --------
async def get_new_burns(cfg, state: dict, burn_addr: Optional[str] = None) -> List[Dict[str, Any]]:
    """
    Return new *deposits* of RNDR into the burn-vault ATA (your 7vq address).
    Cursor: state['last_sig'] (latest processed tx signature).
    Each event: {'signature','ts','amount','price_usd'}.

    We only accept transfers whose *destination token account* equals burn_addr.
    Because ATAs are mint-specific, this implicitly ensures it's RNDR.
    """
    api_key = (getattr(cfg, "HELIUS_API_KEY", "") or os.environ.get("HELIUS_API_KEY") or "").strip()
    addr = (
        (burn_addr or getattr(cfg, "BURN_VAULT_ADDRESS", "") or os.environ.get("BURN_VAULT_ADDRESS") or "")
        .strip()
    )

    if not api_key or not addr:
        # Missing credentials or address; nothing to do.
        return []

    url = f"{HELIUS_BASE}/v0/addresses/{addr}/transactions"
    headers = {"x-api-key": api_key}
    params = {"limit": 100}

    async with httpx.AsyncClient(timeout=20, headers=headers) as client:
        r = await client.get(url, params=params)
        r.raise_for_status()
        txs = r.json()

    last_sig = state.get("last_sig")
    events: List[Dict[str, Any]] = []

    for tx in txs:
        sig = tx.get("signature")
        if not sig:
            continue

        # Stop when we hit the last processed signature (transactions are newest-first).
        if last_sig and sig == last_sig:
            break

        ts = int(tx.get("timestamp") or tx.get("blockTime") or 0)

        # Helius places transfers either top-level or under events.tokenTransfers
        transfers = tx.get("tokenTransfers") or []
        if not transfers:
            ev = tx.get("events") or {}
            transfers = ev.get("tokenTransfers") or []

        for tr in transfers:
            # Destination token account — match the burn vault ATA exactly.
            to_acct = (tr.get("toUserAccount") or tr.get("toTokenAccount") or tr.get("to") or "").strip()
            if to_acct != addr:
                continue

            amount = _extract_amount(tr)
            if amount <= 0:
                continue

            # Price at (or near) the tx time, fallback to "now" if unavailable.
            price_usd = await _usd_price_at(ts)
            if price_usd is None:
                price_usd = await _usd_price_now()

            events.append(
                {
                    "signature": sig,
                    "ts": ts,
                    "amount": float(amount),
                    "price_usd": float(price_usd) if price_usd is not None else None,
                }
            )

    # Advance cursor to the newest signature we saw this call (top of list).
    if txs:
        newest = txs[0].get("signature")
        if newest:
            state["last_sig"] = newest

    return events


# -------- formatter --------
def format_burn(
    ev: Dict[str, Any],
    totals: Tuple[Tuple[float, float], Tuple[float, float], Tuple[float, float]],
) -> str:
    """
    totals: ((a24,u24), (a7,u7), (a30,u30))
    """
    amt = float(ev["amount"])
    p = float(ev.get("price_usd") or 0.0)
    usd = amt * p if p else 0.0

    def fmt_pair(t):
        a, u = t
        return f"{a:,.2f} RNDR (${u:,.2f})"

    s24, s7, s30 = totals
    lines = [
        "🔥 RNDR burn deposit detected",
        f"Just now: {amt:,.2f} RNDR" + (f" (${usd:,.2f})" if usd else ""),
        f"24h: {fmt_pair(s24)}",
        f"7d: {fmt_pair(s7)}",
        f"30d: {fmt_pair(s30)}",
        f"Tx: https://solscan.io/tx/{ev['signature']}",
    ]
    return "\n".join(lines)
