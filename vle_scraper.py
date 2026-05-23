"""
Deep VLE scraper — visits every course page, reads all assignments,
resources and PDFs. Merges with WhatsApp messages. Saves to deadlines.db.
"""
import os
import re
import html
import sqlite3
import requests
from datetime import datetime
from playwright.sync_api import sync_playwright

TELEGRAM_BOT_TOKEN = "REDACTED_TELEGRAM_BOT_TOKEN"
TELEGRAM_CHAT_ID = "716509225"
SESSION_FILE = "/root/student-bot/storageState.json"
DEADLINES_DB = "/root/student-bot/deadlines.db"
MESSAGES_DB  = "/root/student-bot/messages.db"

# All 6 courses from timetable
COURSES = {
    "IEB20603": "DATABASE",
    "ISB16003": "OOP",
    "ISB16204": "COOS",
    "IGB20303": "PROB STAT",
    "IEB20703": "OOSAD",
    "WEB20202": "PE",
}

ASSIGNMENT_KEYWORDS = re.compile(
    r'assignment|project|quiz|report|submission|submit|lab|task|exercise|test|exam|proposal'
    r'|presentation|practical|coursework|portfolio|case\s+study',
    re.IGNORECASE
)

# Match course plan PDFs that contain the full semester schedule with deadlines
COURSE_PLAN_KEYWORDS = re.compile(
    r'course\s+learning\s+plan|course\s+learning\s+program|CLP|learning\s+plan|learning\s+program|course\s+plan'
    r'|course\s+outline|subject\s+outline|assessment\s+plan|course\s+schedule|subject\s+information',
    re.IGNORECASE
)


# ── Telegram ──────────────────────────────────────────────────────────────────

def tg(msg):
    limit = 4000
    chunks = [msg[i:i+limit] for i in range(0, len(msg), limit)]
    for chunk in chunks:
        try:
            requests.post(
                f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage",
                json={"chat_id": TELEGRAM_CHAT_ID, "text": chunk, "parse_mode": "HTML",
                      "disable_web_page_preview": True},
                timeout=10
            )
        except Exception as e:
            print(f"Telegram error: {e}")


# ── Database ──────────────────────────────────────────────────────────────────

