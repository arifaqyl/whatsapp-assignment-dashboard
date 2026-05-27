import requests
import time
from db import init as init_wa, save as save_wa, get_all as get_wa, mark_done as done_wa, delete_item as del_wa, clear_done as clear_wa
from deadlines import init as init_dl, get_all as get_dl, mark_done as done_dl, mark_pending as undo_dl, delete as del_dl, clear_done as clear_dl
from config import BOT_TOKEN, CHAT_ID
from datetime import datetime

BASE = f"https://api.telegram.org/bot{BOT_TOKEN}"
last_update_id = 0


def send(text):
    requests.post(f"{BASE}/sendMessage", json={
        "chat_id": CHAT_ID,
        "text": text,
        "parse_mode": "HTML",
        "disable_web_page_preview": True
    })


def parse_automate_message(text):
    if not text.startswith("📌"):
        return None, None, None
    lines = text.split("\n\n", 1)
    group = lines[0].replace("📌", "").strip()
    msg = lines[1].strip() if len(lines) > 1 else ""
    sender = "Unknown"
    if ": " in msg:
        parts = msg.split(": ", 1)
        sender = parts[0].strip()
        msg = parts[1].strip()
    return group, sender, msg


def handle(update):
    global last_update_id
    msg = update.get("message", {})
    text = msg.get("text", "").strip()
    chat = str(msg.get("chat", {}).get("id", ""))

    if chat != str(CHAT_ID):
        return

    # ── Automate-forwarded WhatsApp message ──────────────────
    if text.startswith("📌"):
        group, sender, message = parse_automate_message(text)
        if message:
            save_wa(group, sender, message)
        return

    # ── DEADLINE TASKS ────────────────────────────────────────

    if text in ("/tasks", "/t"):
        rows = get_dl(include_done=False)
        if not rows:
            send("✅ <b>No pending tasks!</b>")
            return
        lines = [f"📋 <b>Pending Tasks ({len(rows)})</b>\n"]
        for id_, task, course, due, status in rows:
            lines.append(f"<b>{id_}.</b> {task}\n    📚 {course}  📅 {due}\n")
        lines.append("<i>/check ID — mark done  |  /todel ID — remove</i>")
        send("\n".join(lines))

    elif text == "/alltasks":
        rows = get_dl(include_done=True)
        if not rows:
            send("Database is empty.")
            return
        lines = [f"📋 <b>All Tasks ({len(rows)})</b>\n"]
        for id_, task, course, due, status in rows:
            icon = "✅" if status == "Done" else "⏳"
            lines.append(f"{icon} <b>{id_}.</b> {task}  [{course}]  {due}")
        send("\n".join(lines))

    elif text.startswith("/check"):
        parts = text.split()
        if len(parts) < 2:
            send("Usage: <code>/check 3</code> or <code>/check 3 5</code>")
            return
        ids = [int(p) for p in parts[1:] if p.isdigit()]
        for i in ids:
            done_dl(i)
        send(f"✅ Marked done: {ids}\n\n<i>Use /tasks to see remaining</i>")

    elif text.startswith("/undo"):
        parts = text.split()
        if len(parts) < 2:
            send("Usage: <code>/undo 3</code>")
            return
        i = int(parts[1]) if parts[1].isdigit() else None
        if i:
            undo_dl(i)
            send(f"↩️ Task {i} marked pending again.")

    elif text.startswith("/todel"):
        parts = text.split()
        if len(parts) < 2:
            send("Usage: <code>/todel 3</code>")
            return
        i = int(parts[1]) if parts[1].isdigit() else None
        if i:
            del_dl(i)
            send(f"🗑 Deleted task {i}.")

    elif text == "/cleardone":
        clear_dl()
        send("🧹 Cleared all completed tasks.")

    # ── WHATSAPP MESSAGES ─────────────────────────────────────

    elif text in ("/list", "/l"):
        items = get_wa(include_done=False)
        if not items:
            send("✅ <b>No pending WhatsApp messages.</b>")
            return
        lines = [f"💬 <b>WhatsApp Pending ({len(items)})</b>\n"]
        for id_, group, sender, message, received, done in items:
            lines.append(f"<b>[{id_}]</b> <b>{group}</b>\n{message[:120]}\n<i>{received}</i>\n")
        send("\n".join(lines))

    elif text == "/all":
        items = get_wa(include_done=True)
        if not items:
            send("Database is empty.")
            return
        lines = [f"💬 <b>All WhatsApp Messages ({len(items)})</b>\n"]
        for id_, group, sender, message, received, done in items:
            icon = "✅" if done else "⏳"
            lines.append(f"{icon} <b>[{id_}]</b> <b>{group}</b>: {message[:80]}")
        send("\n".join(lines))

    elif text.startswith("/done"):
        parts = text.split()
        if len(parts) < 2:
            send("Usage: <code>/done 3</code> or <code>/done 3 5 8</code>")
            return
        ids = [int(p) for p in parts[1:] if p.isdigit()]
        for i in ids:
            done_wa(i)
        send(f"✅ WA messages marked done: {ids}")

    elif text.startswith("/del"):
        parts = text.split()
        if len(parts) < 2:
            send("Usage: <code>/del 3</code>")
            return
        i = int(parts[1]) if parts[1].isdigit() else None
        if i:
            del_wa(i)
            send(f"🗑 Deleted WA message {i}.")

    elif text == "/clear":
        clear_wa()
        send("🧹 Cleared all completed WA messages.")

    elif text == "/today":
        today = datetime.now().strftime("%Y-%m-%d")
        items = get_wa(include_done=False)
        todays = [r for r in items if r[4].startswith(today)]
        if not todays:
            send("Nothing received today from WhatsApp.")
            return
        lines = [f"📅 <b>Today ({today})</b>\n"]
        for id_, group, sender, message, received, done in todays:
            lines.append(f"<b>[{id_}]</b> <b>{group}</b>\n{message[:120]}\n")
        send("\n".join(lines))

    elif text == "/qr":
        try:
            import requests as req
            # Check session status first
            sr = req.get("http://localhost:2785/api/sessions/default",
                         headers={"X-Api-Key": "dev-admin-key"}, timeout=5)
            st = sr.json().get("status", "unknown") if sr.ok else "unknown"
            if st == "WORKING":
                me = sr.json().get("me", {}) or {}
                send(f"✅ <b>WhatsApp already connected!</b>\n📱 {me.get('pushName','')}")
                return
            r = req.get("http://localhost:2785/api/default/auth/qr?format=image",
                        headers={"X-Api-Key": "dev-admin-key"}, timeout=8)
            if r.status_code == 200:
                requests.post(f"{BASE}/sendPhoto", data={
                    "chat_id": CHAT_ID,
                    "caption": (
                        "📱 <b>Option 1 — Scan QR:</b>\n"
                        "WhatsApp → Settings → Linked Devices → Link a Device\n"
                        "<i>QR expires in ~20s</i>\n\n"
                        "📲 <b>Option 2 — Pairing code (easier):</b>\n"
                        "Send: <code>/link 601XXXXXXXXX</code>\n"
                        "You'll get an 8-digit code to type instead"
                    ),
                    "parse_mode": "HTML"
                }, files={"photo": ("qr.png", r.content, "image/png")})
            else:
                send("⚠️ QR not available. Try: <code>/link 601XXXXXXXXX</code>")
        except Exception as e:
            send(f"❌ QR error: {e}")

    elif text.startswith("/link"):
        parts = text.split()
        if len(parts) < 2:
            send("Usage: <code>/link 601XXXXXXXXX</code>\n\nThen open WhatsApp → Settings → Linked Devices → Link a Device → <b>Link with phone number</b> and enter the code.")
            return
        phone = parts[1].strip().replace("+", "").replace("-", "").replace(" ", "")
        try:
            import requests as req
            r = req.post("http://localhost:2785/api/default/auth/request-code",
                         json={"phoneNumber": phone},
                         headers={"X-Api-Key": "dev-admin-key"}, timeout=20)
            if r.status_code == 200:
                code = r.json().get("code", "")
                send(
                    f"📲 <b>Pairing Code:</b>\n\n"
                    f"<code>{code}</code>\n\n"
                    f"Open WhatsApp → Settings → Linked Devices → Link a Device → "
                    f"<b>Link with phone number</b> → enter the code above."
                )
            else:
                send(f"❌ Code request failed ({r.status_code}): {r.text[:100]}\nMake sure the number is correct (e.g. 60123456789)")
        except Exception as e:
            send(f"❌ Link error: {e}")

    elif text in ("/scrape", "/vle"):
        send("🔄 <b>Scraping VLE now...</b> Results follow in ~10 min.")
        import subprocess
        subprocess.Popen(
            ["python3", "/root/student-bot/vle_scraper.py"],
            stdout=open("/tmp/scrape.log", "w"),
            stderr=subprocess.STDOUT
        )

    elif text == "/digest":
        from digest import send_digest
        send_digest()

    elif text == "/stats":
        all_wa = get_wa(include_done=True)
        pending_wa = [r for r in all_wa if r[5] == 0]
        all_dl = get_dl(include_done=True)
        pending_dl = [r for r in all_dl if r[4] != 'Done']
        send(
            f"📊 <b>Stats</b>\n\n"
            f"Deadline tasks: {len(pending_dl)} pending / {len(all_dl)} total\n"
            f"WA messages: {len(pending_wa)} pending / {len(all_wa)} total"
        )

    elif text == "/help":
        send(
            "<b>Student Bot Commands</b>\n\n"
            "<b>Deadline Tasks:</b>\n"
            "/tasks — pending task list\n"
            "/alltasks — all tasks incl. done\n"
            "/check 3 — mark task 3 done\n"
            "/check 3 5 — mark multiple done\n"
            "/undo 3 — unmark task 3\n"
            "/todel 3 — delete task 3\n"
            "/cleardone — remove completed tasks\n\n"
            "<b>WhatsApp Messages:</b>\n"
            "/list — pending WA messages\n"
            "/all — all WA messages\n"
            "/today — today's WA messages\n"
            "/done 3 — mark WA msg done\n"
            "/del 3 — delete WA msg\n"
            "/clear — remove completed WA msgs\n\n"
            "<b>VLE &amp; Scraping:</b>\n"
            "/scrape — rescan VLE now (all courses)\n"
            "/vle — same as /scrape\n\n"
            "<b>WhatsApp linking:</b>\n"
            "/qr — scan QR code to link WhatsApp\n"
            "/link 601XXXXXXXXX — pairing code (no camera needed)\n\n"
            "<b>Other:</b>\n"
            "/digest — send morning digest now\n"
            "/stats — show counts\n"
            "/help — this message"
        )


def poll():
    global last_update_id
    try:
        resp = requests.get(f"{BASE}/getUpdates", params={
            "offset": last_update_id + 1,
            "timeout": 10
        }, timeout=15)
        updates = resp.json().get("result", [])
        for u in updates:
            last_update_id = u["update_id"]
            handle(u)
    except Exception as e:
        print(f"[{datetime.now().strftime('%H:%M:%S')}] Poll error: {e}")


if __name__ == "__main__":
    init_wa()
    init_dl()
    print("Student bot running. Send /help to get started.")
    while True:
        poll()
        time.sleep(2)
