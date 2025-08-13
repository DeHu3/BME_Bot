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
    await db.ensure_schema()  # safe to call; no-op after first time

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
async def _build_ptb_app(cfg) -> Application:
    app = Application.builder().token(cfg.TELEGRAM_BOT_TOKEN).build()
    app.add_handler(CommandHandler("start", lambda u, c: cmd_start(u, c, cfg, {})))
    app.add_handler(CommandHandler("help",  lambda u, c: cmd_help(u, c, cfg)))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND,
                                   lambda u, c: handle_text(u, c, cfg, {})))
    return app


async def amain():
    cfg = load_settings()

    # Ensure DB schema early
    db = SubscriberDB(cfg.DATABASE_URL)
    await db.ensure_schema()

    # Build PTB app
    ptb_app = await _build_ptb_app(cfg)

    # Build & validate HTTPS webhook URL
    base = (cfg.WEBHOOK_URL or os.getenv("RENDER_EXTERNAL_URL") or "").rstrip("/")
    path = (cfg.WEBHOOK_PATH or "tg").strip("/")
    if not base.startswith("https://"):
        raise RuntimeError(
            "WEBHOOK_URL missing or not https. Set WEBHOOK_URL to your Render URL, e.g. https://<name>.onrender.com"
        )
    full_url = f"{base}/{path}"
    secret = (cfg.TELEGRAM_WEBHOOK_SECRET or "").strip() or None

    # Set Telegram webhook explicitly (https)
    await ptb_app.bot.set_webhook(url=full_url, secret_token=secret, drop_pending_updates=True)
    log.info("Using webhook_url=%s", full_url)

    # --- Build aiohttp server that serves both the webhook and health checks ---
    # Prefer built-in helper if available (PTB >= 20.4), else fall back to manual route.
    aio: web.Application
    if hasattr(ptb_app, "get_webhook_app"):
        aio = ptb_app.get_webhook_app(path=f"/{path}", secret_token=secret)
    else:
        # Fallback: manually wire the webhook handler and manage app lifecycle
        aio = web.Application()

        # PTB lifecycle when embedding in a custom server:
        await ptb_app.initialize()
        await ptb_app.start()

        # Route for Telegram updates (POST)
        aio.add_routes([web.post(f"/{path}", ptb_app.webhook_handler)])

        async def on_cleanup(_):
            # Graceful shutdown for PTB
            await ptb_app.stop()
            await ptb_app.shutdown()

        aio.on_cleanup.append(on_cleanup)

    # Health endpoints for Render
    async def health(_: web.Request):
        return web.Response(text="ok")

    aio.add_routes([web.get("/", health), web.get("/healthz", health)])

    # Bind server
    port = int(os.environ.get("PORT", "10000"))
    runner = web.AppRunner(aio)
    await runner.setup()
    site = web.TCPSite(runner, "0.0.0.0", port)
    log.info("Binding server on 0.0.0.0:%s path=/%s", port, path)
    await site.start()

    # keep running
    await asyncio.Event().wait()


def main():
    asyncio.run(amain())


if __name__ == "__main__":
    main()
