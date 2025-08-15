from __future__ import annotations

import asyncio
import random
from typing import List, Dict, Any, Tuple, Optional
import httpx


# ------------------------
# Price (historical; falls back to current price)
# ------------------------
async def _get_price_usd(coingecko_id: str = "render-token") -> float:
    """
    Fallback: current USD price for RENDER/RNDR.
    """
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


async def usd_price_at(ts_unix: int, coingecko_id: str = "render-token") -> float:
    """
    Historical USD price for the given unix timestamp using CoinGecko's
    market_chart/range endpoint. If the range returns nothing, fall back to
    current price via _get_price_usd.
    """
    # Use a narrow window around the tx time (±8 minutes)
    start = max(0, ts_unix - 8 * 60)
    end = ts_unix + 8 * 60
    url = f"https://api.coingecko.com/api/v3/coins/{coingecko_id}/market_chart/range"
    params = {"vs_currency": "usd", "from": str(start), "to": str(end)}
    try:
        async with httpx.AsyncClient(timeout=12) as client:
            r = await client.get(url, params=params)
            r.raise_for_status()
            data = r.json() or {}
            prices = data.get("prices") or []  # list of [ms, price]
            if prices:
                target_ms = ts_unix * 1000
                best = min(prices, key=lambda p: abs(int(p[0]) - target_ms))
                return float(best[1])
    except Exception:
        pass
    # Fallback to spot if no datapoint was returned or request failed
    return await _get_price_usd(coingecko_id)


# ------------------------
# HTTP helpers with backoff
# ------------------------
async def _get_json_with_backoff(
    url: str,
    params: Dict[str, Any],
    headers: Optional[Dict[str, str]] = None,
    max_attempts: int = 6,
    base_delay: float = 0.5,
) -> List[Dict[str, Any]]:
    """
    GET JSON with exponential backoff for 429 (Helius rate limits).
    If 401/403, raise immediately (bad/missing API key).
    Returns a list (Helius returns a list of txs).
    """
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
    """
    Try to extract decimal amount from various Helius token transfer shapes.
    """
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
    We check multiple possible field names across Helius versions.
    NOTE: Because an SPL token account is tied to a single mint, we do NOT need
    to check symbol/mint if we trust the ATA.
    """
    burn_vault = burn_vault.strip()
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


# ------------------------
# Public API used by webhook_app.run_burn_once
# ------------------------
async def get_new_burns(cfg, state: dict) -> List[Dict[str, Any]]:
    """
    Return new *deposit* events into the burn vault ATA (most frequent signal).
    Cursor: state['last_sig'] (latest processed tx signature).
    Each event is one per transaction (we *sum* all qualifying transfers within the tx)
    so DB dedup by signature never loses value.

    Returned event shape:
      {'signature': str, 'ts': int, 'amount': float, 'price_usd': Optional[float]}
    """
    api_key = (getattr(cfg, "HELIUS_API_KEY", "") or "").strip()
    if not api_key:
        return []

    burn_vault = (getattr(cfg, "BURN_VAULT_ADDRESS", "") or getattr(cfg, "RENDER_BURN_ADDRESS", "") or "").strip()
    if not burn_vault:
        return []

    base = f"https://api.helius.xyz/v0/addresses/{burn_vault}/transactions"
    common_params = {"api-key": api_key, "limit": 100}

    last_sig: Optional[str] = state.get("last_sig") or None
    before: Optional[str] = None
    pages = 0
    max_pages = 5
    found_last = False

    events_by_sig: Dict[str, Dict[str, Any]] = {}
    newest_seen_sig: Optional[str] = None

    while pages < max_pages:
        params = dict(common_params)
        if before:
            params["before"] = before

        txs: List[Dict[str, Any]] = await _get_json_with_backoff(base, params=params)

        if not txs:
            break

        for tx in txs:
            sig = tx.get("signature")
            if not isinstance(sig, str):
                continue

            if newest_seen_sig is None:
                newest_seen_sig = sig

            if last_sig and sig == last_sig:
                found_last = True
                break

            ts = int(tx.get("timestamp") or tx.get("blockTime") or 0)

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
                # 🔹 price at the transaction time (fallbacks internally if needed)
                cg_id = getattr(cfg, "COINGECKO_ID", "render-token")
                price = await usd_price_at(ts, cg_id)

                events_by_sig[sig] = {
                    "signature": sig,
                    "ts": ts,
                    "amount": total_amount,
                    "price_usd": price if price > 0 else None,
                }

        if found_last:
            break

        before = txs[-1].get("signature") or before
        pages += 1

    new_events = list(events_by_sig.values())
    new_events.sort(key=lambda e: e["ts"])

    # Advance cursor ONLY if we actually found qualifying events
    if new_events and newest_seen_sig:
        state["last_sig"] = newest_seen_sig

    return new_events


# ------------------------
# Telegram message formatter (used by webhook_app.py)
# ------------------------
def format_burn(
    ev: Dict[str, Any],
    totals: Tuple[Tuple[float, float], Tuple[float, float], Tuple[float, float]],
) -> str:
    """
    totals: ((a24,u24),(a7,u7),(a30,u30))
    """
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
        "",  # blank line
        f"📊 24 hours: {fmt_pair(s24)}",
        f"📊 7 days: {fmt_pair(s7)}",
        f"📊 30 days: {fmt_pair(s30)}",
        "",
        f'🔗 View transaction on <a href="https://solscan.io/tx/{sig}">Solscan</a>.',
    ]
    return "\n".join(lines)
