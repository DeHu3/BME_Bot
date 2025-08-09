# bot/webhook_app.py
import os
import logging
from telegram.ext import Application, CommandHandler, MessageHandler, filters

from bot.commands import cmd_start, cmd_help, handle_text
from bot.config import load_settings

logging.basicConfig(level=logging.INFO)
log = logging.getLogger("webhook_app")

def build_application():
    cfg = load_settings()

    # Hard fail if the token is not provided, so we don't keep rebooting silently.
    token = getattr(cfg, "TELEGRAM_BOT_TOKEN", None)
    if not token:
        raise RuntimeError(
            "Missing TELEGRAM_BOT_TOKEN. Set it in Cloud Run → "
            "Service → Edit & deploy new revision → Variables & Secrets."
        )

    app = Application.builder().token(token).build()

    # Handlers
    app.add_handler(CommandHandler("start", lambda u, c: cmd_start(u, c, cfg, {})))
    app.add_handler(CommandHandler("help",  lambda u, c: cmd_help(u, c, cfg)))
    app.add_handler(
        MessageHandler(filters.TEXT & ~filters.COMMAND, lambda u, c: handle_text(u, c, cfg, {}))
    )
    return app, cfg

def main():
    app, cfg = build_application()

    port = int(os.environ.get("PORT", "8080"))
    path = (cfg.WEBHOOK_PATH or "tg").strip("/")
    url  = cfg.WEBHOOK_URL.rstrip("/") + "/" + path
    secret = (cfg.TELEGRAM_WEBHOOK_SECRET or "").strip() or None

    log.info("Starting webhook: listen=0.0.0.0:%s path=/%s url=%s", port, path, url)

    # Set the webhook via run_webhook to avoid post_init incompatibilities
    app.run_webhook(
        listen="0.0.0.0",
        port=port,
        url_path=path,
        webhook_url=url,
        secret_token=secret,
        drop_pending_updates=True,
    )

if __name__ == "__main__":
    main()
