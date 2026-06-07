import sqlite3
from paths import DEADLINES_DB
from deadline_utils import (
    choose_better_source,
    choose_better_task,
    is_generic_due,
    parse_due_date,
    task_category,
    should_replace_due,
    tasks_match,
)

DB = str(DEADLINES_DB)


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
    """Returns (id, 'added'|'updated') or (None, 'duplicate')."""
    conn = sqlite3.connect(DB)
    rows = conn.execute(
        "SELECT id, task, due, source FROM deadlines WHERE course = ? AND status != 'Done'",
        (course,)
    ).fetchall()
    for row_id, existing_task, existing_due, existing_source in rows:
        if not tasks_match(existing_task, task):
            continue
        next_task = choose_better_task(existing_task, task)
        next_due = existing_due
        if should_replace_due(existing_due, due, existing_source, source):
            next_due = due
        else:
            existing_due_date = parse_due_date(existing_due)
            new_due_date = parse_due_date(due)
            if (
                existing_source in {"whatsapp", "whatsapp-reschedule"}
                and source in {"whatsapp", "whatsapp-reschedule"}
                and task_category(existing_task) == "exam"
                and new_due_date < existing_due_date
            ):
                next_due = due
        next_source = choose_better_source(existing_source, source)
        changed = (next_task != existing_task) or (next_due != existing_due) or (next_source != existing_source)
        if changed:
            conn.execute(
                "UPDATE deadlines SET task=?, due=?, source=? WHERE id=?",
                (next_task, next_due, next_source, row_id)
            )
            conn.commit()
            conn.close()
            return row_id, 'updated'
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


def cancel(task, course, due=None):
    conn = sqlite3.connect(DB)
    rows = conn.execute(
        "SELECT id, task, due FROM deadlines WHERE course = ? AND status != 'Done'",
        (course,)
    ).fetchall()
    removed = []
    for row_id, existing_task, existing_due in rows:
        if not tasks_match(existing_task, task):
            continue
        if due and existing_due and not is_generic_due(existing_due):
            if parse_due_date(existing_due) != parse_due_date(due):
                continue
        conn.execute("DELETE FROM deadlines WHERE id=?", (row_id,))
        removed.append(row_id)
    if removed:
        conn.commit()
    conn.close()
    return removed


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
    rows.sort(key=lambda r: parse_due_date(r[3]))
    return rows


def _parse_due(due_str):
    return parse_due_date(due_str)


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
