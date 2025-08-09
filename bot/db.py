# bot/db.py

from google.cloud import firestore_async

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
