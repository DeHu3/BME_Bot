# bot/webhook_app.py
import os
import logging
from aiohttp import web
from telegram.ext import Application, CommandHandler, MessageHandler, filters

from bot.config import load_settings
from bot.commands import cmd_start, cmd_help, handle_text
from bot.db import SubscriberDB
from bot import sources

logging.basicConfig(level=logging.INFO)
log = logging.getLogger("webhook_app")


async def run_burn_once(bot, cfg):
    """Pull new burns and notify subscribers."""
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
                log.exception("Failed to send burn alert to chat_id=%s", chat_id)


def make_aio_app(application, cfg):
    """Create aiohttp app used by PTB, add /healthz and /cron/burn routes."""
    aio = web.Application()

    async def healthz(_request):
        return web.Response(text="ok")

    async def cron_burn(request: web.Request):
        expected = (os.environ.get("CRON_SECRET") or "").strip()
        received = (request.query.get("secret") or "").strip()
        if expected and expected and received != expected:
            return web.Response(status=401, text="unauthorized")
        try:
            await run_burn_once(application.bot, cfg)
            return web.Response(text="ok")
        except Exception:
            log.exception("cron burn failed")
            return web.Response(status=500, text="error")

    aio.add_routes([
        web.get("/healthz", healthz),
        web.get("/cron/burn", cron_burn),
    ])
    # Keep refs if you need them later
    aio["application"] = application
    aio["cfg"] = cfg
    return aio


async def _post_init(app: Application, cfg):
    """Run once after HTTP server is ready. Only set the Telegram webhook."""
    webhook_url = cfg.WEBHOOK_URL.rstrip("/") + cfg.WEBHOOK_PATH
    secret = (cfg.TELEGRAM_WEBHOOK_SECRET or "").strip() or None
    try:
        await app.bot.set_webhook(
            url=webhook_url,
            secret_token=secret,
            drop_pending_updates=True,
        )
        log.info("Webhook set: %s", webhook_url)
    except Exception:
        log.exception("set_webhook failed")


def build_application(cfg):
    application = Application.builder().token(cfg.TELEGRAM_BOT_TOKEN).build()

    # Register command & message handlers
    application.add_handler(CommandHandler("start", lambda u, c: cmd_start(u, c, cfg, {})))
    application.add_handler(CommandHandler("help", lambda u, c: cmd_help(u, c, cfg)))
    application.add_handler(
        MessageHandler(filters.TEXT & ~filters.COMMAND, lambda u, c: handle_text(u, c, cfg, {}))
    )
    return application


def main():
    cfg = load_settings()

    port = int(os.environ.get("PORT", "10000"))  # Render provides PORT
    path = cfg.WEBHOOK_PATH  # expect string starting with "/", e.g. "/tg"

    application = build_application(cfg)

    # Provide post_init to set the Telegram webhook
    async def _pi(app: Application):
        await _post_init(app, cfg)

    application.post_init = _pi  # do not append; just set it

    # Build aiohttp app so we can add /healthz and /cron/burn
    aio_app = make_aio_app(application, cfg)

    public_webhook_url = cfg.WEBHOOK_URL.rstrip("/") + path
    log.info("Binding server on 0.0.0.0:%s path=%s", port, path)
    log.info("Using webhook_url=%s", public_webhook_url)

    def main() -> None:
    # Load env and build the Application exactly once
    cfg = load_settings()

    # Normalize the webhook path: PTB expects *url_path* WITHOUT leading slash,
    # but for consistency we allow WEBHOOK_PATH to be "/tg" in env.
    path = cfg.WEBHOOK_PATH.strip()
    if not path.startswith("/"):
        path = "/" + path                   # ensure leading slash for the URL
    url_path = path.lstrip("/")             # PTB expects this *without* slash

    # Construct full HTTPS webhook URL (must be https for Telegram)
    webhook_url = cfg.WEBHOOK_URL.rstrip("/") + path

    # Build your Application the same way you already do above
    application = build_application(cfg)    # keep your existing builder/handlers

    logger.info("Binding server on 0.0.0.0:%s path=%s", cfg.PORT, path)
    logger.info("Using webhook_url=%s", webhook_url)

    # python-telegram-bot 20.7 valid parameters:
    # - listen, port, url_path, webhook_url, secret_token
    application.run_webhook(
        listen="0.0.0.0",
        port=cfg.PORT,
        url_path=url_path,
        webhook_url=webhook_url,
        secret_token=cfg.TELEGRAM_WEBHOOK_SECRET,
        # drop_pending_updates=True,     # optional
    )


if __name__ == "__main__":
    main()
