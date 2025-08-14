# bot/sources.py
from __future__ import annotations

import asyncio
import os
from typing import List, Dict, Any, Tuple
import httpx


async def _get_price_usd(coingecko_id: str = "render") -> float:
    """
    Fetch current USD price for RENDER (approximation for alerts).
    We keep this once-per-run to reduce external calls.
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


def _extract_amount(transfer: Dict[str, Any]) -> float:
    """
    Be lenient with shapes Helius may return.
    Prefer a ready-decimal field; fall back to (amount,decimals).
    """
    if isinstance(transfer.get("tokenAmount"), (int, float, str)):
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


async def _get_json_with_backoff(
    url: str,
    headers: Dict[str, str],
    params: Dict[str, Any] | None = None,
    max_attempts: int = 6,
) -> Any:
    """
    GET with proper async backoff for 429. Respects Retry-After if present,
    otherwise exponential backoff (1s, 2s, 4s, 8s, 16s, 30s cap).
    """
    backoff = 1.0
    last_resp: httpx.Response | None = None

    async with httpx.AsyncClient(timeout=20) as client:
        for attempt in range(max_attempts):
            resp = await client.get(url, headers=headers, params=params)
            if resp.status_code == 429:
                # Save the last response and compute wait
                last_resp = resp
                ra = resp.headers.get("retry-after")
                try:
                    wait = float(ra) if ra is not None else backoff
                except Exception:
                    wait = backoff

                # Cap and increment backoff
                backoff = min(backoff * 2, 30.0)

                # On the final attempt, break and raise
                if attempt == max_attempts - 1:
                    break

                # IMPORTANT: actually await the sleep
                await asyncio.sleep(wait)
                continue

            resp.raise_for_status()
            return resp.json()

    # If we’re here we exhausted attempts; raise the last response error
    if last_resp is not None:
        last_resp.raise_for_status()
    # Fallback: raise a generic error (shouldn’t be reached normally)
    raise httpx.HTTPStatusError("HTTP error with no response", request=None, response=None)


async def get_new_burns(cfg, state: dict) -> List[Dict[str, Any]]:
    """
    Return new deposits of RENDER into the burn vault (your desired signal).
    Cursor: state['last_sig'] (latest processed transaction signature).
    Each event: {'signature','ts','amount','price_usd'}.
    """
    # Read API key from cfg or env
    api_key = (getattr(cfg, "HELIUS_API_KEY", "") or os.getenv("HELIUS_API_KEY", "")).strip()
    if not api_key:
        return []

    # Use the configured burn vault address; fall back to legacy env name for safety.
    burn_vault = (
        getattr(cfg, "BURN_VAULT_ADDRESS", "") or
        getattr(cfg, "RENDER_BURN_ADDRESS", "") or
        os.getenv("BURN_VAULT_ADDRESS", "") or
        os.getenv("RENDER_BURN_ADDRESS", "")
    ).strip()
    if not burn_vault:
        return []

    url = f"https://api.helius.xyz/v0/addresses/{burn_vault}/transactions"
    headers = {
        # Use header auth to avoid leaking key in logs/URLs
        "x-api-key": api_key,
        "accept": "application/json",
        "user-agent": "bme-bot/1.0",
    }
    params = {"limit": 100}

    # Pull latest tx page with robust backoff
    txs: List[Dict[str, Any]] = await _get_json_with_backoff(url, headers, params=params, max_attempts=6)

    last_sig = state.get("last_sig")
    symbol = (getattr(cfg, "RNDR_SYMBOL", "RENDER") or "RENDER").upper()
    price_usd = await _get_price_usd(getattr(cfg, "COINGECKO_ID", "render") or "render")

    new_events: List[Dict[str, Any]] = []

    for tx in txs:
        sig = tx.get("signature")
        if not sig:
            continue
        # Stop once we hit the last processed tx
        if last_sig and sig == last_sig:
            break

        ts = int(tx.get("timestamp") or tx.get("blockTime") or 0)

        # Token transfers may be under different keys depending on Helius output
        transfers = tx.get("tokenTransfers") or []
        if not transfers:
            ev = tx.get("events") or {}
            transfers = ev.get("tokenTransfers") or []

        for tr in transfers:
            # Only inbound RENDER to the *burn vault account* you provided
            sym = (tr.get("symbol") or tr.get("tokenSymbol") or "").upper()
            to_acct = (tr.get("toUserAccount") or tr.get("to") or tr.get("toUserAccountOwner") or "").strip()

            if sym != symbol:
                continue
            if to_acct != burn_vault:
                continue

            amount = _extract_amount(tr)
            if amount <= 0:
                continue

            new_events.append(
                {
                    "signature": sig,
                    "ts": ts,
                    "amount": amount,
                    "price_usd": price_usd if price_usd > 0 else None,
                }
            )

    # Advance cursor to newest signature from this page
    if txs:
        newest = txs[0].get("signature")
        if newest:
            state["last_sig"] = newest

    return new_events


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
        f"7d:  {fmt_pair(s7)}",
        f"30d: {fmt_pair(s30)}",
        f"Tx: https://solscan.io/tx/{ev['signature']}",
    ]
    return "\n".join(lines)
