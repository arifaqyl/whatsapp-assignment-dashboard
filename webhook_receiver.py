#!/usr/bin/env python3
"""
webhook_receiver.py — receives WAHA/OpenWA webhooks, filters by group/keywords, saves to SQLite.
Compatible with both old openwa/wa-automate and new WAHA-based OpenWA (ghcr.io/rmyndharis/openwa).
"""
from flask import Flask, request, jsonify
import sqlite3, json
from datetime import datetime
import config as app_config
from paths import MESSAGES_DB
from whatsapp_filters import is_relevant_message
from whatsapp_deadlines import sync_message

app = Flask(__name__)
DB_PATH = str(MESSAGES_DB)

MONITORED_GROUP_ALIASES = tuple(getattr(app_config, "WHATSAPP_MONITORED_GROUP_ALIASES", (
    "database", "oop", "coos", "prob stat", "oosad",
    "professional english", "logistic pe", "project proposal pe",
    "project group", "class group", "course group"
)))

def init_db():
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute('''CREATE TABLE IF NOT EXISTS messages (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        timestamp TEXT NOT NULL,
        group_name TEXT NOT NULL,
        sender TEXT,
        message TEXT NOT NULL,
        raw_json TEXT,
        done     INTEGER DEFAULT 0
    )''')
    # Migration: add done column if missing
    c.execute("PRAGMA table_info(messages)")
    if "done" not in [r[1] for r in c.fetchall()]:
        c.execute("ALTER TABLE messages ADD COLUMN done INTEGER DEFAULT 0")
    conn.commit()
    conn.close()


def normalize_ts_ms(ts_value):
    ts_ms = ts_value or 0
    if ts_ms and ts_ms < 1e10:
        ts_ms *= 1000
    return int(ts_ms)


def ts_ms_to_iso(ts_ms):
    if not ts_ms:
        return datetime.now().isoformat()
    return datetime.fromtimestamp(ts_ms / 1000).isoformat()


def save_message(group_name, sender, message, timestamp_ms=0, raw_json=None):
    ts_iso = ts_ms_to_iso(timestamp_ms)
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    existing = c.execute(
        "SELECT id FROM messages WHERE group_name=? AND message=? AND timestamp=?",
        (group_name, message, ts_iso)
    ).fetchone()
    if existing:
        conn.close()
        return False
    c.execute(
        'INSERT INTO messages (timestamp,group_name,sender,message,raw_json) VALUES (?,?,?,?,?)',
        (ts_iso, group_name, sender, message, raw_json)
    )
    conn.commit()
    conn.close()
    return True


def parse_waha_payload(payload: dict):
    """
    Parse WAHA-format webhook (ghcr.io/rmyndharis/openwa and WAHA).
    Returns (is_group, group_name, sender, body, timestamp_ms) or None if not a valid message.
    """
    # WAHA format: {"event": "message", "session": "...", "payload": {...}}
    event = payload.get('event', '')
    if event and event != 'message':
        return None

    msg = payload.get('payload', payload)  # fallback to root if no 'payload' key

    body = _extract_message_text(msg).strip()
    if not body:
        return None

    from_id = msg.get('from', '')
    # Group messages end in @g.us
    is_group = from_id.endswith('@g.us')

    if not is_group:
        # Also check old format
        if not msg.get('isGroupMsg', False):
            return None

    # Get group name from various possible locations
    data = msg.get('_data', {})
    chat = data.get('chat', {}) if isinstance(data, dict) else {}
    group_name = (
        chat.get('name', '')
        or msg.get('chatName', '')
        or msg.get('chat', {}).get('name', '')
        or ''
    )

    # Get sender pushname
    sender = (
        msg.get('_data', {}).get('notifyName', '')
        if isinstance(msg.get('_data'), dict) else ''
    ) or msg.get('sender', {}).get('pushname', '') or from_id.split('@')[0]

    timestamp_ms = normalize_ts_ms(msg.get('timestamp') or data.get('t') or 0)
    return is_group, group_name, sender, body, timestamp_ms


def _extract_message_text(msg: dict):
    if not isinstance(msg, dict):
        return ""

    candidates = [
        msg.get("body"),
        msg.get("caption"),
        msg.get("text"),
        msg.get("content"),
    ]
    data = msg.get("_data", {})
    if isinstance(data, dict):
        candidates.extend([
            data.get("body"),
            data.get("caption"),
            data.get("text"),
        ])
        quoted = data.get("quotedMsg") or {}
        if isinstance(quoted, dict):
            candidates.extend([quoted.get("body"), quoted.get("caption")])

    for value in candidates:
        if isinstance(value, str) and value.strip():
            return value
    return ""


def is_monitored_group(group_name: str):
    lower = (group_name or "").strip().lower()
    if not lower:
        return False
    return any(alias in lower for alias in MONITORED_GROUP_ALIASES)


def parse_old_openwa_payload(payload: dict):
    """
    Parse old openwa/wa-automate format.
    Returns (is_group, group_name, sender, body, timestamp_ms) or None.
    """
    event = payload.get('data', payload)
    if not event.get('isGroupMsg', False):
        return None
    body = event.get('body', '').strip()
    if not body:
        return None
    chat = event.get('chat', {})
    group_name = chat.get('name', '') or event.get('chatName', '') or ''
    sender_info = event.get('sender', {})
    sender = sender_info.get('pushname', '') or sender_info.get('id', 'Unknown')
    timestamp_ms = normalize_ts_ms(event.get('timestamp') or event.get('t') or 0)
    return True, group_name, sender, body, timestamp_ms


@app.route('/webhook', methods=['POST'])
def webhook():
    try:
        payload = request.get_json(force=True) or {}

        # Try WAHA format first, then old openwa format
        result = parse_waha_payload(payload) or parse_old_openwa_payload(payload)

        if result is None:
            return jsonify({'status': 'ignored', 'reason': 'not_group_or_no_body'}), 200

        is_group, group_name, sender, body, timestamp_ms = result

        if not is_group:
            return jsonify({'status': 'ignored', 'reason': 'not_group'}), 200

        if not is_monitored_group(group_name):
            return jsonify({'status': 'ignored', 'reason': f'group:{group_name}'}), 200

        saved = save_message(group_name, sender, body, timestamp_ms, json.dumps(payload))
        if not saved:
            return jsonify({'status': 'ignored', 'reason': 'duplicate'}), 200

        promoted = []
        sync_error = None
        if is_relevant_message(group_name, body):
            try:
                promoted = sync_message(group_name, body, ts_ms_to_iso(timestamp_ms))
            except Exception as exc:
                sync_error = str(exc)
                print(f"[SYNC ERROR] {group_name} | {sender}: {exc}", flush=True)
        print(f"[SAVED] {datetime.now().strftime('%H:%M')} | {group_name} | {sender}: {body[:80]}", flush=True)
        result = {'status': 'saved', 'promoted': len(promoted)}
        if sync_error:
            result['sync_error'] = sync_error
        return jsonify(result), 200

    except Exception as e:
        print(f"[ERROR] {e}", flush=True)
        return jsonify({'status': 'error', 'message': str(e)}), 500


@app.route('/health', methods=['GET'])
def health():
    return jsonify({'status': 'ok', 'db': DB_PATH}), 200


if __name__ == '__main__':
    init_db()
    print("[webhook_receiver] Listening on port 8085...", flush=True)
    app.run(host='0.0.0.0', port=8085, debug=False)
