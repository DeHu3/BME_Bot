# bot/webhook_app.py
from __future__ import annotations

import os
import asyncio
import logging
from aiohttp import web
from telegram.ext import Application, CommandHandler, MessageHandler, filters

from bot.config import load_settings
from bot.db import SubscriberDB
from bot.commands import cmd_start, cmd_help, handle_text

logging.basicConfig(level=logging.INFO)
log = logging.getLogger("webhook_app")


# -------- burn job reused by cron worker --------
async def run_burn_once(bot, cfg):
    from bot import sources  # your burn fetch/format module

    db = SubscriberDB(cfg.DATABASE_URL)
    await db.ensure_schema()  # idempotent

    state = await db.get_state("burn")
    events = await sources.get_new_burns(cfg, state)  # must mutate 'state' if you use cursors
    await db.save_state("burn", state)

    if not events:
        return

    subs = await db.get_subs("burn_subs")
    if not subs:
        return

    def fmt(ev):
        try:
            return sources.format_burn(ev)
        except Exception:
            return f"🔥 Burn event:\n{ev}"

    for ev in events:
        text = fmt(ev)
        for chat_id in subs:
            try:
                await bot.send_message(chat_id, text, disable_web_page_preview=True)
            except Exception:
                log.exception("send burn failed chat_id=%s", chat_id)


# -------- aiohttp + PTB webhook server with health route --------
async def _build_ptb_app(cfg):
    app = Application.builder().token(cfg.TELEGRAM_BOT_TOKEN).build()
    app.add_handler(CommandHandler("start", lambda u, c: cmd_start(u, c, cfg, {})))
    app.add_handler(CommandHandler("help",  lambda u, c: cmd_help(u, c, cfg)))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND,
                                   lambda u, c: handle_text(u, c, cfg, {})))
    return app


async def amain():
    cfg = load_settings()

    # Ensure DB schema early (safe & idempotent)
    db = SubscriberDB(cfg.DATABASE_URL)
    await db.ensure_schema()

    # Build PTB app now
    ptb_app = await _build_ptb_app(cfg)

    # Prepare webhook route (doesn't require Telegram set yet)
    path = (cfg.WEBHOOK_PATH or "tg").strip("/")
    secret = (cfg.TELEGRAM_WEBHOOK_SECRET or "").strip() or None

    # Prefer PTB helper if available
    if hasattr(ptb_app, "get_webhook_app"):
        aio = ptb_app.get_webhook_app(path=f"/{path}", secret_token=secret)
    else:
        # Manual fallback
        aio = web.Application()
        await ptb_app.initialize()
        await ptb_app.start()
        aio.add_routes([web.post(f"/{path}", ptb_app.webhook_handler)])
        async def on_cleanup(_):
            await ptb_app.stop()
            await ptb_app.shutdown()
        aio.on_cleanup.append(on_cleanup)

    # Health endpoints for Render
    async def health(_: web.Request):
        return web.Response(text="ok")
    aio.add_routes([web.get("/healthz", health), web.get("/", health)])

    # ---- Bind HTTP server FIRST so Render sees an open port ----
    port = int(os.environ.get("PORT", "10000"))
    runner = web.AppRunner(aio)
    await runner.setup()
    site = web.TCPSite(runner, "0.0.0.0", port)
    await site.start()
    log.info("Listening on 0.0.0.0:%s (health: /healthz, webhook: /%s)", port, path)

    # ---- THEN set Telegram webhook (non-blocking for Render health) ----
    base = (cfg.WEBHOOK_URL or os.getenv("RENDER_EXTERNAL_URL") or "").rstrip("/")
    if base and base.startswith("https://"):
        full_url = f"{base}/{path}"
        try:
            await ptb_app.bot.set_webhook(url=full_url, secret_token=secret, drop_pending_updates=True)
            log.info("Webhook set: %s", full_url)
        except Exception:
            log.exception("set_webhook failed")
    else:
        log.warning("WEBHOOK_URL missing or not https; skipping set_webhook. Service will still pass health checks.")

    # keep running
    await asyncio.Event().wait()


def main():
    asyncio.run(amain())


if __name__ == "__main__":
    main()
