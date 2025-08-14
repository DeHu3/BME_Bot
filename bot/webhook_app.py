# bot/webhook_app.py
import os
import logging
from aiohttp import web
from telegram import Update
from telegram.ext import Application, CommandHandler, MessageHandler, filters

from bot.config import load_settings
from bot.commands import cmd_start, cmd_help, handle_text
from bot.db import SubscriberDB
from bot import sources

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s:%(name)s:%(message)s")
log = logging.getLogger("webhook_app")


# --------- BOT HANDLERS / BUILD PTB APP ----------
def build_ptb_application(cfg: object) -> Application:
    app = Application.builder().token(cfg.TELEGRAM_BOT_TOKEN).build()
    # Commands
    app.add_handler(CommandHandler("start", lambda u, c: cmd_start(u, c, cfg, {})))
    app.add_handler(CommandHandler("help",  lambda u, c: cmd_help(u, c, cfg)))
    # Text
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND,
                                   lambda u, c: handle_text(u, c, cfg, {})))
    return app


# --------- BURN JOB (HTTP CRON TRIGGER USES THIS) ----------
async def run_burn_once(bot, cfg):
    """Pull new burns since last cursor and notify all 'burn_subs'."""
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
                log.exception("send burn failed chat_id=%s", chat_id)


# --------- AIOHTTP ROUTES ----------
async def handle_healthz(_request: web.Request) -> web.Response:
    return web.Response(text="ok")

async def handle_cron_burn(request: web.Request) -> web.Response:
    cfg = request.app["cfg"]
    # Accept CRON_SECRET from env or cfg (both should match what you put in Render)
    expected = (os.environ.get("CRON_SECRET") or getattr(cfg, "CRON_SECRET", "") or "").strip()
    received = (request.query.get("secret") or "").strip()
    if expected and received != expected:
        return web.Response(status=403, text="forbidden")
    try:
        await run_burn_once(request.app["ptb"].bot, cfg)
        return web.Response(text="ok")
    except Exception:
        log.exception("cron burn failed")
        return web.Response(status=500, text="error")

async def handle_telegram_webhook(request: web.Request) -> web.Response:
    cfg = request.app["cfg"]
    ptb: Application = request.app["ptb"]

    # Verify Telegram secret header if configured
    secret_hdr = request.headers.get("X-Telegram-Bot-Api-Secret-Token")
    if getattr(cfg, "TELEGRAM_WEBHOOK_SECRET", "") and secret_hdr != cfg.TELEGRAM_WEBHOOK_SECRET:
        log.warning("Webhook secret mismatch")
        return web.Response(status=403, text="forbidden")

    try:
        data = await request.json()
    except Exception:
        return web.Response(status=400, text="bad json")

    update = Update.de_json(data, ptb.bot)
    await ptb.process_update(update)
    return web.Response(text="ok")


# --------- STARTUP / CLEANUP ----------
async def on_startup(app: web.Application) -> None:
    cfg = app["cfg"]
    ptb: Application = app["ptb"]

    # Start PTB
    await ptb.initialize()
    await ptb.start()

    # Build full webhook URL robustly (exactly one slash)
    hook_url = f"{cfg.WEBHOOK_URL.rstrip('/')}/{cfg.WEBHOOK_PATH.lstrip('/')}"
    log.info("Setting webhook_url=%s", hook_url)
    await ptb.bot.set_webhook(
        url=hook_url,
        secret_token=(getattr(cfg, "TELEGRAM_WEBHOOK_SECRET", "") or None),
        drop_pending_updates=True,
    )

async def on_cleanup(app: web.Application) -> None:
    ptb: Application = app["ptb"]
    await ptb.stop()
    await ptb.shutdown()


# --------- APP FACTORY & MAIN ----------
def build_web_app() -> web.Application:
    cfg = load_settings()
    ptb = build_ptb_application(cfg)

    app = web.Application()
    app["cfg"] = cfg
    app["ptb"] = ptb

    # Normalize path for the route
    hook_path = "/" + cfg.WEBHOOK_PATH.lstrip("/")

    app.add_routes([
        web.get("/healthz", handle_healthz),
        web.get("/cron/burn", handle_cron_burn),
        web.post(hook_path, handle_telegram_webhook),
    ])
    app.on_startup.append(on_startup)
    app.on_cleanup.append(on_cleanup)
    return app


def main() -> None:
    app = build_web_app()
    cfg = app["cfg"]
    port = int(os.environ.get("PORT", getattr(cfg, "PORT", 10000)))
    log.info("Binding server on 0.0.0.0:%s path=%s", port, "/" + cfg.WEBHOOK_PATH.lstrip("/"))
    web.run_app(app, host="0.0.0.0", port=port)


if __name__ == "__main__":
    main()
