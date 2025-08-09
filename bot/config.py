# bot/config.py
from pydantic import BaseSettings


class Settings(BaseSettings):
    # Telegram
    TELEGRAM_BOT_TOKEN: str                       # <-- REQUIRED
    TELEGRAM_WEBHOOK_SECRET: str | None = None    # optional

    # Webhook
    WEBHOOK_URL: str                              # e.g. https://bme-bot-xxxxxx.run.app
    WEBHOOK_PATH: str = "tg"                      # default path

    # Others you already use
    HELIUS_API_KEY: str | None = None
    HELIUS_BASE: str = "https://mainnet.helius-rpc.com"

    class Config:
        env_file = ".env"         # allows local runs to read .env
        case_sensitive = True     # EXACT name match required


def load_settings() -> Settings:
    return Settings()
