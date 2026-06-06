import sqlite3
from datetime import datetime, timedelta
from paths import MESSAGES_DB

DB = str(MESSAGES_DB)


def init():
    conn = sqlite3.connect(DB)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS messages (
            id         INTEGER PRIMARY KEY AUTOINCREMENT,
            timestamp  TEXT NOT NULL,
            group_name TEXT NOT NULL,
            sender     TEXT,
            message    TEXT NOT NULL,
            raw_json   TEXT,
            done       INTEGER DEFAULT 0
        )
    """)
    # Migration: add done column if it was created without it
    cols = [r[1] for r in conn.execute("PRAGMA table_info(messages)").fetchall()]
    if 'done' not in cols:
        conn.execute("ALTER TABLE messages ADD COLUMN done INTEGER DEFAULT 0")
    conn.commit()
    conn.close()


def save(group_name, sender, message):
    conn = sqlite3.connect(DB)
    conn.execute(
        "INSERT INTO messages (timestamp, group_name, sender, message, done) VALUES (?,?,?,?,0)",
        (datetime.now().isoformat(), group_name, sender, message)
    )
    conn.commit()
    conn.close()


def get_all(include_done=False):
    conn = sqlite3.connect(DB)
    if include_done:
        rows = conn.execute(
            "SELECT id, group_name, sender, message, timestamp, done "
            "FROM messages ORDER BY id DESC"
        ).fetchall()
    else:
        rows = conn.execute(
            "SELECT id, group_name, sender, message, timestamp, done "
            "FROM messages WHERE done=0 ORDER BY id DESC"
        ).fetchall()
    conn.close()
    return rows


def get_recent_pending(max_age_days=14):
    cutoff = (datetime.now() - timedelta(days=max_age_days)).isoformat()
    conn = sqlite3.connect(DB)
    rows = conn.execute(
        "SELECT id, group_name, sender, message, timestamp, done "
        "FROM messages WHERE done=0 AND timestamp >= ? ORDER BY id DESC",
        (cutoff,)
    ).fetchall()
    conn.close()
    return rows


def count_old_pending(max_age_days=14):
    cutoff = (datetime.now() - timedelta(days=max_age_days)).isoformat()
    conn = sqlite3.connect(DB)
    count = conn.execute(
        "SELECT COUNT(*) FROM messages WHERE done=0 AND timestamp < ?",
        (cutoff,)
    ).fetchone()[0]
    conn.close()
    return count


def mark_done(item_id):
    conn = sqlite3.connect(DB)
    conn.execute("UPDATE messages SET done=1 WHERE id=?", (item_id,))
    conn.commit()
    conn.close()


def delete_item(item_id):
    conn = sqlite3.connect(DB)
    conn.execute("DELETE FROM messages WHERE id=?", (item_id,))
    conn.commit()
    conn.close()


def clear_done():
    conn = sqlite3.connect(DB)
    conn.execute("DELETE FROM messages WHERE done=1")
    conn.commit()
    conn.close()
