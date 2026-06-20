import os
from dotenv import load_dotenv
import firebase_admin
from firebase_admin import firestore

from config_credentials import load_firebase_credential
from quota_guard import QuotaConfig, UsageCounter, CountingFirestore

load_dotenv()


class Config:
    SECRET_KEY = os.environ.get("SECRET_KEY") \
        or os.environ.get("FLASK_SECRET_KEY") \
        or "dev-only-insecure-key"

    # Email configuration
    MAIL_SERVER = "smtp.gmail.com"
    MAIL_PORT = 587
    MAIL_USERNAME = os.environ.get("MAIL_USERNAME")
    MAIL_PASSWORD = os.environ.get("MAIL_PASSWORD")
    MAIL_USE_TLS = True
    MAIL_USE_SSL = False
    MAIL_DEFAULT_SENDER = os.environ.get("MAIL_USERNAME")
    MAIL_MAX_EMAILS = None
    MAIL_SUPPRESS_SEND = False  # Set to True in testing
    MAIL_ASCII_ATTACHMENTS = False


def init_firebase():
    if not firebase_admin._apps:
        firebase_admin.initialize_app(load_firebase_credential())
    return firestore.client()


_raw_db = init_firebase()
_usage_counter = UsageCounter(QuotaConfig(
    state_path=os.environ.get("QUOTA_STATE_PATH", "quota_state.json")
))
# `db` is a transparent counting proxy: existing call sites are unchanged.
db = CountingFirestore(_raw_db, _usage_counter)
