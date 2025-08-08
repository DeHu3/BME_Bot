import os
from dataclasses import dataclass

@dataclass
class Settings:
    token: str
    helius_base: str
    helius_key: str
    render_mint: str = "rndrizKT3MK1iimdxRdWabcF7Zg7AR5T4nud4EkHBof"

def load_settings() -> Settings:
    token = os.getenv("TELEGRAM_BOT_TOKEN", "").strip()
    if not token:
        raise RuntimeError("TELEGRAM_BOT_TOKEN missing in .env")
    base = os.getenv("HELIUS_BASE", "https://mainnet.helius-rpc.com").strip()
    key = os.getenv("HELIUS_API_KEY", "").strip()
    if not key:
        raise RuntimeError("HELIUS_API_KEY missing in .env")
    return Settings(token=token, helius_base=base, helius_key=key)
