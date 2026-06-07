#!/usr/bin/env python3
import argparse
import sqlite3

from paths import MESSAGES_DB as MESSAGES_DB_PATH
from whatsapp_deadlines import sync_message


def main():
    parser = argparse.ArgumentParser(description="Reprocess saved WhatsApp messages into deadlines.")
    parser.add_argument("--messages-db", default=str(MESSAGES_DB_PATH))
    parser.add_argument("--since-days", type=int, default=30)
    parser.add_argument("--group", default=None, help="Optional case-insensitive group-name filter")
    args = parser.parse_args()

    conn = sqlite3.connect(args.messages_db)
    table_exists = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name='messages'"
    ).fetchone()
    if not table_exists:
        conn.close()
        print("reprocessed_messages=0")
        print("created_or_updated_rows=0")
        print("note=messages table not found in selected database")
        return

    query = """
        SELECT id, group_name, message, timestamp
        FROM messages
        WHERE timestamp >= datetime('now', ?)
    """
    params = (f"-{args.since_days} days",)
    if args.group:
        query += " AND lower(group_name) LIKE ?"
        params += (f"%{args.group.lower()}%",)
    query += " ORDER BY timestamp ASC, id ASC"

    rows = conn.execute(query, params).fetchall()
    conn.close()

    touched = 0
    emitted = 0
    for _, group_name, message, timestamp_iso in rows:
        touched += 1
        results = sync_message(group_name, message, timestamp_iso)
        emitted += len(results)
        if results:
            print(f"{timestamp_iso} | {group_name} | {results}")

    print(f"reprocessed_messages={touched}")
    print(f"created_or_updated_rows={emitted}")


if __name__ == "__main__":
    main()
