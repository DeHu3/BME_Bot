# bot/webhook_app.py

import os
import logging
from aiohttp import web
from telegram.ext import Application, CommandHandler, MessageHandler, filters
from bot.commands import cmd_start, cmd_help, handle_text
from bot.config import load_settings
from bot.db import SubscriberDB
from bot import sources

# Run one burn-check and notify subscribers
async def run_burn_once(bot, cfg):
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
        try:
            return sources.format_burn(ev)
        except Exception:
            return f"🔥 Burn event:\n{ev}"
    for ev in events:
        text = _fmt(ev)
        for chat_id in subs:
            try:
                await bot.send_message(chat_id, text, disable_web_page_preview=True)
            except Exception:
                logging.getLogger("webhook_app").exception(
                    "send burn failed chat_id=%s", chat_id
                )

async def cron_burn(request):
    """
    Simple HTTP handler used by Cloud Scheduler.
    Verifies a shared secret, runs one burn check, and returns 200/401.
    """
    cfg = load_settings()
    expected = os.environ.get("CRON_SECRET", "")
    if not expected or request.query.get("secret") != expected:
        return web.Response(status=401, text="nope")
    try:
        await run_burn_once(request.app["ptb_app"].bot, cfg)
        return web.Response(text="ok")
    except Exception:
        logging.getLogger("webhook_app").exception("cron burn failed")
        return web.Response(status=500, text="error")

logging.basicConfig(level=logging.INFO)
log = logging.getLogger("webhook_app")

def build_application(cfg: object) -> Application:
    # Create the PTB application
    application = (
        Application.builder()
        .token(cfg.TELEGRAM_BOT_TOKEN)
        .build()
    )
    # Attach command/text handlers
    application.add_handler(CommandHandler("start", lambda u, c: cmd_start(u, c, cfg, {})))
    application.add_handler(CommandHandler("help",  lambda u, c: cmd_help(u, c, cfg)))
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND,
                                           lambda u, c: handle_text(u, c, cfg, {})))
    return application

def main() -> None:
    cfg = load_settings()
    if not cfg.TELEGRAM_BOT_TOKEN:
        raise RuntimeError("TELEGRAM_BOT_TOKEN missing in configuration")

    # Build the PTB application
    ptb_app = build_application(cfg)

    # Build an aiohttp app for the webhook, using PTB's helper
    path = (cfg.WEBHOOK_PATH or "tg").strip("/")
    secret = (cfg.TELEGRAM_WEBHOOK_SECRET or "").strip() or None
    webapp = ptb_app.get_webhook_app(path=f"/{path}", secret_token=secret)

    # Store the PTB app on the aiohttp app for access in handlers
    webapp["ptb_app"] = ptb_app

    # Add our cron route to the aiohttp app
    webapp.router.add_get("/cron/burn", cron_burn)

    # Start the webhook server
    port = int(os.environ.get("PORT", "8080"))
    log.info("Binding server on 0.0.0.0:%s path=/%s", port, path)
    ptb_app.run_webhook(
        listen="0.0.0.0",
        port=port,
        web_app=webapp,
    )

if __name__ == "__main__":
    main()
