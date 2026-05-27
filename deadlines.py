import sqlite3
from datetime import datetime

DB = "/root/student-bot/deadlines.db"


def init():
    conn = sqlite3.connect(DB)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS deadlines (
            id      INTEGER PRIMARY KEY AUTOINCREMENT,
            task    TEXT NOT NULL,
            course  TEXT NOT NULL,
            due     TEXT NOT NULL,
            status  TEXT DEFAULT 'Pending',
            source  TEXT DEFAULT 'manual',
            added   TEXT DEFAULT CURRENT_TIMESTAMP
        )
    """)
    conn.commit()
    conn.close()


def add(task, course, due, source='manual'):
    """Returns (id, 'added') or (None, 'duplicate')."""
    conn = sqlite3.connect(DB)
    exists = conn.execute(
        "SELECT id FROM deadlines WHERE LOWER(TRIM(task)) = LOWER(TRIM(?))", (task,)
    ).fetchone()
    if exists:
        conn.close()
        return None, 'duplicate'
    conn.execute(
        "INSERT INTO deadlines (task, course, due, source) VALUES (?,?,?,?)",
        (task, course, due, source)
    )
    conn.commit()
    row_id = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
    conn.close()
    return row_id, 'added'


def get_all(include_done=False):
    conn = sqlite3.connect(DB)
    if include_done:
        rows = conn.execute(
            "SELECT id, task, course, due, status FROM deadlines ORDER BY id"
        ).fetchall()
    else:
        rows = conn.execute(
            "SELECT id, task, course, due, status FROM deadlines WHERE status != 'Done' ORDER BY id"
        ).fetchall()
    conn.close()
    return rows


def mark_done(deadline_id):
    conn = sqlite3.connect(DB)
    conn.execute("UPDATE deadlines SET status='Done' WHERE id=?", (deadline_id,))
    conn.commit()
    conn.close()


def mark_pending(deadline_id):
    conn = sqlite3.connect(DB)
    conn.execute("UPDATE deadlines SET status='Pending' WHERE id=?", (deadline_id,))
    conn.commit()
    conn.close()


def delete(deadline_id):
    conn = sqlite3.connect(DB)
    conn.execute("DELETE FROM deadlines WHERE id=?", (deadline_id,))
    conn.commit()
    conn.close()


def clear_done():
    conn = sqlite3.connect(DB)
    conn.execute("DELETE FROM deadlines WHERE status='Done'")
    conn.commit()
    conn.close()


def clear_all():
    conn = sqlite3.connect(DB)
    conn.execute("DELETE FROM deadlines")
    conn.commit()
    conn.close()
