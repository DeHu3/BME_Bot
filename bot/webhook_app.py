# bot/webhook_app.py
import logging
import os
from telegram.ext import Application, CommandHandler, MessageHandler, filters
from bot.commands import cmd_start, cmd_help, handle_text
from bot.config import load_settings
from bot.db import SubscriberDB

# Pull your “sources” module (should include get_new_burns & format_burn)
from bot import sources

logging.basicConfig(level=logging.INFO)
log = logging.getLogger("webhook_app")

async def run_burn_once(bot, cfg):
    """Pull new burn events since last run and notify all subscribers."""
    db = SubscriberDB()
    state = await db.get_state("burn")
    events = await sources.get_new_burns(cfg, state)  # external API call to fetch burns
    await db.save_state("burn", state)

    if not events:
        return
    subs = await db.get_subs("burn_subs")
    if not subs:
        return

    for ev in events:
        text = sources.format_burn(ev) if hasattr(sources, "format_burn") else f"🔥 Burn event:\n{ev}"
        for chat_id in subs:
            try:
                await bot.send_message(chat_id, text, disable_web_page_preview=True)
            except Exception:
                log.exception("Failed to send burn alert to chat_id=%s", chat_id)

async def _post_init(app: Application) -> None:
    """Set webhook and ensure DB schema after the app starts."""
    cfg = load_settings()
    db = SubscriberDB()
    await db.ensure_schema()
    url = cfg.WEBHOOK_URL.rstrip("/") + "/" + cfg.WEBHOOK_PATH.strip("/")
    secret = cfg.TELEGRAM_WEBHOOK_SECRET or None
    try:
        await app.bot.set_webhook(url=url, secret_token=secret, drop_pending_updates=True)
        log.info("Webhook set: %s", url)
    except Exception:
        log.exception("set_webhook failed")

def build_application(cfg):
    app = Application.builder().token(cfg.TELEGRAM_BOT_TOKEN).post_init(_post_init).build()
    app.add_handler(CommandHandler("start", lambda u, c: cmd_start(u, c, cfg, {})))
    app.add_handler(CommandHandler("help",  lambda u, c: cmd_help(u, c, cfg)))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, lambda u, c: handle_text(u, c, cfg, {})))
    return app

def main():
    cfg = load_settings()
    if not cfg.TELEGRAM_BOT_TOKEN:
        raise RuntimeError("TELEGRAM_BOT_TOKEN missing in configuration")
    app = build_application(cfg)
    # Bind to port 10000 to match Render’s health check; path is defined in cfg.WEBHOOK_PATH
    port = int(os.environ.get("PORT", "10000"))
    path = cfg.WEBHOOK_PATH.strip("/")
    log.info("Binding server on 0.0.0.0:%s path=/%s", port, path)
    app.run_webhook(listen="0.0.0.0", port=port, url_path=path)

if __name__ == "__main__":
    main()
