#!/usr/bin/env python3
import json
import os
import sqlite3
from datetime import datetime

import requests
import config as app_config
from whatsapp_filters import is_relevant_message
from whatsapp_deadlines import sync_recent_messages
from paths import MESSAGES_DB as MESSAGES_DB_PATH

WAHA_URL = getattr(app_config, "WAHA_URL", os.getenv("WAHA_URL", "http://localhost:2785")).rstrip("/")
API_KEY = getattr(app_config, "WAHA_API_KEY", os.getenv("WAHA_API_KEY", ""))
SESSION = getattr(app_config, "WAHA_SESSION", os.getenv("WAHA_SESSION", "default"))
DB_PATH = str(MESSAGES_DB_PATH)
HEADERS = {"X-Api-Key": API_KEY} if API_KEY else {}
SINCE_DATE = datetime(2026, 5, 23)
MESSAGE_LIMIT = 1000

MONITORED_CHAT_IDS = getattr(app_config, "BACKFILL_MONITORED_CHAT_IDS", {})

COURSE_KEYWORDS = list(getattr(app_config, "BACKFILL_COURSE_KEYWORDS", [
    "professional english",
    "logistic pe",
    "database assignment",
    "pe group",
    "oosad",
    "coos",
    "prob stat",
]))

def normalize_ts_ms(ts_value):
    ts_ms = ts_value or 0
    if ts_ms and ts_ms < 1e10:
        ts_ms *= 1000
    return int(ts_ms)


def ts_ms_to_iso(ts_ms):
    if not ts_ms:
        return datetime.now().isoformat()
    return datetime.fromtimestamp(ts_ms / 1000).isoformat()


def save_message(conn, group_name, sender, message, timestamp_ms, raw_json=None):
    ts_iso = ts_ms_to_iso(timestamp_ms)
    existing = conn.execute(
        "SELECT id FROM messages WHERE group_name=? AND message=? AND timestamp=?",
        (group_name, message, ts_iso)
    ).fetchone()
    if existing:
        return False
    conn.execute(
        "INSERT INTO messages (timestamp, group_name, sender, message, raw_json, done) VALUES (?,?,?,?,?,0)",
        (ts_iso, group_name, sender, message, json.dumps(raw_json) if raw_json else None)
    )
    return True


def get_all_chats():
    try:
        response = requests.get(
            f"{WAHA_URL}/api/{SESSION}/chats",
            headers=HEADERS,
            params={"limit": 500},
            timeout=30,
        )
        if response.ok:
            data = response.json()
            return data if isinstance(data, list) else []
    except Exception as exc:
        print(f"  Error fetching chats: {exc}")
    return []


def fetch_group_messages(chat_id):
    url = f"{WAHA_URL}/api/{SESSION}/chats/{chat_id}/messages"
    try:
        response = requests.get(
            url,
            headers=HEADERS,
            params={"limit": MESSAGE_LIMIT, "downloadMedia": False},
            timeout=90,
        )
        if response.status_code == 404:
            return []
        if not response.ok:
            print(f"  Error {response.status_code}: {response.text[:120]}")
            return []
        data = response.json()
        return data if isinstance(data, list) else data.get("messages", [])
    except Exception as exc:
        print(f"  Exception: {exc}")
        return []


def process_messages(conn, messages, friendly_name):
    fetched = 0
    kept = 0
    saved = 0
    for msg in messages:
        body = (
            msg.get("body", "")
            or msg.get("caption", "")
            or msg.get("text", "")
            or msg.get("content", "")
            or ""
        ).strip()
        if not body:
            continue

        timestamp_ms = normalize_ts_ms(msg.get("timestamp", 0))
        if timestamp_ms:
            msg_dt = datetime.fromtimestamp(timestamp_ms / 1000)
            if msg_dt < SINCE_DATE:
                continue

        fetched += 1
        relevant = is_relevant_message(friendly_name, body)
        if relevant:
            kept += 1
        from_id = msg.get("from", "") or ""
        sender_obj = msg.get("sender", {}) or {}
        sender = (
            sender_obj.get("pushname", "")
            or sender_obj.get("name", "")
            or msg.get("notifyName", "")
            or from_id.split("@")[0]
            or "Unknown"
        )

        if save_message(conn, friendly_name, sender, body, timestamp_ms, msg):
            saved += 1

    conn.commit()
    return fetched, kept, saved


def main():
    conn = sqlite3.connect(DB_PATH)
    total_fetched = 0
    total_kept = 0
    total_saved = 0

    print("=" * 60)
    print(f"WhatsApp one-time backfill since {SINCE_DATE.date()} with limit {MESSAGE_LIMIT} per group")
    print("=" * 60)

    print("\nPhase 1: Known monitored groups")
    for chat_id, name in MONITORED_CHAT_IDS.items():
        print(f"\n[*] {name} ({chat_id})")
        messages = fetch_group_messages(chat_id)
        fetched, kept, saved = process_messages(conn, messages, name)
        total_fetched += fetched
        total_kept += kept
        total_saved += saved
        print(f"    raw returned: {len(messages)}")
        print(f"    since 2026-05-23: {fetched}")
        print(f"    relevant kept: {kept}")
        print(f"    saved: {saved}")

    print("\nPhase 2: Extra matching groups")
    all_chats = get_all_chats()
    known_ids = set(MONITORED_CHAT_IDS.keys())
    for chat in all_chats:
        chat_id = chat.get("id", {})
        if isinstance(chat_id, dict):
            chat_id = chat_id.get("_serialized", "")
        chat_name = chat.get("name", "") or ""
        if not str(chat_id).endswith("@g.us"):
            continue
        if chat_id in known_ids:
            continue
        lower_name = chat_name.lower()
        if not any(keyword in lower_name for keyword in COURSE_KEYWORDS):
            continue

        print(f"\n[*] Extra group: {chat_name} ({chat_id})")
        messages = fetch_group_messages(chat_id)
        fetched, kept, saved = process_messages(conn, messages, chat_name)
        total_fetched += fetched
        total_kept += kept
        total_saved += saved
        print(f"    raw returned: {len(messages)}")
        print(f"    since 2026-05-23: {fetched}")
        print(f"    relevant kept: {kept}")
        print(f"    saved: {saved}")

    conn.close()
    synced = sync_recent_messages(DB_PATH, since_days=14)
    print("\n" + "=" * 60)
    print(f"Backfill complete | scanned since-date msgs: {total_fetched} | relevant: {total_kept} | saved: {total_saved}")
    print(f"WhatsApp deadline sync | created/updated: {len(synced)}")


if __name__ == "__main__":
    main()
