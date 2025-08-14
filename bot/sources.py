# bot/sources.py
from __future__ import annotations

import os
import time
import asyncio
from typing import List, Dict, Any, Tuple, Optional

import httpx


def _he_headers(cfg) -> Dict[str, str]:
    """
    Build Helius headers. We use header-based auth to avoid 401s.
    """
    key = (getattr(cfg, "HELIUS_API_KEY", None) or os.environ.get("HELIUS_API_KEY") or "").strip()
    return {"x-api-key": key} if key else {}


async def _get_json_with_backoff(
    url: str,
    headers: Dict[str, str],
    params: Optional[Dict[str, Any]] = None,
    tries: int = 4,
    timeout: float = 20.0,
) -> Any:
    """
    GET with simple exponential backoff for 429 responses.
    """
    delay = 0.8
    last: Optional[httpx.Response] = None
    async with httpx.AsyncClient(timeout=timeout) as client:
        for _ in range(tries):
            r = await client.get(url, headers=headers, params=params)
            last = r
            # Respect rate limiting
            if r.status_code == 429:
                ra = r.headers.get("retry-after")
                wait = float(ra) if ra else delay
                await asyncio.sleep(wait)
                delay = min(delay * 2.0, 8.0)
                continue
            r.raise_for_status()
            return r.json()
    # If we exhausted retries, raise the last error
    if last is not None:
        last.raise_for_status()
    raise RuntimeError("Helius request failed")


async def _get_price_usd(coingecko_id: str = "render") -> float:
    """
    Fetch current USD price for RENDER (approximate for alert).
    If you later want true historical-at-timestamp, we can add a
    small range query to CoinGecko's /market_chart/range endpoint.
    """
    url = "https://api.coingecko.com/api/v3/simple/price"
    params = {"ids": coingecko_id, "vs_currencies": "usd"}
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            r = await client.get(url, params=params)
            r.raise_for_status()
            data = r.json()
            # default id for RENDER is "render". If you set COINGECKO_ID it will use that.
            return float(data.get(coingecko_id, {}).get("usd", 0)) or 0.0
    except Exception:
        return 0.0


def _extract_amount(transfer: Dict[str, Any]) -> float:
    """
    Convert Helius token transfer amount into human units, robustly.
    """
    # preferred already-decimal field
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


async def get_new_burns(cfg, state: dict) -> List[Dict[str, Any]]:
    """
    Return new **BURN** events for the RENDER mint that originate from your burn vault.

    Cursor: state['last_sig']  (last processed signature from the *address timeline* we poll)
    Each returned event: {'signature','ts','amount','price_usd'}

    Env/config used:
      - HELIUS_API_KEY         (required)
      - BURN_VAULT_ADDRESS     (preferred)  -> the owner or the token account that burns
      - RENDER_BURN_ADDRESS    (fallback)   -> used only if BURN_VAULT_ADDRESS is not set
      - RENDER_MINT            (optional)   -> exact mint to match; more reliable than symbol
      - RNDR_SYMBOL            (optional)   -> default 'RENDER' if mint is not set
      - COINGECKO_ID           (optional)   -> default 'render'
    """
    api_key = (getattr(cfg, "HELIUS_API_KEY", None) or os.environ.get("HELIUS_API_KEY") or "").strip()
    if not api_key:
        return []

    # Which timeline to poll from Helius
    watch = (
        (getattr(cfg, "BURN_VAULT_ADDRESS", None) or os.environ.get("BURN_VAULT_ADDRESS"))  # preferred
        or (getattr(cfg, "RENDER_BURN_ADDRESS", None) or os.environ.get("RENDER_BURN_ADDRESS"))  # fallback
        or ""
    ).strip()
    if not watch:
        # nothing to poll
        return []

    render_mint = (getattr(cfg, "RENDER_MINT", None) or os.environ.get("RENDER_MINT") or "").strip() or None
    # If no mint provided, fall back to symbol
    symbol_pref = (
        (getattr(cfg, "RNDR_SYMBOL", None) or os.environ.get("RNDR_SYMBOL") or "RENDER")
        .strip()
        .upper()
    )

    base = f"https://api.helius.xyz/v0/addresses/{watch}/transactions"
    params = {"limit": 100}
    headers = _he_headers(cfg)

    txs: List[Dict[str, Any]] = await _get_json_with_backoff(base, headers, params=params)

    last_sig = state.get("last_sig")
    new_events: List[Dict[str, Any]] = []

    for tx in txs:
        sig = tx.get("signature")
        if not sig:
            continue
        # Stop once we reach the last processed signature
        if last_sig and sig == last_sig:
            break

        ts = int(tx.get("timestamp") or tx.get("blockTime") or int(time.time()))
        transfers = tx.get("tokenTransfers") or (tx.get("events") or {}).get("tokenTransfers") or []

        for tr in transfers:
            # We only alert on **actual burns**
            ttype = str(tr.get("type") or tr.get("transferType") or "").upper()
            if ttype != "BURN":
                continue

            mint = (tr.get("mint") or "").strip()
            sym = (tr.get("symbol") or tr.get("tokenSymbol") or "").strip().upper()

            # Must be the RENDER mint (or symbol fallback)
            if render_mint:
                if mint != render_mint:
                    continue
            else:
                if sym != symbol_pref:
                    continue

            # Ensure the burn **originates** from our vault (owner or token account)
            from_acct = (
                tr.get("fromUserAccount")
                or tr.get("tokenAccount")
                or tr.get("fromTokenAccount")
                or ""
            ).strip()
            from_owner = (tr.get("fromUserAccountOwner") or tr.get("fromOwner") or "").strip()

            if watch and (from_acct != watch and from_owner != watch):
                # Skip burns not done by our vault
                continue

            amount = _extract_amount(tr)
            if amount <= 0:
                continue

            price_usd = await _get_price_usd(getattr(cfg, "COINGECKO_ID", "render"))
            new_events.append(
                {
                    "signature": sig,
                    "ts": ts,
                    "amount": amount,
                    "price_usd": price_usd if price_usd > 0 else None,
                }
            )

    # Update the cursor to the newest signature on the page so we don't re-alert
    if txs:
        newest = txs[0].get("signature")
        if newest:
            state["last_sig"] = newest

    # Process oldest -> newest so aggregates increase monotonically
    new_events.reverse()
    return new_events


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
        return f"{a:,.2f} RENDER (${u:,.2f})"

    s24, s7, s30 = totals
    lines = [
        "🔥 RENDER burn detected",
        f"Just now: {amt:,.2f} RENDER" + (f" (${usd:,.2f})" if usd else ""),
        f"24h: {fmt_pair(s24)}",
        f"7d:  {fmt_pair(s7)}",
        f"30d: {fmt_pair(s30)}",
        f"Tx: https://solscan.io/tx/{ev['signature']}",
    ]
    return "\n".join(lines)
