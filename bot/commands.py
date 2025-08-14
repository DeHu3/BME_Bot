# bot/commands.py
from __future__ import annotations
import logging
from telegram import Update, ReplyKeyboardMarkup
from telegram.ext import ContextTypes
from bot.db import SubscriberDB

BTN_ENABLE  = "Enable burn alerts 🔥"
BTN_DISABLE = "Disable burn alerts 🔕"

def _kb(on: bool) -> ReplyKeyboardMarkup:
    return ReplyKeyboardMarkup([[BTN_DISABLE if on else BTN_ENABLE]], resize_keyboard=True)

def _db(cfg) -> SubscriberDB:
    return SubscriberDB(cfg.DATABASE_URL)

async def _is_burn_sub(chat_id: int, cfg) -> bool:
    try:
        subs = await _db(cfg).get_subs("burn_subs")
        return chat_id in subs
    except Exception:
        logging.exception("is_burn_sub failed")
        return False

async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE, cfg, state):
    if not update.message:
        return
    chat_id = update.effective_chat.id
    on = await _is_burn_sub(chat_id, cfg)
    await update.message.reply_text(
        "🔥 BME Bot\n\nTap the button below to enable or disable RENDER burn alerts.",
        reply_markup=_kb(on),
        disable_web_page_preview=True,
    )

async def handle_text(update: Update, context: ContextTypes.DEFAULT_TYPE, cfg, state):
    if not update.message:
        return
    chat_id = update.effective_chat.id
    text = (update.message.text or "").strip()
    db = _db(cfg)

    if text == BTN_ENABLE:
        try:
            if hasattr(db, "add_sub"):
                await db.add_sub("burn_subs", chat_id)
            elif hasattr(db, "subscribe"):
                await db.subscribe("burn_subs", chat_id)
        except Exception:
            logging.exception("enable failed")
        await update.message.reply_text("✅ Burn alerts enabled.", reply_markup=_kb(True))
        return

    if text == BTN_DISABLE:
        try:
            if hasattr(db, "discard_sub"):
                await db.discard_sub("burn_subs", chat_id)
            elif hasattr(db, "remove_sub"):
                await db.remove_sub("burn_subs", chat_id)
        except Exception:
            logging.exception("disable failed")
        await update.message.reply_text("🚫 Burn alerts disabled.", reply_markup=_kb(False))
        return

    on = await _is_burn_sub(chat_id, cfg)
    await update.message.reply_text(
        "Use the button below to toggle burn alerts.",
        reply_markup=_kb(on),
        disable_web_page_preview=True,
    )
