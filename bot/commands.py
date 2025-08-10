# bot/commands.py
from __future__ import annotations

from functools import partial

from telegram import Update
from telegram.ext import ContextTypes
from bot.db import SubscriberDB


# tag constants
BURN_TAG = "burns"


async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE, *, db: SubscriberDB) -> None:
    """Subscribe the user to burn alerts by default."""
    chat = update.effective_chat
    await db.ensure_schema()
    await db.add_sub(chat.id, BURN_TAG)
    await update.message.reply_text(
        "👋 Hey! You’re now subscribed to 🔥 burn alerts.\n\n"
        "Commands:\n"
        "• /subscribe – subscribe to burn alerts\n"
        "• /unsubscribe – stop burn alerts\n"
        "• /help – help\n"
    )


async def cmd_help(update: Update, context: ContextTypes.DEFAULT_TYPE, *, db: SubscriberDB) -> None:
    await update.message.reply_text(
        "This bot sends 🔥 burn alerts.\n"
        "Use /subscribe to receive them, /unsubscribe to stop."
    )


async def cmd_subscribe(update: Update, context: ContextTypes.DEFAULT_TYPE, *, db: SubscriberDB) -> None:
    chat = update.effective_chat
    await db.add_sub(chat.id, BURN_TAG)
    await update.message.reply_text("✅ Subscribed to 🔥 burn alerts.")


async def cmd_unsubscribe(update: Update, context: ContextTypes.DEFAULT_TYPE, *, db: SubscriberDB) -> None:
    chat = update.effective_chat
    await db.del_sub(chat.id, BURN_TAG)
    await update.message.reply_text("🛑 Unsubscribed from 🔥 burn alerts.")


async def handle_text(update: Update, context: ContextTypes.DEFAULT_TYPE, *, db: SubscriberDB) -> None:
    # Keep this as a passthrough in case you want text behaviors later.
    return  # no-op for now


# helpers to bind handlers in webhook_app
def bind(fn, *, db: SubscriberDB):
    return partial(fn, db=db)
