import requests
import time
import threading
import subprocess
import config as app_config
from db import init as init_wa, save as save_wa, get_all as get_wa, get_recent_pending as get_recent_wa, count_old_pending as count_old_wa, mark_done as done_wa, delete_item as del_wa, clear_done as clear_wa
from deadlines import init as init_dl, get_all as get_dl, mark_done as done_dl, mark_pending as undo_dl, delete as del_dl, clear_done as clear_dl, _parse_due
from config import BOT_TOKEN, CHAT_ID
from datetime import datetime, date
from vle_login import login_state, start_login_thread
from get_session import probe_login_flow, probe_saved_session
from waha_status import build_whatsapp_warning, get_session_status
from deadline_utils import has_concrete_due
from paths import CONFIG_FILE, ROOT, SCRAPE_LOG

BASE = f"https://api.telegram.org/bot{BOT_TOKEN}"
WAHA_URL = getattr(app_config, "WAHA_URL", "http://localhost:2785").rstrip("/")
WAHA_SESSION = getattr(app_config, "WAHA_SESSION", "default")
WAHA_API_KEY = getattr(app_config, "WAHA_API_KEY", "")
WAHA_HEADERS = {"X-Api-Key": WAHA_API_KEY} if WAHA_API_KEY else {}
WAHA_PAIR_NUMBER = getattr(app_config, "WAHA_PAIR_NUMBER", "")
last_update_id = 0


def send(text):
    limit = 3500
    chunks = []
    current = []
    current_len = 0
    for line in text.splitlines():
        line_len = len(line) + 1
        if current and current_len + line_len > limit:
            chunks.append("\n".join(current))
            current = [line]
            current_len = line_len
        else:
            current.append(line)
            current_len += line_len
    if current:
        chunks.append("\n".join(current))
    if not chunks:
        chunks = [text]
    for chunk in chunks:
        requests.post(f"{BASE}/sendMessage", json={
            "chat_id": CHAT_ID,
            "text": chunk,
            "parse_mode": "HTML",
            "disable_web_page_preview": True
        })


