# bot/sources.py
from __future__ import annotations

import asyncio
import random
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
# HTTP helpers with backoff
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


def _candidate_dest_accounts(tr: Dict[str, Any]) -> List[str]:
    """Collect likely destination token-account fields across Helius variants."""
    cands: List[str] = []
    for key in (
        "toUserAccount",
        "toTokenAccount",
        "to",
        "destination",
        "destinationTokenAccount",
        "account",
        "destinationUserAccount",
    ):
        v = tr.get(key)
        if isinstance(v, str) and v.strip():
            cands.append(v.strip())

    # Some shapes carry balances with embedded account info
    pb = tr.get("postTokenBalance") or tr.get("postTokenBalances")
    if isinstance(pb, dict):
        for k in ("account", "owner"):
            vv = pb.get(k)
            if isinstance(vv, str) and vv.strip():
                cands.append(vv.strip())
    elif isinstance(pb, list):
        for e in pb:
            if isinstance(e, dict):
                for k in ("account", "owner"):
                    vv = e.get(k)
                    if isinstance(vv, str) and vv.strip():
                        cands.append(vv.strip())

    # Normalize/unique
    seen = set()
    out = []
    for a in cands:
        if a not in seen:
            out.append(a)
            seen.add(a)
    return out


def _is_to_burn_vault(tr: Dict[str, Any], burn_vault: str) -> bool:
    burn_vault = burn_vault.strip()
    if not burn_vault:
        return False
    for acct in _candidate_dest_accounts(tr):
        if acct == burn_vault:
            return True
    return False


# ------------------------
# Public API used by webhook_app.run_burn_once (poll)
# ------------------------
async def get_new_burns(
    cfg,
    state: dict,
    *,
    ignore_cursor: bool = False,
    max_pages: int = 3,   # kept for compatibility; no longer used as a hard cap
    limit: int = 100,
) -> List[Dict[str, Any]]:
    """
    Return new *deposit* events into the burn vault ATA (most frequent signal).

    Cursor in `state` (mutated in-place but only persisted by caller after send):
      - last_ts: int (unix seconds)
      - last_sig: str

    If ignore_cursor=True (admin replay), we don't filter by cursor and we DO NOT
    touch the persisted cursor (the caller passes a temp state).

    NOTE: This version **removes the page cap**. We page until we hit the
    (last_ts/last_sig) barrier or exhaust history.
    """
    api_key = (getattr(cfg, "HELIUS_API_KEY", "") or "").strip()
    if not api_key:
        return []

    burn_vault = (getattr(cfg, "BURN_VAULT_ADDRESS", "") or getattr(cfg, "RENDER_BURN_ADDRESS", "") or "").strip()
    if not burn_vault:
        return []

    base = f"https://api.helius.xyz/v0/addresses/{burn_vault}/transactions"
    # Use both query param and header to be robust across gateways
    common_params = {"limit": limit, "api-key": api_key}
    common_headers = {"X-API-Key": api_key}

    last_ts: int = int(state.get("last_ts") or 0)
    last_sig: Optional[str] = state.get("last_sig") or None

    newest_page_sig: Optional[str] = None
    newest_page_ts: int = 0

    price_usd = await _get_price_usd(getattr(cfg, "COINGECKO_ID", "render-token"))

    events_by_sig: Dict[str, Dict[str, Any]] = {}
    before: Optional[str] = None
    pages = 0  # informational only

    while True:
        params = dict(common_params)
        if before:
            params["before"] = before

        txs: List[Dict[str, Any]] = await _get_json_with_backoff(base, params=params, headers=common_headers)
        if not txs:
            break

        # Record the very newest tx seen (page 0 top) so caller can advance cursor
        if pages == 0:
            s0 = txs[0].get("signature")
            t0 = int(txs[0].get("timestamp") or txs[0].get("blockTime") or 0)
            if isinstance(s0, str):
                newest_page_sig = s0
                newest_page_ts = t0

        stop = False
        for tx in txs:
            sig = tx.get("signature")
            if not isinstance(sig, str):
                continue
            ts = int(tx.get("timestamp") or tx.get("blockTime") or 0)

            # Cursor barrier unless we're doing admin replay
            if not ignore_cursor and last_ts > 0:
                if ts < last_ts:
                    stop = True
                    break
                if last_sig and ts == last_ts and sig == last_sig:
                    stop = True
                    break

            transfers = tx.get("tokenTransfers") or []
            if not transfers:
                ev = tx.get("events") or {}
                transfers = ev.get("tokenTransfers") or []

            total_amount = 0.0
            for tr in transfers:
                if _is_to_burn_vault(tr, burn_vault):
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

        if stop:
            break

        before = txs[-1].get("signature") or before
        pages += 1

    # Order oldest->newest for human-friendly delivery
    new_events = list(events_by_sig.values())
    new_events.sort(key=lambda e: (e["ts"], e["signature"]))

    # Advance in-memory cursor to the newest we saw on page 0
    if not ignore_cursor and newest_page_sig:
        state["last_ts"] = newest_page_ts
        state["last_sig"] = newest_page_sig

    return new_events


# ------------------------
# NEW: Parse Helius Enhanced Webhook payload (push)
# ------------------------
async def parse_helius_webhook(cfg, payload: Any) -> List[Dict[str, Any]]:
    """
    Parse Helius Enhanced Webhook payload and return burn events in the
    same shape as get_new_burns(): {'signature','ts','amount','price_usd'}.
    We detect deposits into the configured burn vault ATA.
    """
    burn_vault = (getattr(cfg, "BURN_VAULT_ADDRESS", "") or getattr(cfg, "RENDER_BURN_ADDRESS", "") or "").strip()
    if not burn_vault:
        return []

    # Helius may wrap transactions under different keys
    if isinstance(payload, list):
        txs = payload
    elif isinstance(payload, dict):
        txs = (
            payload.get("data")
            or payload.get("transactions")
            or payload.get("events")
            or payload.get("payload")
            or []
        )
    else:
        txs = []

    price_usd = await _get_price_usd(getattr(cfg, "COINGECKO_ID", "render-token"))
    by_sig: Dict[str, Dict[str, Any]] = {}

    for tx in txs:
        sig = tx.get("signature") or tx.get("transaction") or tx.get("txHash")
        if not isinstance(sig, str):
            continue
        ts = int(tx.get("timestamp") or tx.get("blockTime") or 0)

        transfers = tx.get("tokenTransfers") or []
        if not transfers:
            ev = tx.get("events") or {}
            transfers = ev.get("tokenTransfers") or []

        total = 0.0
        for tr in transfers:
            if _is_to_burn_vault(tr, burn_vault):
                amt = _extract_amount(tr)
                if amt > 0:
                    total += amt

        if total > 0:
            by_sig[sig] = {
                "signature": sig,
                "ts": ts,
                "amount": total,
                "price_usd": price_usd if price_usd > 0 else None,
            }

    out = list(by_sig.values())
    out.sort(key=lambda e: (e["ts"], e["signature"]))
    return out


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
