# bot/sources.py
from __future__ import annotations

from typing import List, Dict, Any, Tuple
import os
import asyncio
import httpx

# ---- Price helpers ----------------------------------------------------------

async def _get_price_usd(coingecko_id: str = "render-token") -> float:
    """
    Fetch current USD price (approx for alert). If rate-limited/unavailable,
    return 0.0 and the single-event USD will be omitted.
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


# ---- Helpers for Helius payloads -------------------------------------------

def _extract_amount(transfer: Dict[str, Any]) -> float:
    """
    Helius can return either a pre-decimal 'tokenAmount' or (amount, decimals).
    Handle both safely.
    """
    if "tokenAmount" in transfer:
        try:
            return float(transfer["tokenAmount"])
        except Exception:
            pass

    raw = transfer.get("amount")
    dec = transfer.get("decimals")
    if isinstance(raw, int) and isinstance(dec, int) and dec > 0:
        return raw / (10 ** dec)

    try:
        return float(raw or 0)
    except Exception:
        return 0.0


async def _get_json_with_backoff(url: str, headers: Dict[str, str], params: Dict[str, Any]) -> Any:
    """
    GET with gentle exponential backoff for 429/5xx. Returns parsed JSON or []
    after exhausting retries. Keeps errors away from your cron handler.
    """
    delay = 1.0
    max_attempts = 4
    async with httpx.AsyncClient(timeout=20) as client:
        for attempt in range(max_attempts):
            r = await client.get(url, headers=headers, params=params)
            # Handle rate-limits and transient server errors with backoff
            if r.status_code == 429 or 500 <= r.status_code < 600:
                # Honor Retry-After if provided, otherwise backoff
                retry_after = r.headers.get("retry-after")
                try:
                    wait = float(retry_after) if retry_after else delay
                except Exception:
                    wait = delay
                await asyncio.sleep(wait)
                delay = min(delay * 2, 10.0)
                if attempt + 1 == max_attempts:
                    return []
                continue
            # Any other non-2xx should raise (caller expects [] on exception)
            r.raise_for_status()
            try:
                return r.json() or []
            except Exception:
                return []
    return []


# ---- Main API ---------------------------------------------------------------

async def get_new_burns(
    cfg,
    state: dict,
    *,
    burn_addr: str | None = None,
    vault_address: str | None = None,
) -> List[Dict[str, Any]]:
    """
    Return new *deposits* into the specified RNDR burn vault (ATA).
    Cursor: state['last_sig'] (latest processed transaction signature).

    Each returned event looks like:
      {'signature': str, 'ts': int, 'amount': float, 'price_usd': Optional[float]}
    """
    # 1) API key (prefer env or cfg)
    api_key = (getattr(cfg, "HELIUS_API_KEY", "") or os.environ.get("HELIUS_API_KEY", "")).strip()
    if not api_key:
        return []

    # 2) Which vault address to monitor
    addr = (
        (burn_addr or "").strip()
        or (vault_address or "").strip()
        or (getattr(cfg, "RENDER_BURN_ADDRESS", "") or "").strip()
        or (getattr(cfg, "BURN_VAULT_ADDRESS", "") or "").strip()
    )
    if not addr:
        return []

    # 3) Request newest transactions for that address
    url = f"https://api.helius.xyz/v0/addresses/{addr}/transactions"
    headers = {"x-api-key": api_key}
    params = {"limit": 100}

    txs = await _get_json_with_backoff(url, headers, params)
    if not isinstance(txs, list):
        # If Helius responded with some unexpected payload or we exhausted retries
        return []

    last_sig = state.get("last_sig")
    events: List[Dict[str, Any]] = []

    for tx in txs:
        sig = tx.get("signature")
        if not sig:
            continue
        # Stop when we reach the last processed tx
        if last_sig and sig == last_sig:
            break

        ts = int(tx.get("timestamp") or tx.get("blockTime") or 0)

        # Helius can nest transfers differently depending on endpoint/version
        transfers = tx.get("tokenTransfers") or []
        if not transfers:
            ev = tx.get("events") or {}
            transfers = ev.get("tokenTransfers") or []

        for tr in transfers:
            # Count inbound token transfers specifically to *this* vault
            to_acct = (tr.get("toUserAccount") or tr.get("to") or "").strip()
            if to_acct != addr:
                continue

            amount = _extract_amount(tr)
            if amount <= 0:
                continue

            events.append(
                {
                    "signature": sig,
                    "ts": ts,
                    "amount": amount,
                    # Set price later (only once) to avoid extra HTTP call if no events
                    # We'll attach a price below after we know we have events.
                }
            )

    # Advance the cursor to the newest signature (top of list) so we don't re-alert
    if txs:
        newest = txs[0].get("signature")
        if newest:
            state["last_sig"] = newest

    if not events:
        return []

    # Fetch price once only if we have events
    price_usd = await _get_price_usd(getattr(cfg, "COINGECKO_ID", "render-token"))
    if price_usd > 0:
        for ev in events:
            ev["price_usd"] = price_usd

    return events


# ---- Formatting -------------------------------------------------------------

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
        return f"{a:,.2f} RNDR (${u:,.2f})"

    s24, s7, s30 = totals
    lines = [
        f"🔥 {amt:,.2f} RNDR" + (f" (${usd:,.2f})" if usd else ""),
        f"📊 24 hours: {fmt_pair(s24)}",
        f"📊 7 days: {fmt_pair(s7)}",
        f"📊 30 days: {fmt_pair(s30)}",
        f"🔗 View on Solscan: https://solscan.io/tx/{ev['signature']}",
    ]
    return "\n".join(lines)
