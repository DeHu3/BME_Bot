# bot/sources.py
from __future__ import annotations

import math
import time
from typing import Any, Dict, List, Optional, Tuple

import httpx

# === Canonical constants from Render Foundation docs ===
# RENDER mint (SPL):
RENDER_MINT = "rndrizKT3MK1iimdxRdWabcF7Zg7AR5T4nud4EkHBof"
# Burn Multi‑Sig ATA (deposits accumulate here during the epoch):
BURN_ATA = "GqE7wcwRw86xxMz4pNq5cV3BkM64h3bvajQRQbigYqTX"

HELIUS_ADDR_TX_URL = "https://api.helius.xyz/v0/addresses/{addr}/transactions"
SOLSCAN_TX = "https://solscan.io/tx/{sig}"  # mainnet


# ---------------------------
# Helius: fetch new deposits
# ---------------------------
async def fetch_burn_deposits(
    api_key: str,
    last_ts: int,
    last_sig: str | None,
    limit: int = 100,
) -> List[Dict[str, Any]]:
    """
    Return NEW deposit events into the burn ATA:
      [
        { "signature": str, "slot": int, "ts": int, "amount": float }
      ]
    We filter for SPL token transfers where mint==RENDER_MINT and toUserAccount==BURN_ATA.

    We use last_ts to ignore anything at/older than what we've processed.
    (If two tx share the same timestamp, last_sig helps avoid dup on the boundary.)
    """
    url = HELIUS_ADDR_TX_URL.format(addr=BURN_ATA)
    params = {"api-key": api_key, "limit": str(limit)}
    async with httpx.AsyncClient(timeout=30) as client:
        r = await client.get(url, params=params)
        r.raise_for_status()
        txs = r.json()

    out: List[Dict[str, Any]] = []
    for tx in txs:
        sig = tx.get("signature")
        ts = int(tx.get("timestamp") or 0)
        slot = int(tx.get("slot") or 0)

        # Only consider tx strictly newer than last_ts, or equal ts but different signature.
        if ts < last_ts:
            continue
        if ts == last_ts and last_sig and sig <= last_sig:
            # Already processed boundary tx (or older lexicographically)
            continue

        amount = 0.0
        for tr in tx.get("tokenTransfers") or []:
            if (
                tr.get("mint") == RENDER_MINT
                and tr.get("toUserAccount") == BURN_ATA
            ):
                # Helius returns human-decimal amount in tokenAmount
                amount += float(tr.get("tokenAmount") or 0)

        if amount > 0:
            out.append(
                {"signature": sig, "slot": slot, "ts": ts, "amount": amount}
            )

    # oldest first so alerts are ordered
    out.sort(key=lambda e: (e["ts"], e["signature"]))
    return out


# -----------------------------------------
# CoinGecko: price at/around a given time
# -----------------------------------------
async def _cg_price_range(ts_from: int, ts_to: int, coin_id: str) -> Optional[float]:
    url = (
        f"https://api.coingecko.com/api/v3/coins/{coin_id}/market_chart/range"
        f"?vs_currency=usd&from={ts_from}&to={ts_to}"
    )
    async with httpx.AsyncClient(timeout=30) as client:
        r = await client.get(url)
        if r.status_code != 200:
            return None
        data = r.json()
    prices = data.get("prices") or []
    if not prices:
        return None
    # prices -> [[ms, price], ...]; pick closest to ts midpoint
    return float(prices[len(prices) // 2][1])


async def usd_price_at(ts: int) -> Optional[float]:
    """
    Historical USD price near the tx timestamp. We try "render" first, then
    fallback to legacy "render-token" if CoinGecko ever changes the id.
    We query a ±15min window and pick the mid-point price.
    """
    from_ts = ts - 15 * 60
    to_ts = ts + 15 * 60
    price = await _cg_price_range(from_ts, to_ts, "render")
    if price is None:
        price = await _cg_price_range(from_ts, to_ts, "render-token")
    return price


# ----------------
# Formatting text
# ----------------
def fmt_amount(x: float) -> str:
    return f"{x:,.2f} RENDER"


def fmt_usd(x: float) -> str:
    return f"${x:,.2f}"


def format_burn_message(
    *,
    amount: float,
    usd: float,
    signature: str,
    sum24: Tuple[float, float],
    sum7: Tuple[float, float],
    sum30: Tuple[float, float],
) -> str:
    """
    Build the exact text the user asked for:
      🔥 <amount> RENDER ($X.XX) · View on Solscan
      📊 24 hours: ...
      📊 7 days: ...
      📊 30 days: ...
    """
    link = SOLSCAN_TX.format(sig=signature)
    lines = [
        f"🔥 {fmt_amount(amount)} ({fmt_usd(usd)}) · <a href=\"{link}\">View on Solscan</a>",
        f"📊 24 hours: {fmt_amount(sum24[0])} ({fmt_usd(sum24[1])})",
        f"📊 7 days: {fmt_amount(sum7[0])} ({fmt_usd(sum7[1])})",
        f"📊 30 days: {fmt_amount(sum30[0])} ({fmt_usd(sum30[1])})",
    ]
    return "\n".join(lines)
