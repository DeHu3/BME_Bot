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

# ---- burn runner used by cron_worker too ----
async def run_burn_once(bot, cfg):
    """
    Pull new burns since last run and notify burn subscribers.
    Expects bot.send_message to be available (PTB Bot).
    """
    from bot import sources  # your existing module for Helius fetch & formatting

    db = SubscriberDB(cfg.DATABASE_URL)
    state = await db.get_state("burn")
    events = await sources.get_new_burns(cfg, state)  # must update 'state' internally
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

# ---- PTB app (webhook) ----
async def _post_init(app: Application) -> None:
    cfg = load_settings()

    # Ensure DB schema exists at startup
    db = SubscriberDB(cfg.DATABASE_URL)
    await db.ensure_schema()

    # Set Telegram webhook
    base = (cfg.WEBHOOK_URL or "").rstrip("/")
    if not base:
        # Render should set RENDER_EXTERNAL_URL; load_settings() tries to fill WEBHOOK_URL from it.
        # If still empty, log and skip. You can still set it manually via env later.
        log.error("WEBHOOK_URL missing and RENDER_EXTERNAL_URL not found; webhook not set.")
        return

    path = (cfg.WEBHOOK_PATH or "tg").strip("/")
    url = f"{base}/{path}"
    secret = (cfg.TELEGRAM_WEBHOOK_SECRET or "").strip() or None

    try:
        await app.bot.set_webhook(url=url, secret_token=secret, drop_pending_updates=True)
        log.info("Webhook set: %s", url)
    except Exception:
        log.exception("set_webhook failed")

def build_application(cfg):
    application = (
        Application.builder()
        .token(cfg.TELEGRAM_BOT_TOKEN)
        .post_init(_post_init)
        .build()
    )
    # handlers
    application.add_handler(CommandHandler("start", lambda u, c: cmd_start(u, c, cfg, {})))
    application.add_handler(CommandHandler("help",  lambda u, c: cmd_help(u, c, cfg)))
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, lambda u, c: handle_text(u, c, cfg, {})))
    return application

def main():
    cfg = load_settings()
    if not cfg.TELEGRAM_BOT_TOKEN:
        raise RuntimeError("TELEGRAM_BOT_TOKEN missing")
    application = build_application(cfg)
    port = int(os.environ.get("PORT", "10000"))
    path = (cfg.WEBHOOK_PATH or "tg").strip("/")
    log.info("Binding server on 0.0.0.0:%s path=/%s", port, path)
    application.run_webhook(listen="0.0.0.0", port=port, url_path=path)

if __name__ == "__main__":
    main()
