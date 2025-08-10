# bot/config.py
from functools import lru_cache
from pydantic import BaseSettings


class Settings(BaseSettings):
    TELEGRAM_BOT_TOKEN: str

    # Optional — if omitted we’ll use RENDER_EXTERNAL_URL
    WEBHOOK_URL: str | None = None
    WEBHOOK_PATH: str = "tg"
    TELEGRAM_WEBHOOK_SECRET: str | None = None

    # Render Postgres connection string. Use the **Internal Database URL**.
    DATABASE_URL: str

    # Shared secret for the /cron/burn endpoint
    CRON_SECRET: str | None = None

    class Config:
        env_file = ".env"
        case_sensitive = False


@lru_cache()
def load_settings() -> Settings:
    return Settings()
