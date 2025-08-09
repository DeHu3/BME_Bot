# bot/webhook_app.py
from aiohttp import web
from bot.db import SubscriberDB
from bot import sources  # this is the same module you were using in the old job
import os
import logging
from telegram.ext import Application, CommandHandler, MessageHandler, filters
from bot.commands import cmd_start, cmd_help, handle_text
from bot.config import load_settings

async def run_burn_once(bot, cfg):
    """
    Pull new burns since last run and notify all subscribers of 'burn_subs'.
    We persist/restore the 'state' (cursor) in Firestore so cron runs are stateless.
    """
    db = SubscriberDB()

    # Load last cursor
    state = await db.get_state("burn")

    # Fetch new burn events (uses your existing sources.get_new_burns API)
    events = await sources.get_new_burns(cfg, state)

    # Persist updated cursor/state for next run
    await db.save_state("burn", state)

    # Nothing new? bail fast
    if not events:
        return

    subs = await db.get_subs("burn_subs")
    if not subs:
        return

    # Prepare a formatter if your sources module provides one
    def _fmt(ev):
        try:
            return sources.format_burn(ev)  # if exists in your sources
        except Exception:
            return f"🔥 Burn event:\n{ev}"

    # Send to all subscribers
    for ev in events:
        text = _fmt(ev)
        for chat_id in subs:
            try:
                await bot.send_message(chat_id, text, disable_web_page_preview=True)
            except Exception:
                logging.getLogger("webhook_app").exception("send burn failed chat_id=%s", chat_id)


logging.basicConfig(level=logging.INFO)
log = logging.getLogger("webhook_app")

def build_application(cfg):
    # Use the builder to register post_init callback
    application = (
        Application.builder()
        .token(cfg.TELEGRAM_BOT_TOKEN)
        .post_init(_post_init)  # register post_init here
        .build()
    )

    # handlers
    application.add_handler(CommandHandler("start", lambda u, c: cmd_start(u, c, cfg, {})))
    application.add_handler(CommandHandler("help",  lambda u, c: cmd_help(u, c, cfg)))
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND,
                                           lambda u, c: handle_text(u, c, cfg, {})))
    return application

async def _post_init(app: Application) -> None:
    # set webhook AFTER the server is up; if it fails, we log but keep serving
    cfg = load_settings()  # reload settings to access WEBHOOK_URL etc.
    url  = cfg.WEBHOOK_URL.rstrip("/") + "/" + (cfg.WEBHOOK_PATH or "tg").strip("/")
    secret = (cfg.TELEGRAM_WEBHOOK_SECRET or "").strip() or None
    try:
        await app.bot.set_webhook(url=url, secret_token=secret, drop_pending_updates=True)
        log.info("Webhook set: %s", url)
    except Exception:
        log.exception("set_webhook failed (server still running)")
            # -- add this under the existing try/except in _post_init --
    async def cron_burn(request: web.Request):
        # simple shared-secret check
        expected = os.environ.get("CRON_SECRET", "")
        if not expected or request.query.get("secret") != expected:
            return web.Response(status=401, text="nope")

        try:
            await run_burn_once(app.bot, cfg)
            return web.Response(text="ok")
        except Exception:
            log.exception("cron burn failed")
            return web.Response(status=500, text="error")

    # register the cron route (available on the same aiohttp app PTB uses)
    app.web_app.add_routes([web.get("/cron/burn", cron_burn)])


def main():
    cfg = load_settings()
    # Fail fast if no bot token is configured
    if not cfg.TELEGRAM_BOT_TOKEN:
        raise RuntimeError("TELEGRAM_BOT_TOKEN missing in configuration")
    application = build_application(cfg)
    port = int(os.environ.get("PORT", "8080"))
    path = (cfg.WEBHOOK_PATH or "tg").strip("/")
    log.info("Binding server on 0.0.0.0:%s path=/%s", port, path)
    application.run_webhook(
        listen="0.0.0.0",
        port=port,
        url_path=path,
    )

if __name__ == "__main__":
    main()
