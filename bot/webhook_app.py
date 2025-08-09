# bot/webhook_app.py
import os
import logging

# We import aiohttp.web lazily inside _post_init so that the module import
# itself can never crash the process if aiohttp isn't present.
from telegram.ext import Application, CommandHandler, MessageHandler, filters

from bot.config import load_settings
from bot.commands import cmd_start, cmd_help, handle_text
from bot.db import SubscriberDB
from bot import sources

# ---------- logging ----------
logging.basicConfig(level=logging.INFO)
log = logging.getLogger("webhook_app")


# ---------- burn job ----------
async def run_burn_once(bot, cfg):
    """
    Pull new burns since last run and notify all subscribers of 'burn_subs'.
    Cursor/state is persisted via SubscriberDB so cron runs are stateless.
    """
    db = SubscriberDB()
    state = await db.get_state("burn")               # restore last cursor
    events = await sources.get_new_burns(cfg, state) # fetch new events
    await db.save_state("burn", state)               # persist cursor for next run

    if not events:
        return

    subs = await db.get_subs("burn_subs")
    if not subs:
        return

    def _fmt(ev):
        try:
            return sources.format_burn(ev)  # pretty if available
        except Exception:
            return f"🔥 Burn event:\n{ev}"

    for ev in events:
        text = _fmt(ev)
        for chat_id in subs:
            try:
                await bot.send_message(chat_id, text, disable_web_page_preview=True)
            except Exception:
                log.exception("Failed to send burn alert to chat_id=%s", chat_id)


# ---------- post_init (runs after server binds) ----------
async def _post_init(app: Application) -> None:
    """
    Set Telegram webhook and (when supported) attach /cron/burn route
    to the PTB aiohttp application. MUST NEVER raise.
    """
    try:
        cfg = load_settings()

        # 1) Set Telegram webhook (safe to skip if you prefer manual setup)
        url = cfg.WEBHOOK_URL.rstrip("/") + "/" + (cfg.WEBHOOK_PATH or "tg").strip("/")
        secret = (cfg.TELEGRAM_WEBHOOK_SECRET or "").strip() or None
        try:
            await app.bot.set_webhook(url=url, secret_token=secret, drop_pending_updates=True)
            log.info("Webhook set: %s", url)
        except Exception:
            log.exception("set_webhook failed (server will continue to run)")

        # 2) If PTB exposes an aiohttp app, register / and /cron/burn routes
        web_app = getattr(app, "web_app", None)
        if web_app:
            try:
                from aiohttp import web

                async def health(_request: "web.Request"):
                    return web.Response(text="ok")

                async def cron_burn(request: "web.Request"):
                    expected = os.environ.get("CRON_SECRET", "")
                    if expected and request.query.get("secret") != expected:
                        return web.Response(status=401, text="unauthorized")
                    try:
                        await run_burn_once(app.bot, cfg)
                        return web.Response(text="ok")
                    except Exception:
                        log.exception("cron burn failed")
                        return web.Response(status=500, text="error")

                web_app.add_routes([
                    web.get("/", health),               # quick readiness probe
                    web.get("/cron/burn", cron_burn),   # scheduler target
                ])
                log.info("Registered / and /cron/burn routes")
            except Exception:
                # Never let route registration kill the process
                log.exception("Failed to register aiohttp routes; continuing")
        else:
            # Still fine; the bot will serve updates, just no /cron/burn endpoint.
            log.warning("PTB Application.web_app not available; skipping route registration")

    except Exception:
        # Last‑chance guard: post_init must NEVER bubble an exception.
        log.exception("post_init crashed; ignoring so container can start")


# ---------- application factory ----------
def build_application(cfg):
    """
    Build the PTB Application and register handlers.
    Works on PTB 20.x (no builder.post_init) and PTB 21.x+ (has builder.post_init).
    """
    builder = Application.builder().token(cfg.TELEGRAM_BOT_TOKEN)

    # Build with maximum compatibility:
    app = None
    try:
        # PTB ≥ 21.x supports .post_init() on the builder
        app = builder.post_init(_post_init).build()
    except Exception:
        # Older PTB: build first, then attach if the list is present
        app = builder.build()
        try:
            hook = getattr(app, "post_init", None)
            if hook and hasattr(hook, "append"):
                hook.append(_post_init)
            else:
                log.warning("PTB post_init hook not available; skipping webhook auto-setup")
        except Exception:
            log.exception("Could not attach post_init; continuing")

    # Handlers
    app.add_handler(CommandHandler("start", lambda u, c: cmd_start(u, c, cfg, {})))
    app.add_handler(CommandHandler("help",  lambda u, c: cmd_help(u, c, cfg)))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND,
                                   lambda u, c: handle_text(u, c, cfg, {})))
    return app


# ---------- entrypoint ----------
def main():
    cfg = load_settings()
    if not cfg.TELEGRAM_BOT_TOKEN:
        raise RuntimeError("TELEGRAM_BOT_TOKEN missing in configuration")

    application = build_application(cfg)
    port = int(os.environ.get("PORT", "8080"))
    path = (cfg.WEBHOOK_PATH or "tg").strip("/")

    # Bind server. This returns only when the process is stopping.
    application.run_webhook(
        listen="0.0.0.0",
        port=port,
        url_path=path,
    )


if __name__ == "__main__":
    main()
