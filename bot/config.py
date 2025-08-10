# bot/config.py
import os
from typing import Optional

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    # --- required ---
    TELEGRAM_BOT_TOKEN: str = Field(..., description="Telegram bot token")
    DATABASE_URL: str = Field(..., description="SQLAlchemy / asyncpg URL")

    # --- optional / defaults ---
    WEBHOOK_URL: Optional[str] = Field(
        default=None, description="Public base URL; if None we use RENDER_EXTERNAL_URL"
    )
    WEBHOOK_PATH: str = Field(default="tg", description="Webhook path segment")
    TELEGRAM_WEBHOOK_SECRET: Optional[str] = Field(
        default=None, description="Secret token for Telegram webhook verification"
    )
    CRON_SECRET: Optional[str] = Field(
        default=None, description="Shared secret for /cron/burn endpoint"
    )

    # pydantic v2 config
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")


def load_settings() -> Settings:
    s = Settings()

    # If WEBHOOK_URL not provided, prefer Render’s provided public URL
    if not s.WEBHOOK_URL:
        render_url = os.getenv("RENDER_EXTERNAL_URL")
        if render_url:
            # RENDER_EXTERNAL_URL is like: https://your-service.onrender.com
            s.WEBHOOK_URL = render_url.rstrip("/")

    return s
