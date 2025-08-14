# bot/sources.py
import os
from typing import List, Dict, Any, Tuple
import httpx

# Solana's well-known incinerator "owner" address.
INCINERATOR_OWNER = "1nc1nerator11111111111111111111111111111111"

async def _get_price_usd(coingecko_id: str = "render-token") -> float:
    """Fetch current USD price for RNDR (approximation for alert)."""
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
    """Be lenient with shapes Helius may return."""
    # preferred already-decimal field
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

async def get_new_burns(cfg, state: dict) -> List[Dict[str, Any]]:
    """
    Return new *deposits* of RNDR into the incinerator (most frequent signal).
    Cursor: state['last_sig'] (latest processed transaction signature).
    Each event: {'signature','ts','amount','price_usd'}.
    """
    api_key = getattr(cfg, "HELIUS_API_KEY", "") or ""
    if not api_key:
        return []

    base = f"https://api.helius.xyz/v0/addresses/{INCINERATOR_OWNER}/transactions"
    params = {"api-key": api_key, "limit": 100}

    async with httpx.AsyncClient(timeout=20) as client:
        r = await client.get(base, params=params)
        r.raise_for_status()
        txs = r.json()

    # fetch USD price once for this run (approximate "at time" value)
    sym_filter = (getattr(cfg, "RNDR_SYMBOL", "RNDR") or "RNDR").upper()
    price_usd = await _get_price_usd(getattr(cfg, "COINGECKO_ID", "render-token"))

    last_sig = state.get("last_sig")
    new_events: List[Dict[str, Any]] = []

    for tx in txs:
        sig = tx.get("signature")
        if not sig:
            continue
        # stop at the last processed signature
        if last_sig and sig == last_sig:
            break

        ts = int(tx.get("timestamp") or tx.get("blockTime") or 0)

        # token transfers may be in different places depending on Helius version
        transfers = tx.get("tokenTransfers") or []
        if not transfers:
            ev = tx.get("events") or {}
            transfers = ev.get("tokenTransfers") or []

        for tr in transfers:
            # We only want inbound RNDR to accounts owned by the incinerator.
            sym = (tr.get("symbol") or tr.get("tokenSymbol") or "").upper()
            to_owner = (tr.get("toUserAccountOwner") or tr.get("toOwner") or "").strip()
            to_acct = (tr.get("toUserAccount") or "").strip()

            if sym != sym_filter:
                continue

            # Accept if owner matches. Fallback: account equals incinerator (rare).
            if to_owner != INCINERATOR_OWNER and to_acct != INCINERATOR_OWNER:
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

    # Update cursor to newest seen signature (top of list) so we don't re-alert.
    if txs:
        newest = txs[0].get("signature")
        if newest:
            state["last_sig"] = newest

    return new_events

def format_burn(ev: Dict[str, Any], totals: Tuple[Tuple[float, float], Tuple[float, float], Tuple[float, float]]) -> str:
    """
    totals: ((a24,u24),(a7,u7),(a30,u30))
    """
    amt = float(ev["amount"])
    p = float(ev.get("price_usd") or 0.0)
    usd = amt * p if p else 0.0

    def fmt_pair(t):
        a, u = t
        return f"{a:,.2f} RNDR (${u:,.2f})"

    s24, s7, s30 = totals
    lines = [
        "🔥 RNDR burn detected",
        f"Just now: {amt:,.2f} RNDR" + (f" (${usd:,.2f})" if usd else ""),
        f"24h: {fmt_pair(s24)}",
        f"7d: {fmt_pair(s7)}",
        f"30d: {fmt_pair(s30)}",
        f"Tx: https://solscan.io/tx/{ev['signature']}",
    ]
    return "\n".join(lines)
