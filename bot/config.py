# bot/config.py
from __future__ import annotations
from pydantic_settings import BaseSettings, SettingsConfigDict

class Settings(BaseSettings):
    # Telegram / server
    TELEGRAM_BOT_TOKEN: str
    WEBHOOK_URL: str                      # e.g. https://bme-bot.onrender.com
    WEBHOOK_PATH: str = "/tg"             # we normalize to exactly one leading slash
    TELEGRAM_WEBHOOK_SECRET: str | None = None

    # Storage / data
    DATABASE_URL: str
    HELIUS_API_KEY: str

    # Burn vault address: accept either env var name
    BURN_VAULT_ADDRESS: str | None = None
    RENDER_BURN_ADDRESS: str | None = None

    # Optional tuning
    RNDR_SYMBOL: str = "RNDR"
    COINGECKO_ID: str = "render-token"
    PORT: int = 10000

    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

def load_settings() -> "Settings":
    s = Settings()

    # Normalize WEBHOOK_PATH to a single leading slash (avoids /tg/tg)
    s.WEBHOOK_PATH = "/" + (s.WEBHOOK_PATH or "tg").lstrip("/")

    # Unify the two env var names so the rest of the code can always read .BURN_VAULT_ADDRESS
    if not s.BURN_VAULT_ADDRESS:
        s.BURN_VAULT_ADDRESS = (s.RENDER_BURN_ADDRESS or "").strip()

    return s
