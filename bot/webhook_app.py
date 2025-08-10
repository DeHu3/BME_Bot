# bot/webhook_app.py
from __future__ import annotations

import logging
import os
from functools import partial

from aiohttp import web
from telegram.ext import (
    Application,
    CommandHandler,
    MessageHandler,
    filters,
)

from bot.config import load_settings
from bot.db import SubscriberDB
from bot import sources  # your existing module for burn events
from bot.commands import (
    cmd_start,
    cmd_help,
    cmd_subscribe,
    cmd_unsubscribe,
    handle_text,
    bind as bind_cmd,
)

logging.basicConfig(level=logging.INFO)
log = logging.getLogger("webhook_app")


# ------------ burn job ------------
async def run_burn_once(bot, cfg, db: SubscriberDB):
    """
    Pull new burns since last cursor and notify all burn subscribers.
    Cursor/state is stored in Postgres via SubscriberDB.kv_state.
    """
    # 1) load cursor/state
    state = await db.get_state("burn")

    # 2) fetch events
    # You already have sources.get_new_burns(cfg, state) and maybe sources.format_burn
    events = await sources.get_new_burns(cfg, state)

    # 3) persist updated state
    await db.save_state("burn", state)

    # 4) deliver
    if not events:
        return

    subs = await db.get_subs("burns")
    if not subs:
        return

    def fmt(e):
        try:
            return sources.format_burn(e)
        except Exception:
            return f"🔥 Burn event:\n{e}"

    for ev in events:
        text = fmt(ev)
        for chat_id in subs:
            try:
                await bot.send_message(chat_id, text, disable_web_page_preview=True)
            except Exception:
                log.exception("send burn failed chat_id=%s", chat_id)


async def _post_init(app: Application) -> None:
    """
    Runs after the server starts. Sets the Telegram webhook and registers HTTP routes.
    """
    cfg = load_settings()

    # Ensure DB schema exists on startup
    db: SubscriberDB = app.bot_data["db"]
    await db.ensure_schema()

    # Health endpoint
    async def healthz(_request: web.Request):
        return web.Response(text="ok")

    app.web_app.add_routes([web.get("/healthz", healthz)])

    # Cron endpoint for burn polling (GET /cron/burn?secret=...)
    async def cron_burn(request: web.Request):
        # Accept secret from env or config
        expected = (os.environ.get("CRON_SECRET") or (cfg.CRON_SECRET or "")).strip()
        if expected and request.query.get("secret") != expected:
            return web.Response(status=401, text="unauthorized")

        try:
            await run_burn_once(app.bot, cfg, db)
            return web.Response(text="ok")
        except Exception:
            log.exception("cron burn failed")
            return web.Response(status=500, text="error")

    app.web_app.add_routes([web.get("/cron/burn", cron_burn)])

    # Set webhook
    base = (cfg.WEBHOOK_URL or os.environ.get("RENDER_EXTERNAL_URL", "")).strip()
    if not base:
        log.warning("No WEBHOOK_URL and no RENDER_EXTERNAL_URL; webhook will not be set.")
        return

    url = base.rstrip("/") + "/" + (cfg.WEBHOOK_PATH or "tg").strip("/")
    secret = (cfg.TELEGRAM_WEBHOOK_SECRET or "").strip() or None
    try:
        await app.bot.set_webhook(
            url=url,
            secret_token=secret,
            drop_pending_updates=True,
        )
        log.info("Webhook set: %s", url)
    except Exception:
        log.exception("set_webhook failed")


def build_application(cfg, db: SubscriberDB) -> Application:
    application = (
        Application.builder()
        .token(cfg.TELEGRAM_BOT_TOKEN)
        .post_init(_post_init)
        .build()
    )

    # store db in bot_data for post_init & handlers
    application.bot_data["db"] = db

    # handlers
    application.add_handler(CommandHandler("start", bind_cmd(cmd_start, db=db)))
    application.add_handler(CommandHandler("help", bind_cmd(cmd_help, db=db)))
    application.add_handler(CommandHandler("subscribe", bind_cmd(cmd_subscribe, db=db)))
    application.add_handler(CommandHandler("unsubscribe", bind_cmd(cmd_unsubscribe, db=db)))
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, bind_cmd(handle_text, db=db)))
    return application


def main():
    cfg = load_settings()

    # DB connection
    dsn = os.environ.get("DATABASE_URL", cfg.DATABASE_URL)
    db = SubscriberDB(dsn)

    application = build_application(cfg, db)

    # Render provides $PORT
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
