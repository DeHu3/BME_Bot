from . import db
import os
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

    # Handlers (same logic as polling)
    app.add_handler(CommandHandler("start", lambda u, c: commands.cmd_start(u, c, cfg, state)))
    app.add_handler(CommandHandler("help",  lambda u, c: commands.cmd_help(u, c, cfg)))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND,
                    lambda u, c: commands.handle_text(u, c, cfg, state)))

    # Burn polling job (unchanged)
    async def burn_job(context: ContextTypes.DEFAULT_TYPE):
events = await sources.get_new_burns(cfg, state)
subs = db.list_subs()
if not subs:
    return
for chat_id in subs:
    await context.bot.send_message(chat_id, text)
                except Exception:
                    logging.exception("send_message failed")

    app.job_queue.run_repeating(burn_job, interval=30, first=5)

    # --- Webhook server config (Cloud Run-friendly) ---
    path = os.getenv("WEBHOOK_PATH", "/tg").lstrip("/")
    secret = os.getenv("TELEGRAM_WEBHOOK_SECRET", "") or None
    public_url = (os.getenv("WEBHOOK_URL", "").strip() or None)
    webhook_url = (public_url.rstrip("/") + "/" + path) if public_url else None
    port = int(os.getenv("PORT", "8080"))

    logging.info("Starting webhook server on 0.0.0.0:%s path=/%s webhook_url=%s", port, path, webhook_url)
    app.run_webhook(
        listen="0.0.0.0",
        port=port,
        url_path=path,
        webhook_url=webhook_url,         # if set, PTB will call setWebhook for you
        secret_token=secret,
        drop_pending_updates=True,
    )

if __name__ == "__main__":
    main()
