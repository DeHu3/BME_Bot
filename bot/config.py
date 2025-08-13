# bot/config.py
from functools import lru_cache
from pydantic_settings import BaseSettings

class Settings(BaseSettings):
    TELEGRAM_BOT_TOKEN: str
    TELEGRAM_WEBHOOK_SECRET: str = ""
    WEBHOOK_URL: str
    WEBHOOK_PATH: str = "/tg"
    DATABASE_URL: str  # e.g. postgres://user:pass@host:port/dbname
    CRON_SECRET: str = ""

    class Config:
        env_file = ".env"
        extra = "ignore"

@lru_cache
def load_settings() -> Settings:
    return Settings()
