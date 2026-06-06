from datetime import datetime
import sqlite3

import requests
import config as app_config
from paths import MESSAGES_DB as MESSAGES_DB_PATH

WAHA_URL = getattr(app_config, "WAHA_URL", "http://localhost:2785").rstrip("/")
WAHA_SESSION = getattr(app_config, "WAHA_SESSION", "default")
WAHA_API_KEY = getattr(app_config, "WAHA_API_KEY", "")
WAHA_PAIR_NUMBER = getattr(app_config, "WAHA_PAIR_NUMBER", "")
WAHA_SESSION_URL = f"{WAHA_URL}/api/sessions/{WAHA_SESSION}"
WAHA_HEADERS = {"X-Api-Key": WAHA_API_KEY} if WAHA_API_KEY else {}
MESSAGES_DB = str(MESSAGES_DB_PATH)


def get_latest_message_timestamp():
    try:
        conn = sqlite3.connect(MESSAGES_DB)
        row = conn.execute("SELECT MAX(timestamp) FROM messages").fetchone()
        conn.close()
    except Exception:
        return None

    if not row or not row[0]:
        return None

    try:
        return datetime.fromisoformat(row[0])
    except ValueError:
        return None


def get_session_status():
    try:
        response = requests.get(WAHA_SESSION_URL, headers=WAHA_HEADERS, timeout=5)
        if not response.ok:
            return {"ok": False, "status": f"http_{response.status_code}"}

        data = response.json()
        return {
            "ok": True,
            "status": data.get("status", "unknown"),
            "me": data.get("me") or {},
        }
    except Exception as exc:
        return {"ok": False, "status": "unreachable", "error": str(exc)}


def build_whatsapp_warning():
    session = get_session_status()
    latest_ts = get_latest_message_timestamp()
    latest_str = latest_ts.strftime("%d %b %Y, %I:%M %p") if latest_ts else "unknown"

    if not session["ok"]:
        return (
            "⚠️ <b>WhatsApp bridge check failed.</b>\n"
            f"Last saved WA message: <b>{latest_str}</b>.\n"
            f"Use <code>/qr</code> or <code>/link {WAHA_PAIR_NUMBER or 'YOUR_PHONE_NUMBER'}</code> to reconnect if needed."
        )

    status = session["status"]
    if status == "WORKING":
        return None

    action = f"Use <code>/qr</code> to scan again or <code>/link {WAHA_PAIR_NUMBER or 'YOUR_PHONE_NUMBER'}</code> for a phone-number pairing code."
    if status == "SCAN_QR_CODE":
        headline = "⚠️ <b>WhatsApp is waiting to be linked again.</b>"
    elif status == "FAILED":
        headline = "⚠️ <b>WhatsApp bridge is disconnected.</b>"
    else:
        headline = f"⚠️ <b>WhatsApp session status: {status}.</b>"

    return (
        f"{headline}\n"
        f"Last saved WA message: <b>{latest_str}</b>.\n"
        f"{action}"
    )
