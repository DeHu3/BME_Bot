# bot/webhook_app.py
import os
import logging
from telegram.ext import Application, CommandHandler, MessageHandler, filters

from bot.config import load_settings
from bot.commands import cmd_start, cmd_help, handle_text

logging.basicConfig(level=logging.INFO)
log = logging.getLogger("webhook_app")


async def _post_init(app: Application) -> None:
    """Set Telegram webhook after the server starts; never crash on failure."""
    cfg = load_settings()
    url_base = (getattr(cfg, "WEBHOOK_URL", "") or os.environ.get("WEBHOOK_URL", "")).strip()
    path = (getattr(cfg, "WEBHOOK_PATH", "tg") or "tg").strip("/")

    if not url_base.startswith("https://"):
        log.warning("WEBHOOK_URL missing or not https; skipping set_webhook")
        return

    secret = (getattr(cfg, "TELEGRAM_WEBHOOK_SECRET", "") or os.environ.get("TELEGRAM_WEBHOOK_SECRET", "")).strip() or None
    url = url_base.rstrip("/") + "/" + path
    try:
        await app.bot.set_webhook(url=url, secret_token=secret, drop_pending_updates=True)
        log.info("Webhook set: %s", url)
    except Exception:
        log.exception("set_webhook failed (server continues to run)")


def build_application(cfg):
    app = Application.builder().token(cfg.TELEGRAM_BOT_TOKEN).build()

    # Register the post-init hook (safe, wrapped above)
    app.post_init.append(_post_init)

    # Handlers
    app.add_handler(CommandHandler("start", lambda u, c: cmd_start(u, c, cfg, {})))
    app.add_handler(CommandHandler("help",  lambda u, c: cmd_help(u, c, cfg)))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND,
                                   lambda u, c: handle_text(u, c, cfg, {})))
    return app


def main():
    cfg = load_settings()

    token = getattr(cfg, "TELEGRAM_BOT_TOKEN", None) or os.environ.get("TELEGRAM_BOT_TOKEN")
    if not token:
        raise RuntimeError("TELEGRAM_BOT_TOKEN is not set")

    app = build_application(cfg)

    port = int(os.environ.get("PORT", "8080"))
    path = (getattr(cfg, "WEBHOOK_PATH", "tg") or "tg").strip("/")

    log.info("Starting webhook server on 0.0.0.0:%s path=/%s", port, path)
    # IMPORTANT: no extra routes here. Just run the webhook HTTP server.
    app.run_webhook(listen="0.0.0.0", port=port, url_path=path)


if __name__ == "__main__":
    main()
