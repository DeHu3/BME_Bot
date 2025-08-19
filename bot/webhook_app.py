# bot/webhook_app.py
import os
import logging
import asyncio
import json
import hmac
import hashlib
from aiohttp import web
from telegram import Update
from telegram.ext import Application, CommandHandler, MessageHandler, filters

from bot.config import load_settings
from bot.commands import cmd_start, handle_text
from bot.db import SubscriberDB
from bot import sources

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s:%(name)s:%(message)s")
log = logging.getLogger("webhook_app")
logging.getLogger("httpx").setLevel(logging.WARNING)

# prevent overlapping admin runs
_RUN_LOCK = asyncio.Lock()


# --------- BOT HANDLERS / BUILD PTB APP ----------
def build_ptb_application(cfg) -> Application:
    app = Application.builder().token(cfg.TELEGRAM_BOT_TOKEN).build()
    app.add_handler(CommandHandler("start", lambda u, c: cmd_start(u, c, cfg, {})))
    app.add_handler(
        MessageHandler(filters.TEXT & ~filters.COMMAND, lambda u, c: handle_text(u, c, cfg, {}))
    )
    return app


# --------- AIOHTTP ROUTES ----------
async def handle_healthz(_request: web.Request) -> web.Response:
    return web.Response(text="ok")


async def _check_secret(req: web.Request) -> bool:
    cfg = req.app["cfg"]
    expected = (os.environ.get("CRON_SECRET") or getattr(cfg, "CRON_SECRET", "") or "").strip()
    received = (req.query.get("secret") or "").strip()
    return (not expected) or (received == expected)


# ---- Admin: reset cursor (still useful if you ever want to rescan) ----
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
        async with _RUN_LOCK:
            # Fetch recent events ignoring persisted cursor
            temp_state: dict = {}
            events = await sources.get_new_burns(cfg, temp_state, ignore_cursor=True)
            if not events:
                return web.Response(text="no recent burns")

            to_send = events[-n:]  # newest N
            db = SubscriberDB(cfg.DATABASE_URL)
            subs = await db.get_subs("burn_subs")

            sent = 0
            for ev in to_send:
                # Record (idempotent) but ALWAYS send for manual replay
                await db.record_burn(ev["signature"], ev["ts"], ev["amount"], ev.get("price_usd"))
                totals = await db.sums_24_7_30()
                text = sources.format_burn(ev, totals)

                for chat_id in subs:
                    try:
                        await ptb.bot.send_message(chat_id, text, parse_mode="HTML", disable_web_page_preview=True)
                    except Exception:
                        log.exception("send burn (replay) failed chat_id=%s", chat_id)
                sent += 1

        return web.Response(text=f"ok: sent {sent} alert(s)")
    except Exception:
        log.exception("replay failed")
        return web.Response(status=500, text="error")


# ---- Helius Enhanced Webhook endpoint (push) ----
async def handle_helius_webhook(request: web.Request) -> web.Response:
    cfg = request.app["cfg"]

    # Secret used either for HMAC validation OR header token validation.
    # Keep original precedence (cfg first, then env) but normalize quotes/whitespace.
    secret = (
        getattr(cfg, "HELIUS_WEBHOOK_SECRET", "")
        or os.environ.get("HELIUS_WEBHOOK_SECRET", "")
        or ""
    ).strip().strip('"').strip("'")

    # Safe observability (does not leak the secret value)
    log.info("helius auth enabled=%s secret_len=%d", bool(secret), len(secret))

    # Read raw body once (needed if verifying HMAC)
    try:
        raw = await request.read()
    except Exception:
        return web.Response(status=400, text="bad request")

    # Accept any of the following if a secret is configured:
    #   1) X-Helius-Signature: HMAC_SHA256(secret, raw_body)
    #   2) X-Helius-Auth: <secret>
    #   3) Authorization: Bearer <secret>
    #   4) Authorization: <secret>
    if secret:
        headers = request.headers

        # HMAC path
        sig_hdr = (headers.get("X-Helius-Signature") or "").strip().strip('"').strip("'")

        # Passthrough header path
        xauth_raw = headers.get("X-Helius-Auth") or ""
        xauth = xauth_raw.strip().strip('"').strip("'")

        # Authorization variants
        authz_raw = headers.get("Authorization") or ""
        authz_clean = authz_raw.strip().strip('"').strip("'")
        bearer = ""
        if authz_clean:
            parts = authz_clean.split(None, 1)
            if len(parts) == 2 and parts[0].lower() == "bearer":
                bearer = parts[1].strip().strip('"').strip("'")
            else:
                bearer = authz_clean  # plain token

        ok = False
        if sig_hdr:
            expected = hmac.new(secret.encode("utf-8"), raw, hashlib.sha256).hexdigest()
            ok = hmac.compare_digest(sig_hdr.lower(), expected.lower())
        if not ok and xauth:
            ok = hmac.compare_digest(xauth, secret)
        if not ok and bearer:
            ok = hmac.compare_digest(bearer, secret)

        # Non-sensitive diagnostics
        log.info(
            "helius hdrs present: authorization=%s x-auth=%s x-sig=%s (lens %d/%d/%d)",
            bool(authz_raw), bool(xauth_raw), bool(sig_hdr),
            len(authz_raw), len(xauth_raw), len(sig_hdr),
        )

        if not ok:
            log.warning("Helius webhook auth failed")
            return web.Response(status=403, text="forbidden")

    # Parse JSON after auth
    try:
        payload = json.loads(raw.decode("utf-8"))
    except Exception:
        return web.Response(status=400, text="bad json")

    try:
        # Parse → record → if new → send
        events = await sources.parse_helius_webhook(cfg, payload)
        if not events:
            return web.Response(text="ok")

        db = SubscriberDB(cfg.DATABASE_URL)
        subs = await db.get_subs("burn_subs")
        if not subs:
            # Still record; idempotent with ON CONFLICT DO NOTHING
            for ev in events:
                await db.record_burn(ev["signature"], ev["ts"], ev["amount"], ev.get("price_usd"))
            return web.Response(text="ok")

        sent = 0
        for ev in events:
            is_new = await db.record_burn(ev["signature"], ev["ts"], ev["amount"], ev.get("price_usd"))
            if not is_new:  # duplicate delivery/retry → skip notify
                continue

            totals = await db.sums_24_7_30()
            text = sources.format_burn(ev, totals)

            for chat_id in subs:
                try:
                    await request.app["ptb"].bot.send_message(
                        chat_id, text, parse_mode="HTML", disable_web_page_preview=True
                    )
                except Exception:
                    log.exception("send burn (webhook) failed chat_id=%s", chat_id)
            sent += 1

        return web.Response(text=f"ok: {sent}")
    except Exception:
        log.exception("helius webhook failed")
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

    await ptb.initialize()
    await ptb.start()

    try:
        await SubscriberDB(cfg.DATABASE_URL).ensure_schema()
    except Exception:
        log.exception("ensure_schema failed")
        has_key = bool(getattr(cfg, "HELIUS_API_KEY", ""))
        burn_addr = (getattr(cfg, "BURN_VAULT_ADDRESS", "") or getattr(cfg, "RENDER_BURN_ADDRESS", ""))
        log.info("Startup env check: helius_key_present=%s burn_vault=%s",
                 has_key, (burn_addr[:6] + "..." if burn_addr else "MISSING"))

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
        web.get("/admin/reset-burn-cursor", handle_admin_reset_cursor),
        web.get("/admin/replay", handle_admin_replay),
        web.post("/helius/webhook", handle_helius_webhook),  # webhook POST target
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
