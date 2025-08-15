# bot/sources.py
from __future__ import annotations

import asyncio
import random
import time
from typing import List, Dict, Any, Tuple, Optional
import httpx


# ------------------------
# Price (simple current-price; optional to upgrade to price-at-time)
# ------------------------
async def _get_price_usd(coingecko_id: str = "render-token") -> float:
    """
    Fetch current USD price for RENDER/RNDR. We use current price as an approximation
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

    Cursors in `state` (mutated in-place):
      - state['last_sig']: latest processed transaction signature (string)
      - state['last_ts'] : unix timestamp (int) of the newest processed *event* we recorded

    This function implements a *time-overlap* to survive indexing delays:
      - We do NOT stop immediately at last_sig; we scan at least MIN_PAGES
      - We stop once we've covered OVERLAP seconds *before* last_ts (or reached MAX_PAGES)
      - We still dedupe by signature and report at most one event per transaction

    Returned event shape (one per tx):
      {'signature': str, 'ts': int, 'amount': float, 'price_usd': Optional[float]}
    """
    api_key = (getattr(cfg, "HELIUS_API_KEY", "") or "").strip()
    if not api_key:
        return []

    # Allow either var name; prefer BURN_VAULT_ADDRESS if present
    burn_vault = (
        (getattr(cfg, "BURN_VAULT_ADDRESS", "") or getattr(cfg, "RENDER_BURN_ADDRESS", "") or "")
        .strip()
    )
    if not burn_vault:
        return []

    # Tunables (optional env). Defaults chosen to be safe & light.
    overlap_sec = int(getattr(cfg, "BURN_SCAN_OVERLAP_SEC", 180) or 180)      # re-scan last 3 min
    min_pages   = int(getattr(cfg, "BURN_SCAN_MIN_PAGES", 2) or 2)            # scan at least 2 pages
    max_pages   = int(getattr(cfg, "BURN_SCAN_MAX_PAGES", 5) or 5)            # never scan more than 5

    last_sig: Optional[str] = state.get("last_sig") or None
    last_ts_val: int = int(state.get("last_ts") or 0)

    # For the very first run there may be no last_ts. We'll still page normally.
    cutoff_ts: Optional[int] = None
    if last_ts_val > 0 and overlap_sec > 0:
        cutoff_ts = max(0, last_ts_val - overlap_sec)

    base = f"https://api.helius.xyz/v0/addresses/{burn_vault}/transactions"
    common_params = {"api-key": api_key, "limit": 100}

    pages = 0
    before: Optional[str] = None
    found_last_sig = False
    newest_seen_sig: Optional[str] = None
    newest_event_ts: int = 0

    # Use current price once per run (approx. "at time" value)
    price_usd = await _get_price_usd(getattr(cfg, "COINGECKO_ID", "render-token"))

    events_by_sig: Dict[str, Dict[str, Any]] = {}

    while pages < max_pages:
        params = dict(common_params)
        if before:
            params["before"] = before

        txs: List[Dict[str, Any]] = await _get_json_with_backoff(base, params=params)
        if not txs:
            break

        # Track the oldest ts we saw on this page to decide early stop
        page_oldest_ts: Optional[int] = None

        for idx, tx in enumerate(txs):
            sig = tx.get("signature")
            if not isinstance(sig, str):
                continue

            if pages == 0 and idx == 0:
                # Remember the top-most signature we saw this run
                newest_seen_sig = sig

            ts = int(tx.get("timestamp") or tx.get("blockTime") or 0)
            if page_oldest_ts is None or (ts and ts < page_oldest_ts):
                page_oldest_ts = ts

            if last_sig and sig == last_sig:
                # Don't stop immediately; finish this page and then decide based on overlap/page budget
                found_last_sig = True
                continue

            # Helius can place transfers in different fields depending on version
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
                if ts > newest_event_ts:
                    newest_event_ts = ts

        pages += 1

        # Decide whether we've covered enough to stop
        # Stop if:
        #  - we saw last_sig somewhere already, AND
        #  - we have scanned at least min_pages, AND
        #  - either we have no cutoff (first run) OR this page's oldest ts is at/older than the cutoff
        if found_last_sig and pages >= min_pages:
            if cutoff_ts is None or (page_oldest_ts is not None and page_oldest_ts <= cutoff_ts):
                break

        # If this page returned fewer than limit, we're at the end
        if len(txs) < common_params["limit"]:
            break

        before = txs[-1].get("signature") or before

    # Build final list (ascending by time so older alerts go first)
    new_events = list(events_by_sig.values())
    new_events.sort(key=lambda e: e["ts"])

    # Update cursors:
    # - Always advance last_sig to newest seen this run (we compensate with overlap next time).
    # - Only update last_ts if we actually found any qualifying events (to avoid shrinking the overlap window unnecessarily).
    if newest_seen_sig:
        state["last_sig"] = newest_seen_sig
    if newest_event_ts > 0:
        state["last_ts"] = newest_event_ts

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
