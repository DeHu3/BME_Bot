# bot/db.py

# Firestore async client import (new path first, then fallback for older libs)
try:
    from google.cloud import firestore  # modern path
    AsyncClient = firestore.AsyncClient  # type: ignore[attr-defined]
except Exception:
    # Fallback import path if the installed version doesn’t expose firestore.AsyncClient
    from google.cloud.firestore_v1 import AsyncClient  # type: ignore

class SubscriberDB:
    def __init__(self) -> None:
        # Init Firestore client for async usage
        self.db = AsyncClient()
        # 'subs' collection will hold documents named after list types (e.g. burn_subs, mint_subs)
        self.collection = self.db.collection('subs')
        self.state = self.db.collection('state')

async def get_state(self, key: str) -> dict:
    doc = await self.state.document(key).get()
    return doc.to_dict() or {}

async def save_state(self, key: str, data: dict) -> None:
    """Persist the state dict."""
    await self.state.document(key).set(dict(data or {}))

    async def get_subs(self, list_name: str) -> set[int]:
        """Fetch subscriber chat IDs for a given list."""
        doc = await self.collection.document(list_name).get()
        if doc.exists:
            return set(doc.to_dict().get('subs', []))
        return set()

    async def save_subs(self, list_name: str, subs: set[int]) -> None:
        """Persist subscriber IDs for a given list."""
        await self.collection.document(list_name).set({'subs': list(subs)})
