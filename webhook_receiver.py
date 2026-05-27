#!/usr/bin/env python3
"""
webhook_receiver.py — receives WAHA/OpenWA webhooks, filters by group/keywords, saves to SQLite.
Compatible with both old openwa/wa-automate and new WAHA-based OpenWA (ghcr.io/rmyndharis/openwa).
"""
from flask import Flask, request, jsonify
import sqlite3, json
from datetime import datetime
import os

app = Flask(__name__)
DB_PATH = os.path.expanduser('~/student-bot/messages.db')

MONITORED_GROUPS = {
    "DATABASE BO1", "Database Assignment", "Group Project OOP",
    "COOS L02", "COOS L02-B03", "PROB STAT March 2026",
    "OOSAD March 2026 MIIT", "OOSAD (friends)", "Professional English 1 L07",
    "Project Proposal PE Group 1", "LOGISTIC PE", "CSSC MIIT",
    "DSC UniKL 2526", "SEPC UniKL 2026"
}

KEYWORDS = [
    "submit", "submission", "due", "deadline", "hantar", "serah",
    "assignment", "quiz", "test", "exam", "presentation", "report",
    "esok", "esk", "tomorrow", "hari ni", "harini", "today",
    "malam ni", "tonight", "minggu ni", "this week", "by ",
    "kena", "must", "wajib", "compulsory", "jangan lupa", "urgent", "penting",
    "cancel", "reschedule", "postpone", "tangguh", "online", "zoom", "teams", "replace",
    "e-cert", "ecert", "certificate", "free", "webinar", "workshop", "competition",
    "fill in", "fill up", "isi", "form", "register", "daftar", "vote", "sign",
    "reply", "balas", "confirm", "attendance", "hadir", "sila", "tolong"
]


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


def is_relevant(text: str) -> bool:
    lower = text.lower()
    return any(kw in lower for kw in KEYWORDS)


def save_message(group_name, sender, message, raw_json=None):
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute(
        'INSERT INTO messages (timestamp,group_name,sender,message,raw_json) VALUES (?,?,?,?,?)',
        (datetime.now().isoformat(), group_name, sender, message, raw_json)
    )
    conn.commit()
    conn.close()


def parse_waha_payload(payload: dict):
    """
    Parse WAHA-format webhook (ghcr.io/rmyndharis/openwa and WAHA).
    Returns (is_group, group_name, sender, body) or None if not a valid message.
    """
    # WAHA format: {"event": "message", "session": "...", "payload": {...}}
    event = payload.get('event', '')
    if event and event != 'message':
        return None

    msg = payload.get('payload', payload)  # fallback to root if no 'payload' key

    body = msg.get('body', '').strip()
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

    return is_group, group_name, sender, body


def parse_old_openwa_payload(payload: dict):
    """
    Parse old openwa/wa-automate format.
    Returns (is_group, group_name, sender, body) or None.
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
    return True, group_name, sender, body


@app.route('/webhook', methods=['POST'])
def webhook():
    try:
        payload = request.get_json(force=True) or {}

        # Try WAHA format first, then old openwa format
        result = parse_waha_payload(payload) or parse_old_openwa_payload(payload)

        if result is None:
            return jsonify({'status': 'ignored', 'reason': 'not_group_or_no_body'}), 200

        is_group, group_name, sender, body = result

        if not is_group:
            return jsonify({'status': 'ignored', 'reason': 'not_group'}), 200

        if group_name not in MONITORED_GROUPS:
            return jsonify({'status': 'ignored', 'reason': f'group:{group_name}'}), 200

        if not is_relevant(body):
            return jsonify({'status': 'ignored', 'reason': 'no_keywords'}), 200

        save_message(group_name, sender, body, json.dumps(payload))
        print(f"[SAVED] {datetime.now().strftime('%H:%M')} | {group_name} | {sender}: {body[:80]}", flush=True)
        return jsonify({'status': 'saved'}), 200

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
