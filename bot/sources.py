import httpx

async def _helius_burn_events(cfg, limit=50):
    # Ensure the base URL ends with a slash
    base = (cfg.helius_base or "https://mainnet.helius-rpc.com").rstrip("/") + "/"
    url = base + "?api-key=" + cfg.helius_key

    payload = {
        "jsonrpc": "2.0",
        "id": "bme",
        "method": "getEvents",
        "params": {
            "query": {"account": cfg.render_mint, "types": ["TOKEN_BURN"]},
            "options": {"limit": limit}
        },
    }

    async with httpx.AsyncClient(timeout=20) as client:
        r = await client.post(url, json=payload)
        r.raise_for_status()
        data = r.json()

    return data.get("result", {}).get("value", [])

def _ui_amount(e):
    amt = e.get("amount")
    dec = e.get("decimals") or e.get("tokenDecimals") or 8
    if isinstance(amt, dict):
        if amt.get("uiAmount") is not None:
            return float(amt["uiAmount"])
        if amt.get("uiAmountString") is not None:
            return float(amt["uiAmountString"])
        if "tokenAmount" in amt:
            try:
                return int(amt["tokenAmount"]) / (10 ** int(dec))
            except Exception:
                return None
    if isinstance(amt, (int, float)):
        return float(amt)
    if isinstance(amt, str) and amt.isdigit():
        try:
            return int(amt) / (10 ** int(dec))
        except Exception:
            return None
    return None

async def get_new_burns(cfg, state, limit=50):
    events = await _helius_burn_events(cfg, limit)
    seen = state.setdefault("seen_burn", set())
    warmed = state.setdefault("burn_warmed", False)
    new = []
    for e in reversed(events):  # oldest -> newest
        sig = e.get("signature") or e.get("transactionSignature") or e.get("txSignature")
        if not sig:
            continue
        if not warmed:
            seen.add(sig)
            continue
        if sig in seen:
            continue
        seen.add(sig)
        new.append({"sig": sig, "ui_amount": _ui_amount(e)})
    state["burn_warmed"] = True
    return new
