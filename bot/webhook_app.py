# bot/webhook_app.py
import os
import logging
from telegram.ext import Application, CommandHandler, MessageHandler, filters

# === Handlers you already have ===
# If these come from bot/commands.py, import them instead of stubs:
from .commands import cmd_start, cmd_help, handle_text
from .config import load_settings

logging.basicConfig(level=logging.INFO)
log = logging.getLogger("webhook_app")


def build_application():
    cfg = load_settings()
    token = cfg.TELEGRAM_BOT_TOKEN

    application = Application.builder().token(token).build()

    # Register your handlers (reuse what you already wired in main.py before)
    application.add_handler(CommandHandler("start", lambda u, c: cmd_start(u, c, cfg, {})))
    application.add_handler(CommandHandler("help", lambda u, c: cmd_help(u, c, cfg)))
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND,
                                           lambda u, c: handle_text(u, c, cfg, {})))

    return application, cfg


def main():
    application, cfg = build_application()

    # Cloud Run requires binding to 0.0.0.0 and the env PORT
    port = int(os.environ.get("PORT", "8080"))
    path = cfg.WEBHOOK_PATH.strip("/") if cfg.WEBHOOK_PATH else "tg"
    url  = cfg.WEBHOOK_URL.rstrip("/") + "/" + path

    log.info("Starting webhook server on 0.0.0.0:%s path=/%s url=%s", port, path, url)

    # This starts an aiohttp web server that listens on 0.0.0.0:PORT
    # and registers the webhook with Telegram.
    application.run_webhook(
        listen="0.0.0.0",
        port=port,
        url_path=path,
        webhook_url=url,
        secret_token=cfg.TELEGRAM_WEBHOOK_SECRET or None,
    )


if __name__ == "__main__":
    main()
