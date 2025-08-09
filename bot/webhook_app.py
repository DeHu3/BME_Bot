# bot/webhook_app.py
import os
import logging
from telegram.ext import (
    Application,
    CommandHandler,
    MessageHandler,
    filters,
    ContextTypes,
)

from bot.config import load_settings
from bot.commands import cmd_start, cmd_help, handle_text
from bot.db import SubscriberDB
from bot import sources


logging.basicConfig(level=logging.INFO)
log = logging.getLogger("webhook_app")


async def run_burn_once(bot, cfg):
    """
    Pull new burns since last run and notify all subscribers of 'burn_subs'.
    We persist/restore the 'state' (cursor) in Firestore so cron runs are stateless.
    """
    db = SubscriberDB()

    # Load last cursor (persisted state)
    state = await db.get_state("burn")

    # Fetch new burn events (uses your existing sources.get_new_burns API)
    events = await sources.get_new_burns(cfg, state)

    # Persist updated cursor/state for next run
    await db.save_state("burn", state)

    if not events:
        return

    subs = await db.get_subs("burn_subs")
    if not subs:
        return

    def _fmt(ev):
        try:
            return sources.format_burn(ev)  # if your sources module provides this
        except Exception:
            return f"🔥 Burn event:\n{ev}"

    for ev in events:
        text = _fmt(ev)
        for chat_id in subs:
            try:
                await bot.send_message(chat_id, text, disable_web_page_preview=True)
            except Exception:
                log.exception("send burn failed chat_id=%s", chat_id)


# ---------- JobQueue task (runs every minute) ----------

async def cron_burn_job(context: ContextTypes.DEFAULT_TYPE):
    """Periodic job: check for new burns and notify subs."""
    try:
        cfg = context.job.data["cfg"]  # we pass cfg via job data
        await run_burn_once(context.bot, cfg)
    except Exception:
        log.exception("cron burn job failed")


# ---------- PTB app wiring ----------

async def _post_init(app: Application) -> None:
    """
    Called by PTB after the HTTP server is up. We set the webhook and
    schedule the periodic burn job here.
    """
    cfg = load_settings()

    # set webhook AFTER the server is bound; if it fails, we log but keep serving
    try:
        url = cfg.WEBHOOK_URL.rstrip("/") + "/" + (cfg.WEBHOOK_PATH or "tg").strip("/")
        secret = (cfg.TELEGRAM_WEBHOOK_SECRET or "").strip() or None
        await app.bot.set_webhook(url=url, secret_token=secret, drop_pending_updates=True)
        log.info("Webhook set: %s", url)
    except Exception:
        log.exception("set_webhook failed (server still running)")

    # Schedule periodic burn checks: every 60s, first run after ~15s
    app.job_queue.run_repeating(
        cron_burn_job,
        interval=60,
        first=15,
        name="burn_job",
        data={"cfg": cfg},
    )
    log.info("Scheduled burn_job every 60s")


def build_application(cfg):
    """Create the PTB Application and register handlers."""
    application = (
        Application.builder()
        .token(cfg.TELEGRAM_BOT_TOKEN)
        .post_init(_post_init)  # register post_init
        .build()
    )

    # command & text handlers
    application.add_handler(CommandHandler("start", lambda u, c: cmd_start(u, c, cfg, {})))
    application.add_handler(CommandHandler("help",  lambda u, c: cmd_help(u, c, cfg)))
    application.add_handler(
        MessageHandler(filters.TEXT & ~filters.COMMAND, lambda u, c: handle_text(u, c, cfg, {}))
    )
    return application


def main():
    cfg = load_settings()
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
        # webhook URL is set in _post_init()
    )


if __name__ == "__main__":
    main()
