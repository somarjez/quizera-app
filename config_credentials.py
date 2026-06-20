"""Firebase credential loading, isolated for testability."""
import json
import os
from firebase_admin import credentials

LOCAL_KEY_PATH = "firebase-key.json"


def load_firebase_credential():
    raw = os.environ.get("FIREBASE_CREDENTIALS")
    if raw:
        return credentials.Certificate(json.loads(raw))
    if os.path.exists(LOCAL_KEY_PATH):
        return credentials.Certificate(LOCAL_KEY_PATH)
    raise RuntimeError(
        "No Firebase credentials. Set FIREBASE_CREDENTIALS (service-account "
        "JSON string) or provide a local firebase-key.json file."
    )
