# bot/sources.py
from __future__ import annotations

import asyncio
import math
import random
from typing import List, Dict, Any, Tuple, Optional
import httpx


# ------------------------
# Price (simple current-price; optional to upgrade to price-at-time)
# ------------------------
async def _get_price_usd(coingecko_id: str = "render-token") -> float:
    """
    Fetch current USD price for RNDR/RENDER. We use current price as an approximation
    to keep things simple and robust. If you want strict 'price at burn time',
    we can swap this later for a time-bucketed lookup.
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
                # Respect Retry-After if present; else exponential with jitter
                retry_after = resp.headers.get("Retry-After")
                if retry_after:
                    delay = float(retry_after)
                else:
                    delay = base_delay * (2 ** (attempt - 1)) + random.random() * 0.25
                await asyncio.sleep(min(delay, 8.0))
                continue

            # If API key missing/wrong, don't spin
            if resp.status_code in (401, 403):
                resp.raise_for_status()  # will raise HTTPStatusError

            resp.raise_for_status()
            data = resp.json()
            if isinstance(data, list):
                return data
            # If shape changes, normalize to list
            return list(data or [])
        except Exception as e:
            last_exc = e
            # Soft backoff on transient issues
            await asyncio.sleep(min(base_delay * (2 ** (attempt - 1)), 8.0))
    # Out of attempts
    assert last_exc is not None
    raise last_exc


# ------------------------
# Extract helpers (Helius shapes can vary)
# ------------------------
def _extract_amount(tr: Dict[str, Any]) -> float:
    """
    Try to extract decimal amount from various Helius token transfer shapes.
    """
    # Preferred already-decimal fields
    for key in ("tokenAmount", "amountDecimal"):
        v = tr.get(key)
        if v is not None:
            try:
                return float(v)
            except Exception:
                pass

    # Integer + decimals path
    raw = tr.get("amount")
    dec = tr.get("decimals")
    if isinstance(raw, int) and isinstance(dec, int) and dec > 0:
        try:
            return raw / (10 ** dec)
        except Exception:
            pass

    # Fallback
    try:
        return float(raw or 0)
    except Exception:
        return 0.0


def _matches_burn_vault(tr: Dict[str, Any], burn_vault: str) -> bool:
    """
    True if the transfer's destination token account equals the burn deposit ATA.
    We check multiple possible field names across Helius versions.
    """
    burn_vault = burn_vault.strip()
    if not burn_vault:
        return False

    # Most common fields
    candidates = [
        tr.get("toUserAccount"),
        tr.get("toTokenAccount"),
        tr.get("to"),  # sometimes generic
        tr.get("destination"),
    ]
    for c in candidates:
        if isinstance(c, str) and c.strip() == burn_vault:
            return True
    return False


def _matches_token(tr: Dict[str, Any], mint: Optional[str], symbol: str) -> bool:
    """
    Prefer exact mint match if provided; otherwise fall back to symbol (upper-cased).
    """
    if mint:
        return (tr.get("mint") or "").strip() == mint.strip()
    sym = (tr.get("symbol") or tr.get("tokenSymbol") or "").upper()
    return sym == (symbol or "RENDER").upper()


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

    render_mint = (getattr(cfg, "RENDER_MINT_ADDRESS", "") or "").strip()  # optional
    symbol = (getattr(cfg, "RNDR_SYMBOL", "RENDER") or "RENDER").upper()

    base = f"https://api.helius.xyz/v0/addresses/{burn_vault}/transactions"
    # Helius accepts the api-key as a query param on v0
    common_params = {"api-key": api_key, "limit": 100}

    last_sig: Optional[str] = state.get("last_sig") or None
    before: Optional[str] = None  # pagination cursor (older pages)
    pages = 0
    max_pages = 5  # safety; fetch up to 500 tx to find last_sig
    found_last = False

    # Price (approx) once per run
    price_usd = await _get_price_usd(getattr(cfg, "COINGECKO_ID", "render-token"))

    events_by_sig: Dict[str, Dict[str, Any]] = {}
    newest_seen_sig: Optional[str] = None

    while pages < max_pages:
        params = dict(common_params)
        if before:
            params["before"] = before

        txs: List[Dict[str, Any]] = await _get_json_with_backoff(base, params=params)

        if not txs:
            break

        for idx, tx in enumerate(txs):
            sig = tx.get("signature")
            if not isinstance(sig, str):
                continue

            # Mark the first-ever seen signature this run (the "newest" we processed)
            if newest_seen_sig is None:
                newest_seen_sig = sig

            # Stop if we reached the last processed signature
            if last_sig and sig == last_sig:
                found_last = True
                break

            ts = int(tx.get("timestamp") or tx.get("blockTime") or 0)

            # token transfers may be in different places depending on Helius version
            transfers = tx.get("tokenTransfers") or []
            if not transfers:
                ev = tx.get("events") or {}
                transfers = ev.get("tokenTransfers") or []

            # Sum all qualifying transfers in THIS transaction
            total_amount = 0.0
            for tr in transfers:
                if not _matches_burn_vault(tr, burn_vault):
                    continue
                if not _matches_token(tr, render_mint, symbol):
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

        if found_last:
            break

        # Prepare for next page (older)
        before = txs[-1].get("signature") or before
        pages += 1

    # Convert to list and order oldest -> newest for nicer reading/sending
    new_events = list(events_by_sig.values())
    new_events.sort(key=lambda e: e["ts"])

    # Advance cursor ONLY if we actually found qualifying events.
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
    lines = [
        "🔥 RENDER burn deposit detected",
        f"Just now: {amt:,.2f} RENDER" + (f" (${usd:,.2f})" if usd else ""),
        f"24h: {fmt_pair(s24)}",
        f"7d: {fmt_pair(s7)}",
        f"30d: {fmt_pair(s30)}",
        f"Tx: https://solscan.io/tx/{ev['signature']}",
    ]
    return "\n".join(lines)
