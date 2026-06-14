import sqlite3
from datetime import datetime, timedelta
from paths import MESSAGES_DB

DB = str(MESSAGES_DB)


def _connect():
    conn = sqlite3.connect(DB)
    conn.row_factory = sqlite3.Row
    return conn


def init():
    conn = _connect()
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
    conn.execute("""
        CREATE TABLE IF NOT EXISTS evidence_queue (
            id               INTEGER PRIMARY KEY AUTOINCREMENT,
            source_type      TEXT NOT NULL,
            source_row_id    INTEGER,
            group_name       TEXT,
            course           TEXT,
            title            TEXT,
            message          TEXT,
            reason_code      TEXT NOT NULL,
            evidence_preview TEXT,
            proposed_task    TEXT,
            proposed_due     TEXT,
            status           TEXT NOT NULL DEFAULT 'pending',
            created_at       TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            updated_at       TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            last_error       TEXT
        )
    """)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS operator_actions (
            id            INTEGER PRIMARY KEY AUTOINCREMENT,
            queue_item_id INTEGER NOT NULL,
            action_type   TEXT NOT NULL,
            actor         TEXT NOT NULL DEFAULT 'system',
            action_payload TEXT,
            created_at    TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
        )
    """)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS system_health (
            component       TEXT PRIMARY KEY,
            last_status     TEXT NOT NULL,
            last_success_at TEXT,
            last_failure_at TEXT,
            details         TEXT,
            updated_at      TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
        )
    """)
    conn.commit()
    conn.close()


def save(group_name, sender, message):
    conn = _connect()
    conn.execute(
        "INSERT INTO messages (timestamp, group_name, sender, message, done) VALUES (?,?,?,?,0)",
        (datetime.now().isoformat(), group_name, sender, message)
    )
    conn.commit()
    conn.close()


def get_all(include_done=False):
    conn = _connect()
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
    conn = _connect()
    rows = conn.execute(
        "SELECT id, group_name, sender, message, timestamp, done "
        "FROM messages WHERE done=0 AND timestamp >= ? ORDER BY id DESC",
        (cutoff,)
    ).fetchall()
    conn.close()
    return rows


def count_old_pending(max_age_days=14):
    cutoff = (datetime.now() - timedelta(days=max_age_days)).isoformat()
    conn = _connect()
    count = conn.execute(
        "SELECT COUNT(*) FROM messages WHERE done=0 AND timestamp < ?",
        (cutoff,)
    ).fetchone()[0]
    conn.close()
    return count


def mark_done(item_id):
    conn = _connect()
    conn.execute("UPDATE messages SET done=1 WHERE id=?", (item_id,))
    conn.commit()
    conn.close()


def delete_item(item_id):
    conn = _connect()
    conn.execute("DELETE FROM messages WHERE id=?", (item_id,))
    conn.commit()
    conn.close()


def clear_done():
    conn = _connect()
    conn.execute("DELETE FROM messages WHERE done=1")
    conn.commit()
    conn.close()


def enqueue_evidence_item(
    *,
    source_type,
    source_row_id=None,
    group_name=None,
    course=None,
    title=None,
    message=None,
    reason_code,
    evidence_preview=None,
    proposed_task=None,
    proposed_due=None,
    last_error=None,
):
    conn = _connect()
    if source_row_id is None:
        existing = conn.execute(
            """
            SELECT id FROM evidence_queue
            WHERE source_type = ?
              AND reason_code = ?
              AND COALESCE(title, '') = COALESCE(?, '')
              AND status = 'pending'
            ORDER BY id DESC
            LIMIT 1
            """,
            (source_type, reason_code, title),
        ).fetchone()
    else:
        existing = conn.execute(
            """
            SELECT id FROM evidence_queue
            WHERE source_type = ?
              AND source_row_id = ?
              AND reason_code = ?
              AND status = 'pending'
            ORDER BY id DESC
            LIMIT 1
            """,
            (source_type, source_row_id, reason_code),
        ).fetchone()
    if existing:
        conn.execute(
            """
            UPDATE evidence_queue
            SET group_name = ?, course = ?, title = ?, message = ?, evidence_preview = ?,
                proposed_task = ?, proposed_due = ?, last_error = ?, updated_at = CURRENT_TIMESTAMP
            WHERE id = ?
            """,
            (
                group_name,
                course,
                title,
                message,
                evidence_preview,
                proposed_task,
                proposed_due,
                last_error,
                existing["id"],
            ),
        )
        conn.commit()
        conn.close()
        return existing["id"], "updated"

    cur = conn.execute(
        """
        INSERT INTO evidence_queue (
            source_type, source_row_id, group_name, course, title, message,
            reason_code, evidence_preview, proposed_task, proposed_due, last_error
        ) VALUES (?,?,?,?,?,?,?,?,?,?,?)
        """,
        (
            source_type,
            source_row_id,
            group_name,
            course,
            title,
            message,
            reason_code,
            evidence_preview,
            proposed_task,
            proposed_due,
            last_error,
        ),
    )
    conn.commit()
    row_id = cur.lastrowid
    conn.close()
    return row_id, "added"


