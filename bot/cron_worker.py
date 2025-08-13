# bot/cron_worker.py
import asyncio
from telegram import Bot
from bot.config import load_settings
from bot.webhook_app import run_burn_once

async def amain():
    cfg = load_settings()
    bot = Bot(token=cfg.TELEGRAM_BOT_TOKEN)
    await run_burn_once(bot, cfg)

def main():
    asyncio.run(amain())

if __name__ == "__main__":
    main()
