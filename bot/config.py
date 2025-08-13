# bot/config.py
from functools import lru_cache
from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    # Telegram
    TELEGRAM_BOT_TOKEN: str
    TELEGRAM_WEBHOOK_SECRET: str | None = None

    # Webhook
    WEBHOOK_URL: str                     # e.g. https://bme-bot.onrender.com
    WEBHOOK_PATH: str = "/tg"            # path only, e.g. "/tg"

    # Database
    DATABASE_URL: str                    # Render Postgres External URL

    # Cron (if you use a secret on your cron endpoint)
    CRON_SECRET: str | None = None

    # Helius / chain API (keep these if your code uses them)
    HELIUS_API_KEY: str | None = None
    HELIUS_BASE: str = "https://mainnet.helius-rpc.com"

    class Config:
        env_file = ".env"
        extra = "ignore"


@lru_cache
def load_settings() -> Settings:
    return Settings()
