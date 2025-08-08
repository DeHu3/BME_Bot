# bot/db.py
from __future__ import annotations
from typing import List, Optional
import os

from google.cloud import firestore

# Reuse a single client (Cloud Run will inject credentials for the project)
_project = os.getenv("GOOGLE_CLOUD_PROJECT")  # optional; Firestore can infer
_client = firestore.Client(project=_project)

_SUBS = _client.collection("subs")  # one doc per chat_id
# If you later need cursors/last-processed ids:
_STATE = _client.collection("state").document("runtime")

def sub_on(chat_id: int) -> None:
    """Persist 'subscribed' for this chat."""
    _SUBS.document(str(chat_id)).set({"on": True}, merge=True)

def sub_off(chat_id: int) -> None:
    """Remove subscription for this chat."""
    _SUBS.document(str(chat_id)).delete()

def is_sub(chat_id: int) -> bool:
    """Return True if chat is subscribed."""
    return _SUBS.document(str(chat_id)).get().exists

def list_subs() -> List[int]:
    """Return all subscribed chat ids."""
    return [int(doc.id) for doc in _SUBS.stream()]

# Optional helpers if you want to store cursors/last seen tx, etc.
def save_cursor(key: str, value: str) -> None:
    _STATE.set({key: value}, merge=True)

def load_cursor(key: str) -> Optional[str]:
    snap = _STATE.get()
    data = snap.to_dict() or {}
    return data.get(key)
