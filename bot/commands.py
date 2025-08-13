# bot/commands.py
from __future__ import annotations
import logging
from typing import Optional
from telegram import Update
from telegram.ext import ContextTypes

log = logging.getLogger(__name__)
BURN_LIST = "burn_subs"  # topic name used across app

def _chat_id(update: Update) -> Optional[int]:
    return update.effective_chat.id if update.effective_chat else None

HELP_TEXT = (
    "🔥 *BME Bot*\n\n"
    "I notify you about burn events.\n\n"
    "*Commands*\n"
    "• /start — Subscribe to burn alerts\n"
    "• /help — Show help\n"
    "• `status` — Show your subscription status\n"
    "• `stop` / `unsubscribe` — Unsubscribe\n"
)

async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE, cfg=None, _extra=None) -> None:
    from bot.db import SubscriberDB
    from bot.config import load_settings
    chat_id = _chat_id(update)
    if chat_id is None:
        return
    db = SubscriberDB(load_settings().DATABASE_URL)
    await db.add_sub(BURN_LIST, chat_id)
    await context.bot.send_message(chat_id, "✅ Subscribed to 🔥 burn alerts.\n\n" + HELP_TEXT, parse_mode="Markdown")

async def cmd_help(update: Update, context: ContextTypes.DEFAULT_TYPE, cfg=None, _extra=None) -> None:
    chat_id = _chat_id(update)
    if chat_id is None:
        return
    await context.bot.send_message(chat_id, HELP_TEXT, parse_mode="Markdown")

async def handle_text(update: Update, context: ContextTypes.DEFAULT_TYPE, cfg=None, _extra=None) -> None:
    from bot.db import SubscriberDB
    from bot.config import load_settings
    chat_id = _chat_id(update)
    if chat_id is None or not update.effective_message or not update.effective_message.text:
        return

    db = SubscriberDB(load_settings().DATABASE_URL)
    text = update.effective_message.text.strip().lower()

    if text in {"stop", "/stop", "unsubscribe"}:
        await db.remove_sub(BURN_LIST, chat_id)
        await context.bot.send_message(chat_id, "🛑 Unsubscribed. Send /start to subscribe again.")
        return

    if text in {"status", "/status"}:
        try:
            subs = set(await db.get_subs(BURN_LIST))
            is_sub = chat_id in subs
            await context.bot.send_message(
                chat_id,
                "📡 Status: *Subscribed* ✅" if is_sub else "📡 Status: *Not subscribed* ❌",
                parse_mode="Markdown",
            )
        except Exception:
            log.exception("status failed chat_id=%s", chat_id)
            await context.bot.send_message(chat_id, "⚠️ Couldn’t fetch status.")
        return

    if text in {"subscribe", "start"}:
        await db.add_sub(BURN_LIST, chat_id)
        await context.bot.send_message(chat_id, "✅ Subscribed to 🔥 burn alerts.")
        return

    # Fallback: show help
    await context.bot.send_message(chat_id, HELP_TEXT, parse_mode="Markdown")
