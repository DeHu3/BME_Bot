# bot/sources.py
from __future__ import annotations

import os
import asyncio
import random
import time
from typing import List, Dict, Any, Tuple, Optional
import httpx


# ------------------------
# Price (current price; simple & robust)
# ------------------------
async def _get_price_usd(coingecko_id: str = "render-token") -> float:
    url = "https://api.coingecko.com/api/v3/simple/price"
    params = {"ids": coingecko_id, "vs_currencies": "usd"}
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            r = await client.get(url, params=params)
            r.raise_for_status()
            data = r.json()
            return float(data.get(coingecko_id, {}).get("usd", 0)) or 0.0
    except Exception:
        return 0.0


# ------------------------
# HTTP helpers with backoff (handles 429s)
# ------------------------
async def _get_json_with_backoff(
    url: str,
    params: Dict[str, Any],
    headers: Optional[Dict[str, str]] = None,
    max_attempts: int = 6,
    base_delay: float = 0.5,
) -> List[Dict[str, Any]]:
    last_exc = None
    for attempt in range(1, max_attempts + 1):
        try:
            async with httpx.AsyncClient(timeout=20) as client:
                resp = await client.get(url, params=params, headers=headers)
            if resp.status_code == 429:
                retry_after = resp.headers.get("Retry-After")
                if retry_after:
                    delay = float(retry_after)
                else:
                    delay = base_delay * (2 ** (attempt - 1)) + random.random() * 0.25
                await asyncio.sleep(min(delay, 8.0))
                continue

            if resp.status_code in (401, 403):
                resp.raise_for_status()  # fail fast on auth

            resp.raise_for_status()
            data = resp.json()
            if isinstance(data, list):
                return data
            return list(data or [])
        except Exception as e:
            last_exc = e
            await asyncio.sleep(min(base_delay * (2 ** (attempt - 1)), 8.0))
    assert last_exc is not None
    raise last_exc


# ------------------------
# Extract helpers (Helius shapes can vary)
# ------------------------
def _extract_amount(tr: Dict[str, Any]) -> float:
    # Try pre-decimal fields first
    for key in ("tokenAmount", "amountDecimal"):
        v = tr.get(key)
        if v is not None:
            try:
                return float(v)
            except Exception:
                pass

    raw = tr.get("amount")
    dec = tr.get("decimals")
    if isinstance(raw, int) and isinstance(dec, int) and dec > 0:
        try:
            return raw / (10 ** dec)
        except Exception:
            pass

    try:
        return float(raw or 0)
    except Exception:
        return 0.0


def _is_to_burn_vault(tr: Dict[str, Any], burn_vault: str) -> bool:
    """
    True if the transfer's destination token account equals the burn deposit ATA.
    We rely on the ATA being for RENDER, so we don't additionally check the mint/symbol.
    """
    burn_vault = (burn_vault or "").strip()
    if not burn_vault:
        return False

    for c in (
        tr.get("toUserAccount"),
        tr.get("toTokenAccount"),
        tr.get("to"),
        tr.get("destination"),
    ):
        if isinstance(c, str) and c.strip() == burn_vault:
            return True
    return False


def _lookback_secs(cfg) -> int:
    """
    Rolling window size (minutes) -> seconds. Optional, defaults to 45 minutes.
    Read from cfg.BURN_LOOKBACK_MINUTES if present, else env, else 45.
    """
    v = getattr(cfg, "BURN_LOOKBACK_MINUTES", None)
    if v is None:
        v = os.environ.get("BURN_LOOKBACK_MINUTES", "45")
    try:
        mins = int(v)
    except Exception:
        mins = 45
    mins = max(5, min(mins, 180))  # clamp between 5m and 3h
    return mins * 60


