# bot/webhook_app.py
from __future__ import annotations

import os
import logging
from telegram.ext import Application, CommandHandler, MessageHandler, filters

from bot.config import load_settings
from bot.db import SubscriberDB
from bot.commands import cmd_start, cmd_help, handle_text

logging.basicConfig(level=logging.INFO)
log = logging.getLogger("webhook_app")


# Reused by cron worker
async def run_burn_once(bot, cfg):
    from bot import sources
    db = SubscriberDB(cfg.DATABASE_URL)
    state = await db.get_state("burn")
    events = await sources.get_new_burns(cfg, state)
    await db.save_state("burn", state)

    if not events:
        return

    subs = await db.get_subs("burn_subs")
    if not subs:
        return

    def fmt(ev):
        try:
            return sources.format_burn(ev)
        except Exception:
            return f"🔥 Burn event:\n{ev}"

    for ev in events:
        text = fmt(ev)
        for chat_id in subs:
            try:
                await bot.send_message(chat_id, text, disable_web_page_preview=True)
            except Exception:
                log.exception("send burn failed chat_id=%s", chat_id)


async def _post_init(app: Application) -> None:
    """Only ensure DB schema here. We set webhook in run_webhook below."""
    cfg = load_settings()
    db = SubscriberDB(cfg.DATABASE_URL)
    await db.ensure_schema()


def build_application(cfg):
    application = (
        Application.builder()
        .token(cfg.TELEGRAM_BOT_TOKEN)
        .post_init(_post_init)
        .build()
    )
    application.add_handler(CommandHandler("start", lambda u, c: cmd_start(u, c, cfg, {})))
    application.add_handler(CommandHandler("help",  lambda u, c: cmd_help(u, c, cfg)))
    application.add_handler(
        MessageHandler(filters.TEXT & ~filters.COMMAND, lambda u, c: handle_text(u, c, cfg, {}))
    )
    return application


def main():
    cfg = load_settings()
    if not cfg.TELEGRAM_BOT_TOKEN:
        raise RuntimeError("TELEGRAM_BOT_TOKEN missing")

    # Build the full HTTPS webhook URL explicitly
    base = (cfg.WEBHOOK_URL or os.getenv("RENDER_EXTERNAL_URL") or "").rstrip("/")
    path = (cfg.WEBHOOK_PATH or "tg").strip("/")
    if not base or not base.startswith("https://"):
        raise RuntimeError(
            "WEBHOOK_URL is missing or not https. "
            "Set WEBHOOK_URL to your Render service URL, e.g. https://<name>.onrender.com"
        )
    full_url = f"{base}/{path}"
    secret = (cfg.TELEGRAM_WEBHOOK_SECRET or "").strip() or None

    application = build_application(cfg)

    port = int(os.environ.get("PORT", "10000"))
    log.info("Binding server on 0.0.0.0:%s path=/%s", port, path)
    log.info("Using webhook_url=%s", full_url)

    # Pass webhook_url explicitly so PTB sets it correctly (https)
    application.run_webhook(
        listen="0.0.0.0",
        port=port,
        url_path=path,
        webhook_url=full_url,
        secret_token=secret,
    )


if __name__ == "__main__":
    main()
