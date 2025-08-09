# bot/webhook_app.py
import os
import logging
from aiohttp import web  # <-- import aiohttp's web to register routes
from telegram.ext import Application, CommandHandler, MessageHandler, filters

from bot.config import load_settings
from bot.commands import cmd_start, cmd_help, handle_text
from bot.db import SubscriberDB
from bot import sources

# Configure logging
logging.basicConfig(level=logging.INFO)
log = logging.getLogger("webhook_app")

async def run_burn_once(bot, cfg):
    """
    Pull new burns since last run and notify all subscribers of 'burn_subs'.
    State (cursor) is persisted via SubscriberDB so cron runs are stateless.
    """
    db = SubscriberDB()
    state = await db.get_state("burn")
    events = await sources.get_new_burns(cfg, state)
    await db.save_state("burn", state)

    if not events:
        return

    subs = await db.get_subs("burn_subs")
    if not subs:
        return

    def _fmt(ev):
        # Try to format nicely if sources provides a formatter
        try:
            return sources.format_burn(ev)
        except Exception:
            return f"🔥 Burn event:\n{ev}"

    # Notify each subscriber about each burn event
    for ev in events:
        text = _fmt(ev)
        for chat_id in subs:
            try:
                await bot.send_message(chat_id, text, disable_web_page_preview=True)
            except Exception:
                log.exception("Failed to send burn alert to chat_id=%s", chat_id)

async def _post_init(app: Application) -> None:
    """
    Runs after the server starts.  Sets the Telegram webhook and
    attaches the cron route to the internal aiohttp server.
    """
    cfg = load_settings()
    # Build webhook URL
    url = cfg.WEBHOOK_URL.rstrip("/") + "/" + (cfg.WEBHOOK_PATH or "tg").strip("/")
    secret = (cfg.TELEGRAM_WEBHOOK_SECRET or "").strip() or None

    # Set Telegram webhook
    try:
        await app.bot.set_webhook(url=url, secret_token=secret, drop_pending_updates=True)
        log.info("Webhook set: %s", url)
    except Exception:
        log.exception("set_webhook failed (server still running)")

    # Define cron handler
    async def cron_burn(request: web.Request):
        # Optional simple shared-secret check (set CRON_SECRET in your Cloud Run secret)
        expected = os.environ.get("CRON_SECRET", "")
        if expected and request.query.get("secret") != expected:
            return web.Response(status=401, text="unauthorized")

        try:
            await run_burn_once(app.bot, cfg)
            return web.Response(text="ok")
        except Exception:
            log.exception("cron burn failed")
            return web.Response(status=500, text="error")

    # Register the cron endpoint on the same aiohttp web_app used for the webhook
    app.web_app.add_routes([web.get("/cron/burn", cron_burn)])

def build_application(cfg):
    """
    Create the Telegram Application, register handlers and post_init callback.
    """
    application = Application.builder().token(cfg.TELEGRAM_BOT_TOKEN).build()
    # Attach post_init to set the webhook and cron route
    application.post_init.append(_post_init)

    # Command handlers
    application.add_handler(CommandHandler("start", lambda u, c: cmd_start(u, c, cfg, {})))
    application.add_handler(CommandHandler("help",  lambda u, c: cmd_help(u, c, cfg)))
    # Text handler
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND,
                                           lambda u, c: handle_text(u, c, cfg, {})))
    return application

def main():
    cfg = load_settings()
    if not cfg.TELEGRAM_BOT_TOKEN:
        raise RuntimeError("TELEGRAM_BOT_TOKEN missing in configuration")
    application = build_application(cfg)
    port = int(os.environ.get("PORT", "8080"))
    path = (cfg.WEBHOOK_PATH or "tg").strip("/")
    # Run the webhook server; PTB will create an aiohttp web app for you
    application.run_webhook(listen="0.0.0.0", port=port, url_path=path)

if __name__ == "__main__":
    main()
