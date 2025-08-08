import logging
logging.basicConfig(level=logging.INFO)
from telegram import Update, ReplyKeyboardMarkup
from telegram.ext import ContextTypes
from .config import Settings

BTN_ENABLE  = "Enable burn alerts 🔥"
BTN_DISABLE = "Disable burn alerts 🔕"
BTN_SETTINGS = "Burn alert settings ⚙️"

def _kb(subscribed: bool):
    return ReplyKeyboardMarkup(
        [[BTN_DISABLE if subscribed else BTN_ENABLE], [BTN_SETTINGS]],
        resize_keyboard=True
    )

import logging

async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE, cfg: Settings, state: dict):
    # Log that /start was called and by whom
    chat_id = update.effective_chat.id if update.effective_chat else "Unknown"
    logging.info("cmd_start called for chat_id=%s", chat_id)

    # Send the start message
    await update.message.reply_text(
        "Render Alerts Bot is alive. Use /help."
    )


async def cmd_help(update: Update, context: ContextTypes.DEFAULT_TYPE, cfg: Settings):
    await update.message.reply_text("Enable burn alerts to get notified when RENDER is burned.")

async def handle_text(update: Update, context: ContextTypes.DEFAULT_TYPE, cfg: Settings, state: dict):
    text = (update.message.text or "").strip()
    chat_id = update.effective_chat.id
    subs = state.setdefault("subs", set())

    if text == BTN_ENABLE:
        subs.add(chat_id)
        await update.message.reply_text("Burn alerts enabled for this chat.", reply_markup=_kb(True))
    elif text == BTN_DISABLE:
        subs.discard(chat_id)
        await update.message.reply_text("Burn alerts disabled for this chat.", reply_markup=_kb(False))
    elif text == BTN_SETTINGS:
        await update.message.reply_text("Burn alert settings: coming soon.")
