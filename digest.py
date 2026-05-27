import requests
import sqlite3
from config import BOT_TOKEN, CHAT_ID
from datetime import datetime

BASE = f"https://api.telegram.org/bot{BOT_TOKEN}"
DEADLINES_DB = "/root/student-bot/deadlines.db"


def send(text):
    requests.post(f"{BASE}/sendMessage", json={
        "chat_id": CHAT_ID,
        "text": text,
        "parse_mode": "HTML",
        "disable_web_page_preview": True
    })


def get_pending_deadlines():
    try:
        conn = sqlite3.connect(DEADLINES_DB)
        rows = conn.execute(
            "SELECT id, task, course, due FROM deadlines WHERE status != 'Done' ORDER BY id"
        ).fetchall()
        conn.close()
        return rows
    except Exception:
        return []


def get_wa_messages():
    try:
        from db import init, get_all
        init()
        return get_all(include_done=False)
    except Exception:
        return []


def send_digest():
    now = datetime.now()
    date_str = now.strftime("%a %d %b, %I:%M %p")

    lines = [f"☀️ <b>Morning Digest — {date_str}</b>\n"]

    # ── Deadline Tasks ────────────────────────────────────────
    deadlines = get_pending_deadlines()
    if deadlines:
        lines.append(f"📋 <b>Pending Tasks ({len(deadlines)})</b>")
        for id_, task, course, due in deadlines:
            lines.append(f"  {id_}. {task}\n      {course}  📅 {due}")
        lines.append("\n<i>/check ID to mark done  |  /tasks for full view</i>\n")
    else:
        lines.append("📋 <b>Tasks:</b> All clear!\n")

    # ── WhatsApp Messages ─────────────────────────────────────
    wa_items = get_wa_messages()
    if wa_items:
        grouped = {}
        for id_, group, sender, message, received, done in wa_items:
            grouped.setdefault(group, []).append((id_, message))

        lines.append(f"💬 <b>WhatsApp Messages ({len(wa_items)})</b>")
        for group, msgs in grouped.items():
            lines.append(f"\n  <b>{group}</b> ({len(msgs)})")
            for id_, msg in msgs[:2]:
                lines.append(f"  [{id_}] {msg[:90]}")
            if len(msgs) > 2:
                lines.append(f"  <i>...and {len(msgs) - 2} more</i>")
        lines.append("\n<i>/list for full WA list  |  /done ID to clear</i>")
    else:
        lines.append("💬 <b>WhatsApp:</b> Nothing new.")

    send("\n".join(lines))


if __name__ == "__main__":
    send_digest()