# ------------------------
# Public API used by webhook_app.run_burn_once
# ------------------------
async def get_new_burns(cfg, state: dict) -> List[Dict[str, Any]]:
    """
    Return *deposit* events into the burn vault ATA from a rolling time window.
    We DO NOT rely on a fragile last_sig boundary; instead we:
      - page recent address txs for the last N minutes (default 45m),
      - sum per-transaction RENDER amounts arriving at the burn vault ATA,
      - return one event per tx (signature).

    Returned event shape:
      {'signature': str, 'ts': int, 'amount': float, 'price_usd': Optional[float]}
    """
    api_key = (getattr(cfg, "HELIUS_API_KEY", "") or "").strip()
    if not api_key:
        return []

    burn_vault = (
        getattr(cfg, "BURN_VAULT_ADDRESS", "")
        or getattr(cfg, "RENDER_BURN_ADDRESS", "")
        or ""
    ).strip()
    if not burn_vault:
        return []

    base = f"https://api.helius.xyz/v0/addresses/{burn_vault}/transactions"
    common_params = {"api-key": api_key, "limit": 100}

    cutoff_ts = int(time.time()) - _lookback_secs(cfg)
    before: Optional[str] = None
    pages = 0
    max_pages = 8  # enough to cover the window even during bursts

    price_usd = await _get_price_usd(getattr(cfg, "COINGECKO_ID", "render-token"))

    events_by_sig: Dict[str, Dict[str, Any]] = {}

    while pages < max_pages:
        params = dict(common_params)
        if before:
            params["before"] = before

        txs: List[Dict[str, Any]] = await _get_json_with_backoff(base, params=params)
        if not txs:
            break

        oldest_ts_in_page = None

        for tx in txs:
            sig = tx.get("signature")
            if not isinstance(sig, str):
                continue

            ts = int(tx.get("timestamp") or tx.get("blockTime") or 0)
            if oldest_ts_in_page is None or (ts and ts < oldest_ts_in_page):
                oldest_ts_in_page = ts

            # Only consider txs inside the lookback window
            if ts and ts < cutoff_ts:
                continue

            transfers = tx.get("tokenTransfers") or []
            if not transfers:
                ev = tx.get("events") or {}
                transfers = ev.get("tokenTransfers") or []

            total_amount = 0.0
            for tr in transfers:
                if not _is_to_burn_vault(tr, burn_vault):
                    continue
                amt = _extract_amount(tr)
                if amt > 0:
                    total_amount += amt

            if total_amount > 0:
                events_by_sig[sig] = {
                    "signature": sig,
                    "ts": ts,
                    "amount": total_amount,
                    "price_usd": price_usd if price_usd > 0 else None,
                }

        # Stop if we've reached clearly outside the window.
        if oldest_ts_in_page is not None and oldest_ts_in_page < cutoff_ts:
            break

        before = txs[-1].get("signature") or before
        pages += 1

    # Sort ascending by time so alerts read naturally
    new_events = list(events_by_sig.values())
    new_events.sort(key=lambda e: e["ts"])
    # Optionally store a hint for visibility (not relied upon)
    state["last_checked_at"] = int(time.time())
    return new_events


# ------------------------
# Telegram message formatter (used by webhook_app.py)
# ------------------------
def format_burn(
    ev: Dict[str, Any],
    totals: Tuple[Tuple[float, float], Tuple[float, float], Tuple[float, float]],
) -> str:
    amt = float(ev["amount"])
    p = float(ev.get("price_usd") or 0.0)
    usd = amt * p if p else 0.0

    def fmt_pair(t: Tuple[float, float]) -> str:
        a, u = t
        return f"{a:,.2f} RENDER (${u:,.2f})"

    s24, s7, s30 = totals
    sig = ev["signature"]

    lines = [
        f"🔥  {amt:,.2f} RENDER (${usd:,.2f})",
        "",
        f"📊 24 hours: {fmt_pair(s24)}",
        f"📊 7 days: {fmt_pair(s7)}",
        f"📊 30 days: {fmt_pair(s30)}",
        "",
        f'🔗 View transaction on <a href="https://solscan.io/tx/{sig}">Solscan</a>.',
    ]
    return "\n".join(lines)
