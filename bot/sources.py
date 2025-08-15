# bot/sources.py
from __future__ import annotations

import asyncio
from typing import List, Dict, Any, Tuple
import httpx
from logging import getLogger

log = getLogger("sources")

# ---- Helpers ---------------------------------------------------------------

async def _get_json_with_backoff(url: str, headers: Dict[str, str], params: Dict[str, Any] | None,
                                 max_attempts: int = 6) -> Any:
    """
    GET with exponential backoff. Explicitly handle 401/429 and 5xx.
    """
    delay = 0.5
    last_exc: Exception | None = None

    for attempt in range(1, max_attempts + 1):
        try:
            async with httpx.AsyncClient(timeout=20) as client:
                resp = await client.get(url, headers=headers, params=params)
            if resp.status_code == 401:
                # Auth problem - no point retrying unless env is fixed
                resp.raise_for_status()
            if resp.status_code == 429:
                # Respect Retry-After when present
                ra = resp.headers.get("Retry-After")
                if ra:
                    try:
                        delay = max(delay, float(ra))
                    except Exception:
                        pass
                await asyncio.sleep(delay)
                delay = min(delay * 2, 10)
                last_exc = httpx.HTTPStatusError("429 Too Many Requests", request=resp.request, response=resp)
                continue

            # Raise on any other non-2xx status
            resp.raise_for_status()
            return resp.json()

        except httpx.HTTPStatusError as e:
            last_exc = e
            code = e.response.status_code if e.response is not None else None
            if code in (500, 502, 503, 504):
                await asyncio.sleep(delay)
                delay = min(delay * 2, 10)
                continue
            break  # 4xx that we can't fix with a retry (e.g. 401, 404)
        except Exception as e:
            last_exc = e
            await asyncio.sleep(delay)
            delay = min(delay * 2, 10)
            continue

    assert last_exc is not None
    raise last_exc


def _extract_amount(transfer: Dict[str, Any]) -> float:
    """
    Extract decimal token amount robustly for Helius shapes.
    """
    # Preferred already-decimal field
    if isinstance(transfer.get("tokenAmount"), (int, float, str)):
        try:
            return float(transfer["tokenAmount"])
        except Exception:
            pass

    # integer + decimals path
    raw = transfer.get("amount")
    dec = transfer.get("decimals")
    if isinstance(raw, int) and isinstance(dec, int) and dec > 0:
        return raw / (10 ** dec)

    try:
        return float(raw or 0)
    except Exception:
        return 0.0


async def _get_price_usd_current(coingecko_id: str = "render") -> float:
    """
    Fetch current USD price for RENDER (approximation).
    Using current price (not exact historical) keeps us simple & within limits.
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


# ---- Main entry used by webhook_app.run_burn_once --------------------------

async def get_new_burns(cfg, state: dict) -> List[Dict[str, Any]]:
    """
    Return new *deposits* of RENDER into the configured burn vault address.
    Cursor: state['last_sig'] (latest processed transaction signature).
    Each event: {'signature','ts','amount','price_usd'}.

    Reads from cfg:
      - HELIUS_API_KEY (string, required)
      - BURN_VAULT_ADDRESS or RENDER_BURN_ADDRESS (string, required)
      - RENDER_SYMBOL (optional, default "RENDER")
    """
    api_key = (getattr(cfg, "HELIUS_API_KEY", "") or "").strip()
    burn_vault = (getattr(cfg, "BURN_VAULT_ADDRESS", "") or getattr(cfg, "RENDER_BURN_ADDRESS", "") or "").strip()
    symbol = (getattr(cfg, "RENDER_SYMBOL", "RENDER") or "RENDER").upper()

    if not burn_vault:
        log.error("BURN_VAULT_ADDRESS/RENDER_BURN_ADDRESS is missing")
        return []

    if not api_key:
        # Make it loud & clear in logs; returning [] avoids spamming retries
        log.error("HELIUS_API_KEY is missing on the WEB SERVICE environment")
        return []

    base = f"https://api.helius.xyz/v0/addresses/{burn_vault}/transactions"

    # Belt + suspenders auth: send both header and query param
    headers = {
        "x-api-key": api_key,
        "accept": "application/json",
        "user-agent": "bme-bot/1.0"
    }
    params = {"limit": 100, "api-key": api_key}

    # Download recent transactions with backoff
    txs: List[Dict[str, Any]] = await _get_json_with_backoff(base, headers, params)

    last_sig = state.get("last_sig")
    new_events: List[Dict[str, Any]] = []

    # Fetch the (approximate) current price once to annotate this batch
    price_usd = await _get_price_usd_current("render")

    for tx in txs:
        sig = tx.get("signature")
        if not sig:
            continue
        # Stop once we hit the last processed signature
        if last_sig and sig == last_sig:
            break

        ts = int(tx.get("timestamp") or tx.get("blockTime") or 0)

        transfers = tx.get("tokenTransfers") or []
        if not transfers:
            ev = tx.get("events") or {}
            transfers = ev.get("tokenTransfers") or []

        # Accept any inbound RENDER transfer to this vault address
        for tr in transfers:
            sym = (tr.get("symbol") or tr.get("tokenSymbol") or "").upper()
            if sym != symbol:
                continue

            # Helius shapes use either user account or token account in "to*"
            to_user = (tr.get("toUserAccount") or "").strip()
            to_token = (tr.get("toTokenAccount") or "").strip()

            if to_user != burn_vault and to_token != burn_vault:
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

        # Update cursor to newest signature if we got any txs
    if txs:
        newest = txs[0].get("signature")
        if newest:
            state["last_sig"] = newest
    return new_events


def format_burn(ev: Dict[str, Any],
                totals: Tuple[Tuple[float, float], Tuple[float, float], Tuple[float, float]]) -> str:
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
