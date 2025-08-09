# bot/burn_job.py
import asyncio
import logging
import os
from telegram import Bot

from bot.config import load_settings
from bot.db import SubscriberDB
from bot import sources

logging.basicConfig(level=logging.INFO)
log = logging.getLogger("burn_job")


async def run_once():
    cfg = load_settings()
    token = getattr(cfg, "TELEGRAM_BOT_TOKEN", None) or os.environ["TELEGRAM_BOT_TOKEN"]
    bot = Bot(token)

    db = SubscriberDB()
    state = await db.get_state("burn")
    events = await sources.get_new_burns(cfg, state)
    await db.save_state("burn", state)

    if not events:
        log.info("no new burns")
        return

    subs = await db.get_subs("burn_subs")
    if not subs:
        log.info("no subscribers")
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


if __name__ == "__main__":
    asyncio.run(run_once())