def get_queue_items(status="pending", source_type=None, reason_code=None, limit=200, offset=0):
    conn = _connect()
    sql = """
        SELECT id, source_type, source_row_id, group_name, course, title, message,
               reason_code, evidence_preview, proposed_task, proposed_due,
               status, created_at, updated_at, last_error
        FROM evidence_queue
        WHERE 1=1
    """
    params = []
    if status:
        sql += " AND status = ?"
        params.append(status)
    if source_type:
        sql += " AND source_type = ?"
        params.append(source_type)
    if reason_code:
        sql += " AND reason_code = ?"
        params.append(reason_code)
    sql += " ORDER BY updated_at DESC, id DESC LIMIT ? OFFSET ?"
    params.extend([limit, offset])
    rows = [dict(row) for row in conn.execute(sql, params).fetchall()]
    conn.close()
    return rows


def count_queue_items(status="pending", source_type=None, reason_code=None):
    conn = _connect()
    sql = """
        SELECT COUNT(*) AS count
        FROM evidence_queue
        WHERE 1=1
    """
    params = []
    if status:
        sql += " AND status = ?"
        params.append(status)
    if source_type:
        sql += " AND source_type = ?"
        params.append(source_type)
    if reason_code:
        sql += " AND reason_code = ?"
        params.append(reason_code)
    count = conn.execute(sql, params).fetchone()["count"]
    conn.close()
    return count


def get_queue_item(queue_item_id):
    conn = _connect()
    row = conn.execute(
        """
        SELECT id, source_type, source_row_id, group_name, course, title, message,
               reason_code, evidence_preview, proposed_task, proposed_due,
               status, created_at, updated_at, last_error
        FROM evidence_queue
        WHERE id = ?
        """,
        (queue_item_id,),
    ).fetchone()
    conn.close()
    return dict(row) if row else None


def update_queue_item(
    queue_item_id,
    *,
    status=None,
    proposed_task=None,
    proposed_due=None,
    last_error=None,
    reason_code=None,
):
    current = get_queue_item(queue_item_id)
    if not current:
        return False
    conn = _connect()
    conn.execute(
        """
        UPDATE evidence_queue
        SET status = ?, proposed_task = ?, proposed_due = ?, last_error = ?,
            reason_code = ?, updated_at = CURRENT_TIMESTAMP
        WHERE id = ?
        """,
        (
            status if status is not None else current["status"],
            proposed_task if proposed_task is not None else current["proposed_task"],
            proposed_due if proposed_due is not None else current["proposed_due"],
            last_error if last_error is not None else current["last_error"],
            reason_code if reason_code is not None else current["reason_code"],
            queue_item_id,
        ),
    )
    conn.commit()
    conn.close()
    return True


def get_queue_counts():
    conn = _connect()
    rows = conn.execute(
        """
        SELECT source_type, reason_code, status, COUNT(*) AS count
        FROM evidence_queue
        GROUP BY source_type, reason_code, status
        ORDER BY source_type, reason_code, status
        """
    ).fetchall()
    conn.close()
    return [dict(row) for row in rows]


def record_operator_action(queue_item_id, action_type, actor="system", action_payload=None):
    conn = _connect()
    conn.execute(
        """
        INSERT INTO operator_actions (queue_item_id, action_type, actor, action_payload)
        VALUES (?,?,?,?)
        """,
        (queue_item_id, action_type, actor, action_payload),
    )
    conn.commit()
    conn.close()


def get_recent_operator_actions(limit=30):
    conn = _connect()
    rows = conn.execute(
        """
        SELECT id, queue_item_id, action_type, actor, action_payload, created_at
        FROM operator_actions
        ORDER BY id DESC
        LIMIT ?
        """,
        (limit,),
    ).fetchall()
    conn.close()
    return [dict(row) for row in rows]


def get_operator_actions_for_item(queue_item_id, limit=50):
    conn = _connect()
    rows = conn.execute(
        """
        SELECT id, queue_item_id, action_type, actor, action_payload, created_at
        FROM operator_actions
        WHERE queue_item_id = ?
        ORDER BY id DESC
        LIMIT ?
        """,
        (queue_item_id, limit),
    ).fetchall()
    conn.close()
    return [dict(row) for row in rows]


def record_system_health(component, status, details=None):
    now = datetime.now().isoformat()
    conn = _connect()
    existing = conn.execute(
        "SELECT component, last_success_at, last_failure_at FROM system_health WHERE component = ?",
        (component,),
    ).fetchone()
    last_success_at = existing["last_success_at"] if existing else None
    last_failure_at = existing["last_failure_at"] if existing else None
    if status == "ok":
        last_success_at = now
    elif status == "error":
        last_failure_at = now
    conn.execute(
        """
        INSERT INTO system_health (
            component, last_status, last_success_at, last_failure_at, details, updated_at
        ) VALUES (?,?,?,?,?,?)
        ON CONFLICT(component) DO UPDATE SET
            last_status=excluded.last_status,
            last_success_at=excluded.last_success_at,
            last_failure_at=excluded.last_failure_at,
            details=excluded.details,
            updated_at=excluded.updated_at
        """,
        (component, status, last_success_at, last_failure_at, details, now),
    )
    conn.commit()
    conn.close()


def get_system_health():
    conn = _connect()
    rows = conn.execute(
        """
        SELECT component, last_status, last_success_at, last_failure_at, details, updated_at
        FROM system_health
        ORDER BY component
        """
    ).fetchall()
    conn.close()
    return [dict(row) for row in rows]


def get_message_row(message_id):
    conn = _connect()
    row = conn.execute(
        """
        SELECT id, timestamp, group_name, sender, message, raw_json, done
        FROM messages
        WHERE id = ?
        """,
        (message_id,),
    ).fetchone()
    conn.close()
    return dict(row) if row else None