def _urgency(due_str):
    d = _parse_due(due_str)
    today = date.today()
    if d >= date(9999, 12, 30):
        return "📌"
    days = (d - today).days
    if days < 0:
        return "🔴 OVERDUE"
    elif days == 0:
        return "🔴 TODAY"
    elif days <= 2:
        return f"🔴 {days}d"
    elif days <= 7:
        return f"🟡 {days}d"
    else:
        return f"🟢 {days}d"


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

    # Normalize click-to-check command links, e.g. /check_213_261 -> /check 213 261
    if text.startswith("/check_"):
        text = "/check " + text[7:].replace("_", " ")

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
        
        # Deduplicate tasks locally in command view too!
        from gemini_dashboard import deduplicate_tasks
        rows_deduped = deduplicate_tasks(rows)
        today = date.today()
        active_rows = []
        hidden_overdue = 0
        hidden_nodate = 0
        for id_str, course, task, due, source in rows_deduped:
            if not has_concrete_due(due):
                hidden_nodate += 1
                continue
            parsed_due = _parse_due(due)
            if parsed_due < today:
                hidden_overdue += 1
                continue
            active_rows.append((id_str, course, task, due, source))

        if not active_rows:
            msg = "✅ <b>No active pending tasks!</b>"
            if hidden_overdue:
                msg += f"\n\n<i>{hidden_overdue} overdue item(s) hidden from /tasks.</i>"
            if hidden_nodate:
                msg += f"\n<i>{hidden_nodate} undated placeholder item(s) hidden from /tasks.</i>"
            send(msg)
            return
        
        lines = [f"📋 <b>Pending Tasks ({len(active_rows)})</b>\n"]
        for id_str, course, task, due, source in active_rows:
            urg = _urgency(due)
            cmd_str = id_str.replace(",", "_")
            lines.append(f"• [{course}] {task} — {due} {urg} /check_{cmd_str}")
        if hidden_overdue:
            lines.append(f"\n<i>{hidden_overdue} overdue item(s) hidden from /tasks.</i>")
        if hidden_nodate:
            lines.append(f"<i>{hidden_nodate} undated placeholder item(s) hidden from /tasks.</i>")
        lines.append("\n<i>✅ Tap /check_ID next to a task to mark it done.</i>")
        send("\n".join(lines))

    elif text == "/alltasks":
        rows = get_dl(include_done=True)
        if not rows:
            send("Database is empty.")
            return
        lines = [f"📋 <b>All Tasks ({len(rows)})</b>\n"]
        for id_, task, course, due, status in rows:
            icon = "✅" if status == "Done" else _urgency(due)
            lines.append(f"{icon} <b>{id_}.</b> {task}  [{course}]  {due}")
        send("\n".join(lines))

    elif text.startswith("/check"):
        parts = text.split()
        if len(parts) < 2:
            send("Usage: <code>/check 3</code> or <code>/check 3 5</code>")
            return
        # Support comma-separated or space-separated IDs
        raw_ids = []
        for p in parts[1:]:
            raw_ids.extend(p.replace(',', ' ').split())
        ids = [int(p) for p in raw_ids if p.isdigit()]
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

    elif text in ("/clear done", "/clear tasks", "/taskclear"):
        clear_dl()
        send("🧹 Cleared all completed tasks.")

    # ── WHATSAPP MESSAGES ─────────────────────────────────────

    elif text in ("/list", "/l"):
        import re as _re
        from whatsapp_filters import is_relevant_message
        items = get_recent_wa(max_age_days=14)
        hidden_old = count_old_wa(max_age_days=14)
        warning = build_whatsapp_warning()
        if not items:
            if warning:
                send(f"{warning}\n\n✅ <b>No pending WA alerts in the database.</b>")
            elif hidden_old:
                send(f"✅ <b>No recent WA alerts.</b>\n\n<i>{hidden_old} older pending message(s) hidden from /list. Use /all to inspect them.</i>")
            else:
                send("✅ <b>No new alerts.</b>")
            return

        def _is_academic_msg(group, t):
            raw = t
            t = t.lower()
            g = (group or "").lower()
            strong = ["deadline","due date","due:","submit by","kena hantar","kena submit",
                      "no class","cancel class","class cancel","replace class","replacement class",
                      "reschedule","postpone","tangguh kelas","quiz","final exam","mid term",
                      "midterm","project brief","project due","assignment due"]
            if any(s in t for s in strong): return True
            has_sub = any(w in t for w in ["submit","hantar","serah","submission"])
            has_asgn = "assignment" in t
            has_date = bool(_re.search(r'\b(monday|tuesday|wednesday|thursday|friday|saturday|sunday|isnin|selasa|rabu|khamis|jumaat|sabtu|ahad|\d+\s*(jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec|june|july)|week\s*\d+|minggu\s*\d+|esok|tomorrow|malam ni|tonight|hari ni)\b', t))
            has_due = bool(_re.search(r'\b(due|before |sebelum )\b', t))
            if any(hint in g for hint in ["project proposal pe group", "professional english", "logistic pe"]):
                has_pe_context = any(word in t for word in ["event", "activity", "school", "sekolah", "visit", "bus", "proposal", "meeting", "report", "video"])
                if has_date or has_pe_context:
                    return True
            if is_relevant_message(group, raw) and not _is_ecert_msg(raw):
                return True
            return (has_sub or has_asgn) and (has_date or has_due)

        def _is_ecert_msg(t):
            t = t.lower()
            has_cert = any(w in t for w in ["e-cert","ecert","certificate","sijil"])
            has_free = any(w in t for w in ["free","percuma"])
            has_event = any(w in t for w in ["webinar","workshop","competition","hackathon","seminar"])
            return (has_cert and (has_free or has_event)) or (has_free and any(w in t for w in ["competition","hackathon","contest"]))

        academic = [(id_, g, msg, recv) for id_, g, s, msg, recv, d in items if _is_academic_msg(g, msg)]
        ecerts   = [(id_, g, msg, recv) for id_, g, s, msg, recv, d in items if not _is_academic_msg(g, msg) and _is_ecert_msg(msg)]

        lines = [warning, ""] if warning else []
        if academic:
            lines.append(f"📚 <b>Academic Alerts ({len(academic)})</b>")
            char_count = 30
            for id_, group, msg, recv in academic:
                block = f"\n<b>[{id_}]</b> <b>{group}</b>\n{msg[:150]}\n"
                if char_count + len(block) > 3500:
                    lines.append(f"<i>...+{len(academic)} more — use /listgroup to view all</i>")
                    break
                lines.append(block)
                char_count += len(block)

        if ecerts:
            lines.append(f"\n🏆 <b>Free E-cert / Events ({len(ecerts)})</b>")
            for id_, group, msg, recv in ecerts[:5]:
                lines.append(f"\n<b>[{id_}]</b> <b>{group}</b>\n{msg[:150]}\n")
            if len(ecerts) > 5:
                lines.append(f"<i>...+{len(ecerts)-5} more</i>")

        if not academic and not ecerts:
            if hidden_old:
                send(f"✅ <b>No recent academic alerts or e-cert opportunities.</b>\n\n<i>{hidden_old} older pending message(s) hidden from /list. Use /all to inspect them.</i>")
            else:
                send("✅ <b>No academic alerts or e-cert opportunities.</b>")
            return

        if hidden_old:
            lines.append(f"<i>{hidden_old} older pending message(s) hidden from /list. Use /all to inspect them.</i>\n")
        lines.append("\n<i>/done ID — dismiss  |  /listgroup NAME — full group msgs</i>")
        send("\n".join(lines))

    elif text == "/all":
        items = get_wa(include_done=True)
        if not items:
            send("Database is empty.")
            return
        pending = sum(1 for r in items if r[5] == 0)
        done_count = len(items) - pending
        grouped = {}
        for id_, group, sender, message, received, done in items:
            grouped.setdefault(group, {'pending': 0, 'done': 0})
            if done:
                grouped[group]['done'] += 1
            else:
                grouped[group]['pending'] += 1
        lines = [f"💬 <b>All WA Messages: {len(items)} total ({pending} pending, {done_count} done)</b>\n"]
        for group, counts in grouped.items():
            lines.append(f"  <b>{group}</b>: {counts['pending']} pending, {counts['done']} done")
        lines.append("\n<i>/list — show pending  |  /clear — remove done</i>")
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
            warning = build_whatsapp_warning()
            if warning:
                send(f"{warning}\n\nNothing received today from WhatsApp.")
            else:
                send("Nothing received today from WhatsApp.")
            return
        warning = build_whatsapp_warning()
        lines = [warning, ""] if warning else []
        lines.append(f"📅 <b>Today ({today})</b>\n")
        for id_, group, sender, message, received, done in todays:
            lines.append(f"<b>[{id_}]</b> <b>{group}</b>\n{message[:120]}\n")
        send("\n".join(lines))

    elif text == "/qr":
        try:
            import requests as req
            sr = req.get(f"{WAHA_URL}/api/sessions/{WAHA_SESSION}",
                         headers=WAHA_HEADERS, timeout=5)
            st = sr.json().get("status", "unknown") if sr.ok else "unknown"
            if st == "WORKING":
                me = sr.json().get("me", {}) or {}
                send(f"✅ <b>WhatsApp already connected!</b>\n📱 {me.get('pushName','')}")
                return
            r = req.get(f"{WAHA_URL}/api/{WAHA_SESSION}/auth/qr?format=image",
                         headers=WAHA_HEADERS, timeout=8)
            if r.status_code == 200:
                requests.post(f"{BASE}/sendPhoto", data={
                    "chat_id": CHAT_ID,
                    "caption": (
                        "📱 <b>Option 1 — Scan QR:</b>\n"
                        "WhatsApp → Settings → Linked Devices → Link a Device\n"
                        "<i>QR expires in ~20s</i>\n\n"
                        "📲 <b>Option 2 — Pairing code (easier):</b>\n"
                        f"Send: <code>/link {WAHA_PAIR_NUMBER or 'YOUR_PHONE_NUMBER'}</code>"
                    ),
                    "parse_mode": "HTML"
                }, files={"photo": ("qr.png", r.content, "image/png")})
            else:
                send(f"⚠️ QR not available. Try: <code>/link {WAHA_PAIR_NUMBER or 'YOUR_PHONE_NUMBER'}</code>")
        except Exception as e:
            send(f"❌ QR error: {e}")

    elif text.startswith("/link"):
        parts = text.split()
        if len(parts) < 2:
            send("Usage: <code>/link YOUR_PHONE_NUMBER</code>")
            return
        phone = parts[1].strip().replace("+", "").replace("-", "").replace(" ", "")
        try:
            import requests as req
            session_resp = req.get(
                f"{WAHA_URL}/api/sessions/{WAHA_SESSION}",
                headers=WAHA_HEADERS,
                timeout=5
            )
            session_status = session_resp.json().get("status", "unknown") if session_resp.ok else "unknown"
            if session_status == "WORKING":
                send("✅ <b>WhatsApp is already connected.</b>\nUse <code>/list</code> or <code>/stats</code> to verify fresh intake.")
                return
            r = req.post(f"{WAHA_URL}/api/{WAHA_SESSION}/auth/request-code",
                         json={"phoneNumber": phone},
                         headers=WAHA_HEADERS, timeout=20)
            if r.status_code in (200, 201):
                code = r.json().get("code", "")
                send(f"📲 <b>Pairing Code:</b>\n\n<code>{code}</code>\n\nWhatsApp → Settings → Linked Devices → Link a Device → <b>Link with phone number</b>")
            else:
                body = r.text[:300]
                if "PairingCodeLinkUtils" in body:
                    send(
                        "⚠️ <b>Phone-number pairing is broken in this WAHA build.</b>\n"
                        "Use <code>/qr</code> instead, or keep using the current connected session if <code>/stats</code> shows <b>WORKING</b>."
                    )
                else:
                    send(f"❌ Code request failed ({r.status_code}): {body[:100]}")
        except Exception as e:
            send(f"❌ Link error: {e}")

    elif text.startswith("/listgroup"):
        parts = text.split(None, 1)
        if len(parts) < 2:
            send("Usage: <code>/listgroup GROUPNAME</code>\nExample: <code>/listgroup OOSAD</code>")
        else:
            query = parts[1].strip().lower()
            items = get_recent_wa(max_age_days=30)
            matched = [r for r in items if query in r[1].lower()]
            if not matched:
                send(f"No recent pending messages matching: {query}")
            else:
                lines = [f"💬 <b>{matched[0][1]} ({len(matched)} msgs)</b>\n"]
                char_count = len(lines[0])
                for id_, group, sender, message, received, done in matched:
                    block = f"<b>[{id_}]</b> {sender}\n{message[:200]}\n<i>{received[:10]}</i>\n\n"
                    if char_count + len(block) > 3800:
                        lines.append(f"<i>...{len(matched)} total, showing first {len(lines)-1}</i>")
                        break
                    lines.append(block)
                    char_count += len(block)
                lines.append("<i>/done ID to mark read</i>")
                send("\n".join(lines))

    # ── VLE LOGIN & MFA ──────────────────────────────────────

    elif text.startswith("/setup_vle"):
        parts = text.split()
        if len(parts) < 3:
            send("Usage: <code>/setup_vle email password</code>")
            return
        email = parts[1].strip()
        password = parts[2].strip()
        
        with open(CONFIG_FILE, "r", encoding="utf-8") as f:
            lines = f.readlines()

        new_lines = []
        has_base_url = False
        for line in lines:
            stripped = line.strip()
            if stripped.startswith("VLE_BASE_URL"):
                has_base_url = True
                new_lines.append('VLE_BASE_URL = "https://vle.unikl.edu.my"\n')
                continue
            if stripped.startswith("VLE_EMAIL") or stripped.startswith("VLE_PASSWORD"):
                continue
            new_lines.append(line)
        if not has_base_url:
            new_lines.append('\nVLE_BASE_URL = "https://vle.unikl.edu.my"\n')
        new_lines.append(f"\nVLE_EMAIL = {repr(email)}\n")
        new_lines.append(f"VLE_PASSWORD = {repr(password)}\n")
        
        with open(CONFIG_FILE, "w", encoding="utf-8") as f:
            f.writelines(new_lines)
                
        send("🔒 <b>VLE credentials saved!</b> You can now run `/login` to refresh your VLE session.")

    elif text == "/login":
        success = start_login_thread()
        if success:
            send(
                "🌐 <b>VLE login started.</b>\n"
                "I will try email/password first.\n"
                "If <code>/vle_status</code> shows <b>needs_approval</b>, approve it on your phone. If Microsoft shows a number, enter that number in Authenticator and approve there.\n"
                "Only use <code>/code 123456</code> if <code>/vle_status</code> shows <b>needs_code</b>.\n"
                "Use <code>/vle_status</code> to check progress."
            )
        else:
            status = login_state.get("status", "unknown")
            message = login_state.get("message", "") or "login already running"
            send(f"⚠️ VLE login already running.\nStatus: <b>{status}</b>\n{message}")

    elif text.startswith("/code"):
        parts = text.split()
        if len(parts) < 2:
            send("Usage: <code>/code 123456</code>")
            return
        code = parts[1].strip()
        if login_state["status"] == "waiting_code":
            login_state["code"] = code
            send("🔑 <b>Code received!</b> Submitting verification code to VLE login...")
        else:
            send("⚠️ No login session is currently waiting for a code.")

    elif text == "/vle_status":
        status = login_state.get("status", "unknown")
        message = login_state.get("message", "") or "-"
        error = login_state.get("error", "")
        probe = probe_saved_session()
        preview = probe_login_flow(max_seconds=8)
        lines = [f"🌐 <b>VLE Login Status</b>\nStatus: <b>{status}</b>\nInfo: {message}"]
        probe_line = f"Saved session: <b>{probe['status']}</b>"
        if probe.get("age_minutes") is not None:
            probe_line += f" | age {probe['age_minutes']} min"
        lines.append(probe_line)
        if probe.get("detail"):
            lines.append(f"Session detail: {probe['detail']}")
        if probe.get("final_url"):
            lines.append(f"Landing URL: <code>{probe['final_url'][:500]}</code>")
        lines.append(f"Login preview: <b>{preview['status']}</b>")
        if preview.get("detail"):
            lines.append(f"Preview detail: {preview['detail']}")
        if preview.get("final_url"):
            lines.append(f"Preview URL: <code>{preview['final_url'][:500]}</code>")
        if preview.get("status") == "needs_approval":
            lines.append("Action: approve the Microsoft sign-in on your phone. If it shows a number, enter that number in Authenticator. No <code>/code</code> needed yet.")
        elif preview.get("status") == "needs_code":
            lines.append("Action: enter the OTP you receive using <code>/code 123456</code>.")
        elif preview.get("status") in {"needs_email", "needs_password"}:
            lines.append("Action: bot is still before MFA. Wait a bit, then check <code>/vle_status</code> again.")
        elif preview.get("status") == "vle_ready":
            lines.append("Action: login flow can already reach VLE.")
        if error:
            lines.append(f"Error: <code>{error[:500]}</code>")
        send("\n".join(lines))

    # ── DASHBOARD & DIGEST ────────────────────────────────────

    elif text in ("/dashboard", "/dash", "/summary", "/agenda"):
        send("⏳ Building dashboard...")
        try:
            warning = build_whatsapp_warning()
            if warning:
                send(warning)
            from gemini_dashboard import get_dashboard
            result = get_dashboard()
            send(result)
        except Exception as e:
            send(f"❌ Dashboard error: {e}")

    elif text in ("/scrape", "/vle"):
        send("🔄 <b>Scraping VLE now...</b> Results follow in ~10 min.")
        log_handle = open(SCRAPE_LOG, "w", encoding="utf-8")
        subprocess.Popen(
            ["python", str(ROOT / "vle_scraper.py")],
            stdout=log_handle,
            stderr=subprocess.STDOUT,
            cwd=str(ROOT)
        )

    elif text == "/digest":
        from digest import send_digest
        send_digest()

    elif text == "/stats":
        all_wa = get_wa(include_done=True)
        pending_wa = [r for r in all_wa if r[5] == 0]
        all_dl = get_dl(include_done=True)
        pending_dl = [r for r in all_dl if r[4] != 'Done']
        wa_session = get_session_status()
        wa_status = wa_session.get("status", "unknown")
        send(
            f"📊 <b>Stats</b>\n\n"
            f"Deadline tasks: {len(pending_dl)} pending / {len(all_dl)} total\n"
            f"WA messages: {len(pending_wa)} pending / {len(all_wa)} total\n"
            f"WA session: <b>{wa_status}</b>"
        )

    elif text == "/help":
        send(
            "🌟 <b>Student Bot — Core Commands</b> 🌟\n\n"
            "📊 /summary — Main view for VLE + WhatsApp deadlines together\n"
            "🗓️ /agenda — Same merged view, easier to remember\n"
            "✅ /check ID — Mark a task done (e.g. <code>/check 300</code>)\n"
            "💬 /done ID — Dismiss a WhatsApp alert (e.g. <code>/done 521</code>)\n"
            "🌐 /login — Refresh VLE session (MFA on your phone)\n\n"
            "💡 <i>/summary is the one to remember. Everything else is secondary.</i>"
        )

    elif text == "/advanced":
        send(
            "⚙️ <b>Advanced Management Commands</b> ⚙️\n\n"
            "<b>Main:</b>\n"
            "/summary — unified agenda with VLE + WhatsApp merged by urgency/date\n"
            "/agenda — same merged agenda view\n"
            "/check ID — mark a task as done\n"
            "/done ID — dismiss a WhatsApp alert\n\n"
            "<b>Fallbacks:</b>\n"
            "/tasks — task-only list\n"
            "/list — WhatsApp-only list\n"
            "/all — all WhatsApp alerts\n"
            "/today — today's WhatsApp alerts\n"
            "/listgroup NAME — full group messages\n\n"
            "<b>System:</b>\n"
            "/scrape — rescan VLE immediately\n"
            "/digest — trigger daily digest now\n"
            "/stats — show counts\n"
            "/setup_vle email password — save credentials on server\n"
            "/code 123456 — enter OTP code for VLE login\n"
            "/vle_status — inspect current VLE login state\n"
            "/qr — scan QR for WhatsApp\n"
            f"/link {WAHA_PAIR_NUMBER or 'YOUR_PHONE_NUMBER'} — pair WhatsApp"
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
        try:
            poll()
            time.sleep(2)
        except KeyboardInterrupt:
            print("Shutting down cleanly.")
            break
