# bot/config.py
from __future__ import annotations
import os
from functools import lru_cache
from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

class Settings(BaseSettings):
    TELEGRAM_BOT_TOKEN: str = Field(..., min_length=10)
    DATABASE_URL: str

    WEBHOOK_URL: str | None = None     # now OPTIONAL
    WEBHOOK_PATH: str = "tg"
    TELEGRAM_WEBHOOK_SECRET: str | None = None
    CRON_SECRET: str | None = None

    HELIUS_API_KEY: str | None = None
    HELIUS_BASE: str = "https://mainnet.helius-rpc.com"

    RENDER_MINT: str                                # Solana RNDR mint (REQUIRED)
    RENDER_BURN_ADDRESS: str                        # The burn/deposit address you want to watch (REQUIRED)
    COINGECKO_ID: str = "render-token"              # Optional, defaults to CoinGecko's RNDR ID
    BURN_VAULT_ADDRESS: str = ""  # set in Render env to your RNDR burn deposit ATA (…7vq)

    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

@lru_cache
def load_settings() -> Settings:
    s = Settings()
    if not s.WEBHOOK_URL:
        # Fallback to Render-provided URL if present
        base = os.getenv("RENDER_EXTERNAL_URL") or os.getenv("PUBLIC_URL")
        if base:
            s.WEBHOOK_URL = base.rstrip("/")
    return s
