# bot/db.py

# Firestore async client import (new path first, then fallback for older libs)
try:
    from google.cloud import firestore  # modern path
    AsyncClient = firestore.AsyncClient  # type: ignore[attr-defined]
except Exception:
    # Fallback import path if the installed version doesn’t expose firestore.AsyncClient
    from google.cloud.firestore_v1 import AsyncClient  # type: ignore

class SubscriberDB:
    def __init__(self):
        self.db = firestore_async.Client()
        # 'subs' collection will hold documents named after list types (e.g. burn_subs, mint_subs)
        self.collection = self.db.collection('subs')

    async def get_subs(self, list_name: str) -> set[int]:
        """Fetch subscriber chat IDs for a given list."""
        doc = await self.collection.document(list_name).get()
        if doc.exists:
            return set(doc.to_dict().get('subs', []))
        return set()

    async def save_subs(self, list_name: str, subs: set[int]) -> None:
        """Persist subscriber IDs for a given list."""
        await self.collection.document(list_name).set({'subs': list(subs)})
