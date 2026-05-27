import sqlite3
import re
from datetime import datetime, date

DB = "/root/student-bot/deadlines.db"


def _parse_due(due_str):
    """Parse due date string for sorting. Unparseable -> pushed to end."""
    if not due_str:
        return date(9999, 12, 31)
    s = due_str.strip()
    
    # Extract clean date pattern if present
    m = re.search(r'(\d{1,2}\s+[A-Za-z]{3,9}\s+\d{2,4})', s)
    if m:
        s = m.group(1)
    else:
        m = re.search(r'(\d{1,2}/\d{1,2}/\d{2,4})', s)
        if m:
            s = m.group(1)
        else:
            m = re.search(r'(\d{4}-\d{2}-\d{2})', s)
            if m:
                s = m.group(1)
                
    for fmt in ('%d %b %Y', '%d %B %Y', '%d %b %y', '%d %B %y', '%d/%m/%y', '%d/%m/%Y', '%Y-%m-%d'):
        try:
            return datetime.strptime(s, fmt).date()
        except ValueError:
            pass
            
    su = due_str.upper()
    if 'VLE' in su or 'CLP' in su or 'TBD' in su or 'N/A' in su or 'SEE' in su:
        return date(9999, 12, 31)
        
    return date(9999, 12, 30)


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
            "SELECT id, task, course, due, status FROM deadlines"
        ).fetchall()
    else:
        rows = conn.execute(
            "SELECT id, task, course, due, status FROM deadlines WHERE status != 'Done'"
        ).fetchall()
    conn.close()
    rows.sort(key=lambda r: _parse_due(r[3]))
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
