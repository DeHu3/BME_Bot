# bot/webhook_app.py
import os
import logging
from telegram.ext import Application, CommandHandler, MessageHandler, filters
from bot.commands import cmd_start, cmd_help, handle_text
from bot.config import load_settings

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
