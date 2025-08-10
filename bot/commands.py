# bot/commands.py
from telegram import Update
from telegram.ext import ContextTypes


async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Subscribe the chat to burn alerts."""
    db = context.application.bot_data["db"]
    chat_id = update.effective_chat.id
    await db.add_sub("burn_subs", chat_id)
    await context.bot.send_message(
        chat_id,
        "✅ Subscribed to burn alerts.\nSend 'stop' to unsubscribe, or /help for help."
    )


async def cmd_help(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await context.bot.send_message(
        update.effective_chat.id,
        "Commands:\n/start – subscribe to burn alerts\n/help – this help\n"
        "Send 'stop' to unsubscribe."
    )


async def handle_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = (update.message.text or "").strip().lower()
    db = context.application.bot_data["db"]
    chat_id = update.effective_chat.id
    if text in {"stop", "unsubscribe", "unsub"}:
        await db.remove_sub("burn_subs", chat_id)
        await context.bot.send_message(chat_id, "🛑 Unsubscribed. Send /start to subscribe again.")
