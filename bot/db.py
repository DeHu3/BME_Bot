from google.cloud import firestore

_db = firestore.Client()
_subs = _db.collection("subs")  # docs keyed by chat_id (string)

def sub_on(chat_id: int):
    _subs.document(str(chat_id)).set({"on": True}, merge=True)

def sub_off(chat_id: int):
    _subs.document(str(chat_id)).delete()

def is_sub(chat_id: int) -> bool:
    return _subs.document(str(chat_id)).get().exists

def list_subs() -> list[int]:
    return [int(d.id) for d in _subs.stream()]
