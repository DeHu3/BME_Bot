from . import db
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

async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE, cfg: Settings, state: dict):
    chat_id = update.effective_chat.id if update.effective_chat else "Unknown"
    logging.info("cmd_start called for chat_id=%s", chat_id)

    subscribed = db.is_sub("burn_subs", update.effective_chat.id)
    await update.message.reply_text(
        "Render Alerts Bot is alive. Use /help.",
        reply_markup=_kb(subscribed)
    )

async def cmd_help(update: Update, context: ContextTypes.DEFAULT_TYPE, cfg: Settings):
    await update.message.reply_text("Enable burn alerts to get notified when RENDER is burned.")

async def handle_text(update: Update, context: ContextTypes.DEFAULT_TYPE, cfg: Settings, state: dict):
    text = (update.message.text or "").strip()
    chat_id = update.effective_chat.id

    if text == BTN_ENABLE:
        db.sub_on(chat_id)
        logging.info("alerts ON for chat_id=%s", chat_id)
        await update.message.reply_text("Burn alerts enabled for this chat.", reply_markup=_kb(True))

    elif text == BTN_DISABLE:
        db.sub_off(chat_id)
        logging.info("alerts OFF for chat_id=%s", chat_id)
        await update.message.reply_text("Burn alerts disabled for this chat.", reply_markup=_kb(False))

    elif text == BTN_SETTINGS:
        await update.message.reply_text("Burn alert settings: coming soon.")
