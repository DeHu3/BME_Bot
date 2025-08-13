# bot/commands.py
from __future__ import annotations

import logging
from typing import Optional

from telegram import Update
from telegram.ext import ContextTypes

from bot.db import SubscriberDB

log = logging.getLogger(__name__)

# The single list we use for burn alerts
BURN_LIST = "burn_subs"

HELP_TEXT = (
    "🔥 *BME Bot*\n\n"
    "I can notify you about burn events.\n\n"
    "*Commands*\n"
    "• /start — Subscribe to burn alerts\n"
    "• /help — Show this help\n"
    "• `subscribe` — Subscribe (same as /start)\n"
    "• `stop` or `unsubscribe` — Unsubscribe\n"
    "• `status` — Show your subscription status\n"
)

def _chat_id(update: Update) -> Optional[int]:
    if update.effective_chat:
        return update.effective_chat.id
    if update.message:
        return update.message.chat_id
    return None


async def _subscribe(chat_id: int) -> str:
    """Subscribe a chat to burn alerts."""
    db = SubscriberDB()
    try:
        await db.add_sub(BURN_LIST, chat_id)
        return "✅ Subscribed. You’ll now receive burn alerts."
    except Exception:
        log.exception("subscribe failed chat_id=%s", chat_id)
        return "⚠️ Couldn’t subscribe you right now. Please try again."


async def _unsubscribe(chat_id: int) -> str:
    """Unsubscribe a chat from burn alerts."""
    db = SubscriberDB()
    try:
        await db.remove_sub(BURN_LIST, chat_id)
        return "🛑 Unsubscribed. You will no longer receive burn alerts."
    except Exception:
        log.exception("unsubscribe failed chat_id=%s", chat_id)
        return "⚠️ Couldn’t unsubscribe you right now. Please try again."


async def cmd_start(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
    cfg=None,
    _extra=None,
) -> None:
    """Handler for /start. Subscribes the user and shows help."""
    chat_id = _chat_id(update)
    if chat_id is None:
        return
    msg = await _subscribe(chat_id)
    await context.bot.send_message(chat_id=chat_id, text=f"{msg}\n\n{HELP_TEXT}", parse_mode="Markdown")


async def cmd_help(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
    cfg=None,
    _extra=None,
) -> None:
    """Handler for /help."""
    chat_id = _chat_id(update)
    if chat_id is None:
        return
    await context.bot.send_message(chat_id=chat_id, text=HELP_TEXT, parse_mode="Markdown")


async def handle_text(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
    cfg=None,
    _extra=None,
) -> None:
    """
    Text message handler.
    Supports: subscribe, stop/unsubscribe, status.
    """
    chat_id = _chat_id(update)
    if chat_id is None or not update.effective_message or not update.effective_message.text:
        return

    text = update.effective_message.text.strip().lower()

    if text in {"stop", "/stop", "unsubscribe"}:
        msg = await _unsubscribe(chat_id)
        await context.bot.send_message(chat_id=chat_id, text=msg)
        return

    if text in {"subscribe", "start"}:
        msg = await _subscribe(chat_id)
        await context.bot.send_message(chat_id=chat_id, text=msg)
        return

    if text in {"status", "/status"}:
        try:
            db = SubscriberDB()
            subs = await db.get_subs(BURN_LIST)
            is_sub = chat_id in set(subs)
            await context.bot.send_message(
                chat_id=chat_id,
                text="📡 Status: *Subscribed* ✅" if is_sub else "📡 Status: *Not subscribed* ❌",
                parse_mode="Markdown",
            )
        except Exception:
            log.exception("status failed chat_id=%s", chat_id)
            await context.bot.send_message(chat_id=chat_id, text="⚠️ Couldn’t fetch status.")
        return

    # Default fallback → show help
    await context.bot.send_message(chat_id=chat_id, text=HELP_TEXT, parse_mode="Markdown")
