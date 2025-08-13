# bot/config.py
from __future__ import annotations
import os
from functools import lru_cache
from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

class Settings(BaseSettings):
    # required
    TELEGRAM_BOT_TOKEN: str = Field(..., min_length=10)
    DATABASE_URL: str

    # optional / defaults
    WEBHOOK_URL: str | None = None          # if None → use RENDER_EXTERNAL_URL at runtime
    WEBHOOK_PATH: str = "tg"                # do NOT include leading slash
    TELEGRAM_WEBHOOK_SECRET: str | None = None
    CRON_SECRET: str | None = None

    # chain API (keep if you use them)
    HELIUS_API_KEY: str | None = None
    HELIUS_BASE: str = "https://mainnet.helius-rpc.com"

    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

@lru_cache
def load_settings() -> Settings:
    s = Settings()
    # fallback for public base URL
    if not s.WEBHOOK_URL:
        base = os.getenv("RENDER_EXTERNAL_URL") or os.getenv("PUBLIC_URL")
        if base:
            s.WEBHOOK_URL = base.rstrip("/")
    return s
