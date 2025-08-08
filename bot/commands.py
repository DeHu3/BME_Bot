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
    chat_id = update.effective_chat.id
    subscribed = chat_id in state.get("subs", set())
    await update.message.reply_text("Render Alerts Bot is alive. Use /help.", reply_markup=_kb(subscribed))

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
