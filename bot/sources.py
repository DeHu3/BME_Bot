# bot/sources.py
from __future__ import annotations
from typing import List, Dict, Any, Tuple
import httpx

# ---------- price helpers ----------
async def _get_price_usd(coingecko_id: str = "render-token") -> float:
    """
    Fetch current USD price for RNDR (used as an approximation).
    If you later want exact historical prices, we can swap this out.
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

async def usd_price_at(_ts: int, coingecko_id: str = "render-token") -> float:
    """
    Placeholder for 'price at time'.
    For now we return current price; later we can wire CoinGecko market_chart_range
    or a paid historical-pricing API for true at-time prices.
    """
    return await _get_price_usd(coingecko_id)

# ---------- parsing helpers ----------
def _extract_amount(tr: Dict[str, Any]) -> float:
    """Leniently extract RNDR amount from a Helius tokenTransfer object."""
    # already-decimal field
    if isinstance(tr.get("tokenAmount"), (int, float, str)):
        try:
            return float(tr["tokenAmount"])
        except Exception:
            pass

    # integer-with-decimals path
    raw = tr.get("amount")
    dec = tr.get("decimals")
    if isinstance(raw, int) and isinstance(dec, int) and dec > 0:
        return raw / (10 ** dec)

    try:
        return float(raw or 0)
    except Exception:
        return 0.0

# ---------- main fetch ----------
async def get_new_burns(cfg, state: dict) -> List[Dict[str, Any]]:
    """
    Return new *deposits* of RNDR into the configured burn vault token account.
    Cursor: state['last_sig'] (latest processed transaction signature).
    Each event: {'signature','ts','amount','price_usd'}.
    """
    api_key = (getattr(cfg, "HELIUS_API_KEY", "") or "").strip()
    vault  = (getattr(cfg, "BURN_VAULT_ADDRESS", "") or "").strip()
    if not api_key or not vault:
        return []

    # Optional: exact mint filter; fallback to symbol 'RNDR'
    rndr_mint = (getattr(cfg, "RNDR_MINT", "") or "").strip().lower()
    sym_filter = (getattr(cfg, "RNDR_SYMBOL", "RNDR") or "RNDR").upper()

    base = f"https://api.helius.xyz/v0/addresses/{vault}/transactions"
    params = {"api-key": api_key, "limit": 100}  # <-- include API key (fixes 401)

    async with httpx.AsyncClient(timeout=20) as client:
        r = await client.get(base, params=params)
        r.raise_for_status()
        txs = r.json()

    last_sig = state.get("last_sig")
    price_usd = await _get_price_usd(getattr(cfg, "COINGECKO_ID", "render-token"))

    new_events: List[Dict[str, Any]] = []

    for tx in txs:
        sig = tx.get("signature")
        if not sig:
            continue

        # Stop at the last processed signature to avoid duplicates
        if last_sig and sig == last_sig:
            break

        ts = int(tx.get("timestamp") or tx.get("blockTime") or 0)

        transfers = tx.get("tokenTransfers") or []
        if not transfers:
            ev = tx.get("events") or {}
            transfers = ev.get("tokenTransfers") or []

        for tr in transfers:
            # We only want inbound RNDR transfers to THIS token account (vault)
            to_acct = (tr.get("toUserAccount") or tr.get("toTokenAccount") or tr.get("to") or "").strip()
            if to_acct != vault:
                continue

            # Filter by mint if provided; else by symbol
            mint = (tr.get("mint") or tr.get("tokenMint") or "").strip().lower()
            symbol = (tr.get("symbol") or tr.get("tokenSymbol") or "").upper()

            if rndr_mint:
                if mint != rndr_mint:
                    continue
            else:
                if symbol != sym_filter:
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

# ---------- formatting ----------
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
        return f"{a:,.2f} RNDR (${u:,.2f})"

    s24, s7, s30 = totals
    lines = [
        "🔥 RNDR burn deposit detected",
        f"Just now: {amt:,.2f} RNDR" + (f" (${usd:,.2f})" if usd else ""),
        f"24h: {fmt_pair(s24)}",
        f"7d: {fmt_pair(s7)}",
        f"30d: {fmt_pair(s30)}",
        f"Tx: https://solscan.io/tx/{ev['signature']}",
    ]
    return "\n".join(lines)
