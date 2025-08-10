# bot/config.py
import os
from typing import Optional

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    # --- required ---
    TELEGRAM_BOT_TOKEN: str = Field(..., description="Telegram bot token")
    DATABASE_URL: str = Field(..., description="Postgres URL (use Render's INTERNAL URL)")

    # --- optional ---
    WEBHOOK_URL: Optional[str] = Field(
        default=None, description="Public base URL; if None we use RENDER_EXTERNAL_URL"
    )
    WEBHOOK_PATH: str = Field(default="tg", description="Webhook path segment")
    TELEGRAM_WEBHOOK_SECRET: Optional[str] = Field(
        default=None, description="Secret token Telegram includes in webhook"
    )
    CRON_SECRET: Optional[str] = Field(
        default=None, description="Shared secret for /cron/burn endpoint"
    )

    model_config = SettingsConfigDict(env_file=".env", extra="ignore")


def load_settings() -> Settings:
    s = Settings()

    # If WEBHOOK_URL is not provided, prefer Render’s provided public URL
    if not s.WEBHOOK_URL:
        render_url = os.getenv("RENDER_EXTERNAL_URL")
        if render_url:
            s.WEBHOOK_URL = render_url.rstrip("/")

    return s
