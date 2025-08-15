# bot/webhook_app.py
import os
import logging
import asyncio
from aiohttp import web
from telegram import Update
from telegram.ext import Application, CommandHandler, MessageHandler, filters

from bot.config import load_settings
from bot.commands import cmd_start, handle_text
from bot.db import SubscriberDB
from bot import sources

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s:%(name)s:%(message)s")
log = logging.getLogger("webhook_app")
# quiet noisy client logs
logging.getLogger("httpx").setLevel(logging.WARNING)

# prevent overlapping cron/admin runs
_RUN_LOCK = asyncio.Lock()


# --------- BOT HANDLERS / BUILD PTB APP ----------
def build_ptb_application(cfg) -> Application:
    app = Application.builder().token(cfg.TELEGRAM_BOT_TOKEN).build()
    # Commands
    app.add_handler(CommandHandler("start", lambda u, c: cmd_start(u, c, cfg, {})))
    # Text (buttons)
    app.add_handler(
        MessageHandler(filters.TEXT & ~filters.COMMAND, lambda u, c: handle_text(u, c, cfg, {}))
    )
    return app


# --------- BURN JOB (HTTP CRON TRIGGER USES THIS) ----------
async def run_burn_once(bot, cfg):
    """
    Pull new deposits to the configured burn address(es) and notify 'burn_subs'.
    Also record each burn to support 24h/7d/30d aggregations.
    """
    db = SubscriberDB(cfg.DATABASE_URL)

    # Load state (sources.get_new_burns mutates this dict in-place with last_sig)
    state = await db.get_state("burn")

    # Get new events since the last saved cursors; sources.py handles Helius + filtering
    events = await sources.get_new_burns(cfg, state)

    if not events:
        log.info("burn job: no new events; cursor unchanged (last_sig=%s)", state.get("last_sig"))
        return

    subs = await db.get_subs("burn_subs")
    if not subs:
        # No subscribers: advance the cursor so we don't replay the same page forever.
        await db.save_state("burn", state)
        log.info(
            "burn job: %d event(s) but 0 subscribers; saved cursor last_sig=%s",
            len(events), state.get("last_sig")
        )
        return

    # Record each event, compute rolling sums, and send alerts
    log.info("burn job: %d event(s), sending to %d subscriber(s)", len(events), len(subs))
    for ev in events:
        # Persist the event (totals are based on DB; ON CONFLICT DO NOTHING keeps totals idempotent)
        await db.record_burn(ev["signature"], ev["ts"], ev["amount"], ev.get("price_usd"))

        totals = await db.sums_24_7_30()
        text = sources.format_burn(ev, totals)

        for chat_id in subs:
            try:
                await bot.send_message(chat_id, text, disable_web_page_preview=True)
            except Exception:
                log.exception("send burn failed chat_id=%s", chat_id)

    # ✅ Only now that recording + sends are done do we advance the cursor
    await db.save_state("burn", state)
    log.info("burn job: saved cursor last_sig=%s", state.get("last_sig"))


# --------- AIOHTTP ROUTES ----------
async def handle_healthz(_request: web.Request) -> web.Response:
    return web.Response(text="ok")


async def _check_secret(req: web.Request) -> bool:
    cfg = req.app["cfg"]
    expected = (os.environ.get("CRON_SECRET") or getattr(cfg, "CRON_SECRET", "") or "").strip()
    received = (req.query.get("secret") or "").strip()
    return (not expected) or (received == expected)


async def handle_cron_burn(request: web.Request) -> web.Response:
    if not await _check_secret(request):
        return web.Response(status=403, text="forbidden")
    try:
        # prevent overlap with manual/admin triggers or slow previous run
        async with _RUN_LOCK:
            await run_burn_once(request.app["ptb"].bot, request.app["cfg"])
        return web.Response(text="ok")
    except Exception:
        log.exception("cron burn failed")
        return web.Response(status=500, text="error")


# ---- Admin: reset cursor (fixes 'stuck' cursor) ----
async def handle_admin_reset_cursor(request: web.Request) -> web.Response:
    if not await _check_secret(request):
        return web.Response(status=403, text="forbidden")
    cfg = request.app["cfg"]
    try:
        await SubscriberDB(cfg.DATABASE_URL).save_state("burn", {})
        return web.Response(text="ok: reset")
    except Exception:
        log.exception("reset cursor failed")
        return web.Response(status=500, text="error")


# ---- Admin: replay last N recent deposits (manual backfill) ----
async def handle_admin_replay(request: web.Request) -> web.Response:
    if not await _check_secret(request):
        return web.Response(status=403, text="forbidden")

    cfg = request.app["cfg"]
    ptb: Application = request.app["ptb"]
    n_str = request.query.get("n") or "1"
    try:
        n = max(1, min(50, int(n_str)))
    except Exception:
        n = 1

    try:
        # Fetch recent events ignoring the persisted cursor
        temp_state = {}
        events = await sources.get_new_burns(cfg, temp_state)
        if not events:
            return web.Response(text="no recent burns")

        to_send = events[-n:]  # newest n
        db = SubscriberDB(cfg.DATABASE_URL)
        subs = await db.get_subs("burn_subs")

        sent = 0
        for ev in to_send:
            # Record + totals + send
            await db.record_burn(ev["signature"], ev["ts"], ev["amount"], ev.get("price_usd"))
            totals = await db.sums_24_7_30()
            text = sources.format_burn(ev, totals)

            for chat_id in subs:
                try:
                    await ptb.bot.send_message(chat_id, text, disable_web_page_preview=True)
                except Exception:
                    log.exception("send burn (replay) failed chat_id=%s", chat_id)
            sent += 1

        # Advance the real cursor so cron doesn't resend these
        newest_sig = to_send[-1]["signature"]
        await db.save_state("burn", {"last_sig": newest_sig})

        return web.Response(text=f"ok: sent {sent} alert(s)")
    except Exception:
        log.exception("replay failed")
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

    # Ensure DB schema/tables exist
    try:
        await SubscriberDB(cfg.DATABASE_URL).ensure_schema()
    except Exception:
        log.exception("ensure_schema failed")
        has_key = bool(getattr(cfg, "HELIUS_API_KEY", ""))
        burn_addr = (getattr(cfg, "BURN_VAULT_ADDRESS", "") or getattr(cfg, "RENDER_BURN_ADDRESS", ""))
        log.info(
            "Startup env check: helius_key_present=%s burn_vault=%s",
            has_key,
            (burn_addr[:6] + "..." if burn_addr else "MISSING"),
        )

    # Build full webhook URL robustly (exactly one slash)
    hook_url = f"{cfg.WEBHOOK_URL.rstrip('/')}/{cfg.WEBHOOK_PATH.lstrip('/')}"
    log.info("Setting webhook_url=%s", hook_url)
    try:
        await ptb.bot.set_webhook(
            url=hook_url,
            secret_token=(getattr(cfg, "TELEGRAM_WEBHOOK_SECRET", "") or None),
            drop_pending_updates=True,
        )
    except Exception:
        log.exception("set_webhook failed")


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

    hook_path = "/" + cfg.WEBHOOK_PATH.lstrip("/")

    app.add_routes([
        web.get("/healthz", handle_healthz),
        web.get("/cron/burn", handle_cron_burn),
        web.get("/admin/reset-burn-cursor", handle_admin_reset_cursor),
        web.get("/admin/replay", handle_admin_replay),
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
