import logging
from dotenv import load_dotenv
from telegram.ext import Application, CommandHandler, MessageHandler, ContextTypes, filters
from .config import load_settings
from . import commands, sources

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
load_dotenv()

def main():
    cfg = load_settings()
    app = Application.builder().token(cfg.token).build()
    state = {"subs": set()}  # in-memory subscribers

    app.add_handler(CommandHandler("start", lambda u, c: commands.cmd_start(u, c, cfg)))
    app.add_handler(CommandHandler("help",  lambda u, c: commands.cmd_help(u, c, cfg)))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, lambda u, c: commands.handle_text(u, c, cfg, state)))

    async def burn_job(context: ContextTypes.DEFAULT_TYPE):
        if not state["subs"]: return
        events = await sources.get_new_burns(cfg, state)
        for e in events:
            amt = e.get("ui_amount")
            amt_txt = f"{amt:,.4f}" if isinstance(amt, (int, float)) else "?"
            url = f"https://solscan.io/tx/{e['sig']}"
            text = f"🔥 RENDER burn: {amt_txt}\n{url}"
            for chat_id in list(state["subs"]):
                await context.bot.send_message(chat_id, text)

    app.job_queue.run_repeating(burn_job, interval=30, first=5)
    app.run_polling()

if __name__ == "__main__":
    main()
