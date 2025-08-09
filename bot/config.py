# bot/config.py
import os
from dataclasses import dataclass
import logging

log = logging.getLogger("config")


@dataclass
class Settings:
    # Telegram
    TELEGRAM_BOT_TOKEN: str
    TELEGRAM_WEBHOOK_SECRET: str | None

    # Webhook
    WEBHOOK_URL: str         # e.g. https://bme-bot-xxxxxxxxxx.run.app  (no trailing slash)
    WEBHOOK_PATH: str        # e.g. "tg"

    # Helius / others
    HELIUS_API_KEY: str | None
    HELIUS_BASE: str


def _need(name: str, *, required: bool = False, default: str | None = None) -> str | None:
    val = os.getenv(name, default)
    if required and not val:
        raise RuntimeError(f"Missing required environment variable: {name}")
    return val


def load_settings() -> Settings:
    s = Settings(
        TELEGRAM_BOT_TOKEN      = _need("TELEGRAM_BOT_TOKEN", required=True),
        TELEGRAM_WEBHOOK_SECRET = _need("TELEGRAM_WEBHOOK_SECRET", required=False),
        WEBHOOK_URL             = _need("WEBHOOK_URL", required=True),
        WEBHOOK_PATH            = _need("WEBHOOK_PATH", required=False, default="tg"),
        HELIUS_API_KEY          = _need("HELIUS_API_KEY", required=False),
        HELIUS_BASE             = _need("HELIUS_BASE", required=False, default="https://mainnet.helius-rpc.com"),
    )

    # Log non‑secret keys to help diagnose in Cloud Run logs
    log.info("config loaded: WEBHOOK_URL=%s WEBHOOK_PATH=%s HELIUS_BASE=%s",
             s.WEBHOOK_URL, s.WEBHOOK_PATH, s.HELIUS_BASE)
    return s