def init_db():
    conn = sqlite3.connect(DEADLINES_DB)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS deadlines (
            id      INTEGER PRIMARY KEY AUTOINCREMENT,
            task    TEXT NOT NULL,
            course  TEXT NOT NULL,
            due     TEXT NOT NULL,
            status  TEXT DEFAULT 'Pending',
            source  TEXT DEFAULT 'vle',
            added   TEXT DEFAULT CURRENT_TIMESTAMP
        )
    """)
    conn.commit()
    return conn


def _clean_task_name(task):
    """Strip leading bullets/symbols and trailing punctuation from task names."""
    task = re.sub(r'^[•\-\*·]+\s*', '', task).strip()  # leading bullets
    task = re.sub(r'[,;.]+\s*$', '', task).strip()      # trailing punctuation
    task = re.sub(r'\s{2,}', ' ', task)
    return task


def _is_stale_date(due_str):
    """Return True if the due date is clearly from a past semester (before 2025)."""
    m = re.search(r'\b(20\d{2})\b', due_str)
    if m:
        year = int(m.group(1))
        if year < 2025:
            return True
    return False


def add_if_new(conn, task, course, due, source='vle'):
    """Returns True if added, False if duplicate."""
    task = _clean_task_name(task.strip())
    if _is_stale_date(due):
        return False
    if len(task) < 3:
        return False
    # Normalize for dedup: ignore [PDF/Resource] prefix and whitespace
    norm = re.sub(r'^\[PDF/Resource\]\s*', '', task, flags=re.IGNORECASE).strip().lower()
    exists = conn.execute(
        "SELECT id FROM deadlines WHERE LOWER(TRIM(REPLACE(task,'[PDF/Resource] ',''))) = ?",
        (norm,)
    ).fetchone()
    if exists:
        return False
    conn.execute(
        "INSERT INTO deadlines (task, course, due, source) VALUES (?,?,?,?)",
        (task, course, due, source)
    )
    conn.commit()
    return True


def get_wa_messages(limit=15):
    try:
        conn = sqlite3.connect(MESSAGES_DB)
        rows = conn.execute(
            "SELECT group_name, sender, message, timestamp FROM messages ORDER BY id DESC LIMIT ?",
            (limit,)
        ).fetchall()
        conn.close()
        return rows
    except Exception:
        return []


# ── VLE helpers ───────────────────────────────────────────────────────────────

DATE_RE = re.compile(
    r'\b(\d{1,2}[\s/\-]\w+[\s/\-]\d{2,4}|\w+\s+\d{1,2},?\s+\d{4}|\d{1,2}/\d{1,2}/\d{2,4})\b'
)
DEADLINE_CONTEXT_RE = re.compile(
    r'(?:due|deadline|submit(?:ted)?|submission)\s*[:\-–]?\s*(.{0,80})',
    re.IGNORECASE
)


def _extract_text_from_file(data, filename=''):
    """Extract text from bytes, auto-detecting PDF or DOCX."""
    import tempfile, subprocess
    suffix = '.pdf'
    fname = filename.lower()
    if fname.endswith('.docx') or fname.endswith('.doc'):
        suffix = '.docx'
    elif 'pdf' in fname or not fname:
        suffix = '.pdf'

    with tempfile.NamedTemporaryFile(suffix=suffix, delete=False) as tf:
        tf.write(data)
        tmp_path = tf.name

    text = None
    try:
        if suffix == '.pdf':
            result = subprocess.run(['pdftotext', tmp_path, '-'], capture_output=True, timeout=15)
            text = result.stdout.decode('utf-8', errors='ignore')
            if not text.strip():
                try:
                    import pdfminer.high_level
                    text = pdfminer.high_level.extract_text(tmp_path)
                except Exception:
                    pass
        elif suffix == '.docx':
            try:
                import docx
                doc = docx.Document(tmp_path)
                text = '\n'.join(p.text for p in doc.paragraphs)
            except Exception:
                pass
    except Exception as e:
        print(f"    ! text extract error: {e}")
    finally:
        try:
            os.unlink(tmp_path)
        except Exception:
            pass
    return text or None


def _moodle_get_file_text(page, resource_url):
    """Download a Moodle resource and extract text content.
    Method 1: requests (works for standard resource/file modules).
    Method 2: Playwright download handler (works for custom modules like obedoc)."""
    cookies = page.context.cookies()
    headers = {
        'Cookie': '; '.join(f"{c['name']}={c['value']}" for c in cookies),
        'User-Agent': 'Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36',
    }

    # Method 1: Direct download via requests (fast, works for mod_resource)
    try:
        resp = requests.get(resource_url, headers=headers, timeout=20, allow_redirects=True)
        if resp.status_code == 200:
            ct = resp.headers.get('content-type', '').lower()
            filename = resp.url.rsplit('/', 1)[-1].split('?')[0]
            if 'pdf' in ct or 'word' in ct or 'msword' in ct or 'octet-stream' in ct:
                text = _extract_text_from_file(resp.content, filename)
                if text and text.strip():
                    return text
    except Exception:
        pass

    # Method 2: Playwright download handler (works for obedoc and other custom modules)
    try:
        import tempfile
        with page.expect_download(timeout=12000) as dl_info:
            try:
                page.goto(resource_url, timeout=15000)
            except Exception:
                pass  # "Download is starting" error is expected and OK here
        download = dl_info.value
        if download:
            with tempfile.NamedTemporaryFile(suffix='.tmp', delete=False) as tf:
                tmp_path = tf.name
            download.save_as(tmp_path)
            filename = download.suggested_filename or ''
            with open(tmp_path, 'rb') as f:
                data = f.read()
            os.unlink(tmp_path)
            text = _extract_text_from_file(data, filename)
            if text and text.strip():
                return text
    except Exception as e:
        pass

    return None


def read_pdf_deadline(page, resource_url):
    """Download a Moodle resource and extract the first due date found."""
    try:
        text = _moodle_get_file_text(page, resource_url)
        if not text:
            return None
        for line in text.splitlines():
            m = DEADLINE_CONTEXT_RE.search(line)
            if m:
                context = m.group(1).strip()
                d = DATE_RE.search(context)
                if d:
                    return d.group(0)
                if len(context) > 5:
                    return context[:60]
    except Exception as e:
        print(f"    ! PDF read error: {e}")
    return None


def extract_clp_deadlines(page, resource_url, course_code):
    """Read a Course Learning Plan and extract assessment names.
    CLPs typically list week-based schedules without exact dates, so we save
    assessments as tasks with 'See CLP' due date for the student to fill in."""
    tasks = []
    try:
        text = _moodle_get_file_text(page, resource_url)
        if not text:
            return tasks

        seen = set()
        lines = [l.strip() for l in text.splitlines()]
        weight_re = re.compile(r'\d{1,3}\s*%')
        # Merged PDF table cells look like "Test, Final" or "Assignment, Project"
        merged_cell_re = re.compile(r'^(\w[\w\s]+),\s*(\w[\w\s]+)$')
        # Section headings: "1.4 Project Proposal" or "xi. Project Management"
        section_heading_re = re.compile(r'^(\d+\.\d+\s|\b[xvi]+\.\s)', re.IGNORECASE)

        for i, line in enumerate(lines):
            if not line or len(line) < 4:
                continue

            # Skip long description lines
            if len(line) > 90:
                continue
            # Skip prose sentences starting with common words
            if re.match(r'^(this|the|a|an|in|to|at|by|for|of|and|or|it|is|are|was|were|with|that)\b', line, re.IGNORECASE):
                continue
            if re.match(r'^(lecture|chapter|topic|section|week)\b', line, re.IGNORECASE):
                continue
            # Skip merged table cells (two items joined by comma: "Test, Final")
            if merged_cell_re.match(line):
                continue
            # Skip section headings (numbered or roman numeral)
            if section_heading_re.match(line):
                continue

            has_keyword = ASSIGNMENT_KEYWORDS.search(line)
            has_weight = weight_re.search(line)

            # Keep lines with assessment keyword AND weight %, or short keyword-only lines
            if has_keyword and (has_weight or len(line) <= 40):
                # Try to find a date nearby (within 5 lines)
                due = "See CLP"
                for j in range(max(0, i-5), min(len(lines), i+6)):
                    d = DATE_RE.search(lines[j])
                    if d:
                        due = d.group(0)
                        break

                task_text = re.sub(r'\s{2,}', ' ', line).strip()[:80]
                norm = task_text.lower()
                if len(task_text) > 4 and norm not in seen:
                    seen.add(norm)
                    tasks.append((task_text, due))

        print(f"    [CLP] Extracted {len(tasks)} assessment(s) from document")
    except Exception as e:
        print(f"    ! CLP extract error: {e}")
    return tasks


def extract_course_code(text):
    m = re.search(r'\b([A-Z]{2,3}\d{5})\b', text)
    return m.group(1) if m else None


def clean_name(name):
    name = re.sub(r'\s+is due\s*$', '', name, flags=re.IGNORECASE)
    name = re.sub(r'\s{2,}', ' ', name)
    return name.strip()


def try_sso_refresh(page, ctx):
    """Attempt SSO auto-refresh if Moodle session expired."""
    import time
    print("  Session expired — trying SSO auto-refresh...")
    page.goto("https://vle.unikl.edu.my/auth/oidc/", timeout=30000)
    for _ in range(20):
        time.sleep(1)
        if "vle.unikl.edu.my/my" in page.url:
            print("  SSO refresh OK!")
            ctx.storage_state(path=SESSION_FILE)
            return True
    return False


def scrape_assignment_page(page, url):
    """Visit an individual assignment/activity page and extract due date."""
    try:
        page.goto(url, timeout=20000)
        page.wait_for_load_state("domcontentloaded", timeout=10000)

        # Look for due date in assignment view
        for sel in [
            '.submissionstatusbutton',
            '[data-region="assign-due-date"]',
            '.box.generalbox td:nth-child(2)',
            'td.cell.c1',
        ]:
            els = page.query_selector_all(sel)
            for el in els:
                txt = el.inner_text().strip()
                if re.search(r'\d{1,2}\s+\w+\s+\d{4}|\d{1,2}/\d{1,2}/\d{2,4}', txt):
                    return txt[:60]

        # Try table rows looking for "due date"
        rows = page.query_selector_all('tr')
        for row in rows:
            txt = row.inner_text().lower()
            if 'due' in txt:
                val = row.inner_text().strip()
                if re.search(r'\d{1,2}\s+\w+\s+\d{4}', val):
                    return val[:80]
    except Exception as e:
        print(f"    assignment page error: {e}")
    return "See VLE"


def scrape_course(page, course_url, course_code, conn):
    """Visit a course page and scrape all assignments and resources."""
    added = []
    course_name = COURSES.get(course_code, course_code)
    print(f"\n  [{course_name}] {course_url[:70]}")

    try:
        page.goto(course_url, timeout=30000)
        page.wait_for_load_state("networkidle", timeout=15000)
    except Exception as e:
        print(f"    ! Load error: {e}")
        return added

    # Expand all collapsed course sections so all activities are visible
    try:
        collapsed = page.query_selector_all('.collapsed[data-toggle="collapse"], .section-toggle, .course-section-header.collapsed')
        for btn in collapsed:
            try:
                btn.click(timeout=2000)
            except Exception:
                pass
        if collapsed:
            page.wait_for_timeout(1000)
    except Exception:
        pass

    # ── Step 1: collect all activity metadata BEFORE any navigation ──
    # This avoids DOM context errors from stale ElementHandle references.
    activities = []  # list of (task_name, href, modtype, due_inline)
    items = page.query_selector_all('[data-activityname]')
    print(f"    Activities found: {len(items)}")

    for item in items:
        try:
            # data-activityname attribute gives the clean name
            task_name = html.unescape(item.get_attribute('data-activityname') or '')
            # Strip embedded newlines and trailing Moodle type suffixes (e.g. "\nFile")
            task_name = re.sub(r'\s*\n.*$', '', task_name).strip()
            task_name = re.sub(r'\s+(File|URL|Page|Folder|Forum|Label|Quiz|Assignment)\s*$', '', task_name, flags=re.IGNORECASE).strip()

            modtype = item.get_attribute('data-activitytype') or ''
            if not modtype:
                classes = item.get_attribute('class') or ''
                for cls in classes.split():
                    if cls.startswith('modtype_'):
                        modtype = cls[len('modtype_'):]
                        break

            if not task_name:
                name_el = (item.query_selector('.activityname a')
                           or item.query_selector('.instancename')
                           or item.query_selector('a[href]'))
                if not name_el:
                    continue
                raw = html.unescape(name_el.inner_text().strip())
                raw = re.sub(r'\s*\n.*$', '', raw).strip()
                raw = re.sub(r'\s+(File|URL|Page|Folder|Forum|Label|Quiz|Assignment)\s*$', '', raw, flags=re.IGNORECASE)
                task_name = clean_name(raw)

            task_name = re.sub(r'\s*\(Not available\)\s*', '', task_name).strip()
            if len(task_name) < 3:
                continue

            name_el = (item.query_selector('.activityname a')
                       or item.query_selector('a[href]'))
            href = (name_el.get_attribute('href') if name_el else '') or ''

            # Infer modtype from href URL pattern (reliable for Moodle)
            if not modtype:
                for mod in ('assign', 'quiz', 'resource', 'page', 'forum', 'folder', 'url'):
                    if f'/mod/{mod}/' in href:
                        modtype = mod
                        break

            due_el = (item.query_selector('.activity-info .text-truncate')
                      or item.query_selector('.dudatetimeparsed')
                      or item.query_selector('.activity-dates'))
            due_inline = due_el.inner_text().strip() if due_el else None

            activities.append((task_name, href, modtype, due_inline))
        except Exception as e:
            print(f"    ! collect error: {e}")

    # ── Step 2: process each activity, navigating only when needed ──
    # For PE (WEB20202): skip items from other sections. Arif is in L07.
    pe_section_re = re.compile(r'\bL0[0-9]\b', re.IGNORECASE)

    for task_name, href, modtype, due_inline in activities:
        try:
            if not task_name or len(task_name) < 3:
                continue

            is_assign_type = modtype in ('assign', 'quiz')
            is_keyword_match = ASSIGNMENT_KEYWORDS.search(task_name)

            # Skip study materials (Sample Proposal, Exercise files, Lecture Notes etc.)
            # These match keywords like "exercise" but aren't graded submissions.
            if modtype == 'resource' and re.match(
                r'^(sample\s|exercise\s*[-–]|lecture\s|slide|study\s+guide|tutorial\s+note|note[s]?\s)',
                task_name, re.IGNORECASE
            ):
                continue

            # PE section filter: keep only L07 and generic (no section suffix) items
            if course_code == 'WEB20202':
                m = pe_section_re.search(task_name)
                if m and m.group(0).upper() != 'L07':
                    continue

            # Skip Moodle announcement forum posts (not actual tasks)
            if task_name.startswith('📢') or re.match(r'^Announcement[s]?[\s:–—]', task_name, re.IGNORECASE):
                continue

            is_clp = COURSE_PLAN_KEYWORDS.search(task_name)

            if not is_assign_type and not is_keyword_match and not is_clp:
                continue

            # Only use inline due if it looks like an actual date
            due = due_inline if (due_inline and DATE_RE.search(due_inline)) else None

            # For assign/quiz: visit assignment page to get due date
            if (not due) and href and is_assign_type:
                try:
                    result = scrape_assignment_page(page, href)
                    if result and result != "See VLE" and DATE_RE.search(result):
                        due = result
                    page.goto(course_url, timeout=20000)
                    page.wait_for_load_state("domcontentloaded", timeout=10000)
                except Exception as e:
                    print(f"    ! assign page error: {e}")

            # For linked files (resource/page or unknown): download via requests and extract deadline
            if (not due) and href and modtype not in ('forum', 'label', 'assign', 'quiz'):
                try:
                    pdf_due = read_pdf_deadline(page, href)
                    if pdf_due:
                        due = pdf_due
                except Exception as e:
                    print(f"    ! resource read error: {e}")

            # CLP documents: extract deadline lines as separate tasks
            if is_clp:
                if href:
                    try:
                        clp_tasks = extract_clp_deadlines(page, href, course_code)
                        for ct_name, ct_due in clp_tasks:
                            if add_if_new(conn, ct_name, course_name, ct_due, 'vle-clp'):
                                added.append((ct_name, course_code, ct_due))
                                print(f"    + [clp] {ct_name[:50]}  due: {ct_due[:30]}")
                            else:
                                print(f"    ~ dup [clp]: {ct_name[:50]}")
                    except Exception as e:
                        print(f"    ! CLP error: {e}")
                # CLP itself is not a submittable task — always skip adding it
                if not is_assign_type and not is_keyword_match:
                    continue

            due = (due or "See VLE").strip()[:80]
            source = f'vle-{course_name.lower()}'

            if add_if_new(conn, task_name, course_name, due, source):
                added.append((task_name, course_code, due))
                print(f"    + [{modtype}] {task_name[:50]}  due: {due[:30]}")
            else:
                print(f"    ~ dup: {task_name[:50]}")
        except Exception as e:
            print(f"    ! process error: {e}")

    # ── Fallback: if nothing was added, scan ALL resources for project briefs ──
    if not added:
        print(f"    [fallback] 0 tasks found — reading all PDFs/pages for project info...")
        for task_name, href, modtype, _ in activities:
            if modtype not in ('resource', 'page', 'url') or not href:
                continue
            # Skip things that are clearly not assignment documents
            if re.match(r'^(announcement|notice|welcome|news|forum)\b', task_name, re.IGNORECASE):
                continue
            try:
                clp_tasks = extract_clp_deadlines(page, href, course_code)
                for ct_name, ct_due in clp_tasks:
                    if add_if_new(conn, ct_name, course_name, ct_due, 'vle-clp'):
                        added.append((ct_name, course_code, ct_due))
                        print(f"    + [fallback] {ct_name[:50]}  due: {ct_due[:30]}")
            except Exception as e:
                print(f"    ! fallback error for {task_name[:40]}: {e}")

    return added


# ── Main ──────────────────────────────────────────────────────────────────────

def run():
    ts = datetime.now().strftime('%H:%M:%S')
    print(f"[{ts}] VLE Deep Scraper starting...")
    tg("🔄 <b>VLE Deep Scraper</b>\nLoading all 6 course pages...")

    if not os.path.exists(SESSION_FILE):
        tg("❌ storageState.json not found.")
        return

    conn = init_db()
    all_added = []

    with sync_playwright() as p:
        b = p.chromium.launch(headless=True, args=['--no-sandbox', '--disable-dev-shm-usage'])
        ctx = b.new_context(storage_state=SESSION_FILE)
        page = ctx.new_page()

        # Load dashboard
        print("Loading VLE...")
        page.goto("https://vle.unikl.edu.my/my/", timeout=60000)
        page.wait_for_load_state("networkidle", timeout=20000)

        if "microsoftonline.com" in page.url:
            tg("⚠️ <b>Session Expired (Microsoft)</b>\nRun get_session.py locally.")
            b.close(); conn.close(); return

        if "login/index.php" in page.url:
            if not try_sso_refresh(page, ctx):
                tg("⚠️ <b>Session Expired</b>\nRun get_session.py locally.")
                b.close(); conn.close(); return
            page.goto("https://vle.unikl.edu.my/my/", timeout=30000)
            page.wait_for_load_state("networkidle", timeout=15000)

        # Discover course URLs from dashboard
        course_map = {}  # code -> url
        links = page.query_selector_all('a[href*="course/view.php"]')
        for link in links:
            href = link.get_attribute('href') or ''
            text = link.inner_text().strip()
            code = extract_course_code(text) or extract_course_code(href)
            if code and code in COURSES and code not in course_map:
                course_map[code] = href
                print(f"  Found course: {code} → {href}")

        # If any courses not found by link text, try the "All courses" / enrolment API
        missing = [c for c in COURSES if c not in course_map]
        if missing:
            print(f"  Missing via links: {missing}, trying course search...")
            page.goto("https://vle.unikl.edu.my/my/courses.php", timeout=20000)
            page.wait_for_load_state("networkidle", timeout=10000)
            links2 = page.query_selector_all('a[href*="course/view.php"]')
            for link in links2:
                href = link.get_attribute('href') or ''
                text = (link.inner_text() + " " + href)
                code = extract_course_code(text)
                if code and code in COURSES and code not in course_map:
                    course_map[code] = href
                    print(f"  Found (courses page): {code} → {href}")

        print(f"\nCourses found: {list(course_map.keys())}")
        print(f"Missing: {[c for c in COURSES if c not in course_map]}")

        # Deep-scrape each course
        for code, url in course_map.items():
            added = scrape_course(page, url, code, conn)
            all_added.extend(added)

        b.close()
    conn.close()

    # ── Build unified report ──────────────────────────────────
    conn2 = sqlite3.connect(DEADLINES_DB)
    pending = conn2.execute(
        "SELECT id, task, course, due FROM deadlines WHERE status != 'Done' ORDER BY course, id"
    ).fetchall()
    conn2.close()

    wa_msgs = get_wa_messages(10)

    # Group pending by course
    by_course = {}
    for id_, task, course, due in pending:
        by_course.setdefault(course, []).append((id_, task, due))

    lines = [f"📚 <b>VLE Deep Scrape Done</b>  {len(all_added)} new task(s)\n"]
    lines.append(f"<b>All Pending Tasks ({len(pending)})</b>\n")

    for code, tasks in by_course.items():
        lines.append(f"<b>── {code} ──</b>")
        for id_, task, due in tasks:
            lines.append(f"  {id_}. {task[:55]}\n      📅 {due[:50]}")
        lines.append("")

    if wa_msgs:
        lines.append(f"\n<b>── WhatsApp Messages ({len(wa_msgs)}) ──</b>")
        for group, sender, msg, ts in wa_msgs:
            lines.append(f"  <b>{group}</b>: {msg[:80]}")
    else:
        lines.append("\n<b>── WhatsApp ──</b>\n  No messages yet (scan QR to connect)")

    lines.append("\n<i>/tasks — manage tasks  |  /list — WA messages  |  /scrape — rescan now</i>")

    tg("\n".join(lines))
    print(f"\nDone. {len(all_added)} new, {len(pending)} total pending.")


if __name__ == "__main__":
    run()
