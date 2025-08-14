# bot/sources.py
import os
import asyncio
from typing import List, Dict, Any, Tuple, Optional
import httpx

# ---------- small helpers ----------

async def _get_price_usd(coingecko_id: str = "render") -> float:
    """
    Fetch current USD price for RENDER (approximation for alert).
    If you need precise historical-at-timestamp pricing later, we can replace this.
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
    Be lenient about shapes Helius may return. Prefer already-decimal 'tokenAmount',
    otherwise compute amount = raw / (10 ** decimals).
    """
    # preferred decimal field if present
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


async def _get_json_with_backoff(
    url: str,
    headers: Dict[str, str],
    params: Optional[Dict[str, Any]] = None,
    max_attempts: int = 6,
    base_delay: float = 0.6,
) -> Any:
    """
    GET with exponential backoff for 429 and 5xx. We *do not* include the API key
    in query params to avoid leaking it in logs. Auth is via headers.
    """
    last_resp: Optional[httpx.Response] = None
    for attempt in range(1, max_attempts + 1):
        try:
            async with httpx.AsyncClient(timeout=20) as client:
                resp = await client.get(url, headers=headers, params=params)
            # handle throttling and transient server errors
            if resp.status_code == 429 or 500 <= resp.status_code < 600:
                last_resp = resp
                # Respect Retry-After if present
                ra = resp.headers.get("Retry-After")
                delay = float(ra) if ra and ra.isdigit() else base_delay * (2 ** (attempt - 1))
                await asyncio.sleep(min(delay, 10.0))
                continue
            resp.raise_for_status()
            return resp.json()
        except httpx.HTTPStatusError as e:
            # If it's a 401, don't retry forever—this is an auth/config issue
            if e.response.status_code == 401:
                raise
            last_resp = e.response
            await asyncio.sleep(base_delay * (2 ** (attempt - 1)))
        except (httpx.TransportError, httpx.TimeoutException):
            await asyncio.sleep(base_delay * (2 ** (attempt - 1)))
    if last_resp is not None:
        last_resp.raise_for_status()
    raise RuntimeError("HTTP request failed after retries")


# ---------- main fetch & format API ----------

async def get_new_burns(cfg, state: dict) -> List[Dict[str, Any]]:
    """
    Return new *deposits* of RENDER into the burn vault (most frequent signal).
    Cursor: state['last_sig'] (latest processed transaction signature).
    Each event: {'signature','ts','amount','price_usd'}.
    """
    # Load API key safely; work even if Settings doesn’t declare it
    api_key = (getattr(cfg, "HELIUS_API_KEY", "") or os.getenv("HELIUS_API_KEY", "")).strip()
    if not api_key:
        # No key configured -> nothing to fetch
        return []

    # Figure out which address to watch:
    # Prefer BURN_VAULT_ADDRESS, fallback to RENDER_BURN_ADDRESS, then env.
    burn_vault = (
        (getattr(cfg, "BURN_VAULT_ADDRESS", "") or "").strip()
        or (getattr(cfg, "RENDER_BURN_ADDRESS", "") or "").strip()
        or os.getenv("BURN_VAULT_ADDRESS", "").strip()
        or os.getenv("RENDER_BURN_ADDRESS", "").strip()
    )
    if not burn_vault:
        # No address configured -> nothing to fetch
        return []

    url = f"https://api.helius.xyz/v0/addresses/{burn_vault}/transactions"

    # Use header auth only so your key never appears in logs
    headers = {
        "x-api-key": api_key,
        "accept": "application/json",
        "user-agent": "bme-bot/1.0",
    }
    params = {"limit": 100}

    txs: List[Dict[str, Any]] = await _get_json_with_backoff(url, headers, params=params, max_attempts=6)

    last_sig = state.get("last_sig")
    new_events: List[Dict[str, Any]] = []

    # We only want RENDER (not old RNDR). You can override via env RNDR_SYMBOL=RENDER if needed.
    sym_filter = (getattr(cfg, "RNDR_SYMBOL", "RENDER") or "RENDER").upper()

    # Approximate USD price once per run (optional; set COINGECKO_ID if needed)
    price_usd = await _get_price_usd(getattr(cfg, "COINGECKO_ID", "render"))

    for tx in txs:
        sig = tx.get("signature")
        if not sig:
            continue
        # stop at the last processed signature
        if last_sig and sig == last_sig:
            break

        ts = int(tx.get("timestamp") or tx.get("blockTime") or 0)

        # token transfers can be in different shapes; try both
        transfers = tx.get("tokenTransfers") or (tx.get("events") or {}).get("tokenTransfers") or []
        for tr in transfers:
            sym = (tr.get("symbol") or tr.get("tokenSymbol") or "").upper()
            if sym != sym_filter:
                continue

            to_acct = (tr.get("toUserAccount") or tr.get("toTokenAccount") or tr.get("toAccount") or "").strip()
            to_owner = (tr.get("toUserAccountOwner") or tr.get("toOwner") or "").strip()

            # Accept if the *destination* matches the burn vault (account or owner)
            if to_acct != burn_vault and to_owner != burn_vault:
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

    # Update cursor to the newest seen signature to prevent re-alerts
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

    def fmt_pair(t):
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
