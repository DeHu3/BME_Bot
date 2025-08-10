# bot/webhook_app.py
import os
import logging
from aiohttp import web
from telegram.ext import Application, CommandHandler, MessageHandler, filters

from bot.config import load_settings
from bot.db import SubscriberDB
from bot.commands import cmd_start, cmd_help, handle_text

logging.basicConfig(level=logging.INFO)
log = logging.getLogger("webhook_app")


async def run_burn_once(bot, cfg, db: SubscriberDB):
    """Fetch new burn events and notify subscribers."""
    from bot import sources  # your existing sources module

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
    cfg = load_settings()

    # DB init + schema
    db = SubscriberDB(cfg.DATABASE_URL)
    await db.ensure_schema()
    app.bot_data["db"] = db

    # Webhook
    base = (cfg.WEBHOOK_URL or os.getenv("RENDER_EXTERNAL_URL", "")).rstrip("/")
    url = f"{base}/{(cfg.WEBHOOK_PATH or 'tg').strip('/')}"
    secret = (cfg.TELEGRAM_WEBHOOK_SECRET or "").strip() or None

    try:
        await app.bot.set_webhook(url=url, secret_token=secret, drop_pending_updates=True)
        log.info("Webhook set: %s", url)
    except Exception:
        log.exception("set_webhook failed (server still running)")

    # Cron endpoint (shared secret)
    async def cron_burn(request: web.Request):
        expected = (cfg.CRON_SECRET or "").strip()
        if expected and request.query.get("secret") != expected:
            return web.Response(status=401, text="nope")
        try:
            await run_burn_once(app.bot, cfg, db)
            return web.Response(text="ok")
        except Exception:
            log.exception("cron burn failed")
            return web.Response(status=500, text="error")

    app.web_app.add_routes([web.get("/cron/burn", cron_burn)])


def build_application(cfg):
    application = (
        Application.builder()
        .token(cfg.TELEGRAM_BOT_TOKEN)
        .post_init(_post_init)
        .build()
    )
    application.add_handler(CommandHandler("start", lambda u, c: cmd_start(u, c)))
    application.add_handler(CommandHandler("help",  lambda u, c: cmd_help(u, c)))
    application.add_handler(
        MessageHandler(filters.TEXT & ~filters.COMMAND, lambda u, c: handle_text(u, c))
    )
    return application


def main():
    cfg = load_settings()
    if not cfg.TELEGRAM_BOT_TOKEN:
        raise RuntimeError("TELEGRAM_BOT_TOKEN missing")
    application = build_application(cfg)
    port = int(os.environ.get("PORT", "10000"))
    path = (cfg.WEBHOOK_PATH or "tg").strip("/")
    log.info("Binding server on 0.0.0.0:%s path=/%s", port, path)
    application.run_webhook(
        listen="0.0.0.0",
        port=port,
        url_path=path,
    )


if __name__ == "__main__":
    main()
