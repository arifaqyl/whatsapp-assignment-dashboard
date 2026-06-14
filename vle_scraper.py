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
from urllib.parse import unquote, urlparse
from playwright.sync_api import sync_playwright
import config as app_config
import db as ops_db
from config import BOT_TOKEN as TELEGRAM_BOT_TOKEN, CHAT_ID as TELEGRAM_CHAT_ID
from paths import DEADLINES_DB as DEADLINES_DB_PATH, MESSAGES_DB as MESSAGES_DB_PATH, SESSION_FILE as SESSION_FILE_PATH
from deadline_utils import (
    choose_better_source,
    choose_better_task,
    has_concrete_due,
    is_generic_due,
    normalize_task_name,
    is_active_due,
    should_replace_due,
    tasks_match,
)
SESSION_FILE = str(SESSION_FILE_PATH)
DEADLINES_DB = str(DEADLINES_DB_PATH)
MESSAGES_DB = str(MESSAGES_DB_PATH)
VLE_BASE_URL = getattr(app_config, "VLE_BASE_URL", "https://vle.example.edu.my").rstrip("/")
COURSES = getattr(app_config, "VLE_COURSES", {})

ASSIGNMENT_KEYWORDS = re.compile(
    r'\b(?:assignment|project|quiz|quizzes|report|submission|submit|lab|task|exercise|test|exam|proposal'
    r'|presentation|practical|coursework|portfolio|case\s+study)s?\b',
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
    return normalize_task_name(task)


def _is_stale_date(due_str):
    """Return True if the due date is clearly from a past semester (before 2025)."""
    m = re.search(r'\b(20\d{2})\b', due_str)
    if m:
        year = int(m.group(1))
        if year < 2025:
            return True
    return False


def add_if_new(conn, task, course, due, source='vle'):
    """Returns True if added/updated, False if duplicate with no changes."""
    task = _clean_task_name(task.strip())
    if _is_stale_date(due):
        return False
    if len(task) < 3:
        return False
    rows = conn.execute(
        "SELECT id, task, due, source FROM deadlines WHERE course = ? AND status != 'Done'",
        (course,)
    ).fetchall()
    for row_id, existing_task, existing_due, existing_source in rows:
        if not tasks_match(existing_task, task):
            continue
        next_task = choose_better_task(existing_task, task)
        next_due = due if should_replace_due(existing_due, due, existing_source, source) else existing_due
        next_source = choose_better_source(existing_source, source)
        changed = (next_task != existing_task) or (next_due != existing_due) or (next_source != existing_source)
        if changed:
            conn.execute(
                "UPDATE deadlines SET task = ?, due = ?, source = ? WHERE id = ?",
                (next_task, next_due, next_source, row_id)
            )
            conn.commit()
            print(f"    * updated task '{existing_task}' -> '{next_task}' | due '{existing_due}' -> '{next_due}'")
            return True
        if is_generic_due(existing_due) and is_generic_due(due):
            return False
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
    r'\b('
    r'(?:Monday|Tuesday|Wednesday|Thursday|Friday|Saturday|Sunday),?\s+[A-Za-z]{3,10}\s+\d{1,2},?\s+\d{4}'
    r'|\d{1,2}[\s/\-]\w+[\s/\-]\d{2,4}'
    r'|\w+\s+\d{1,2},?\s+\d{4}(?:\s+\d{1,2}:\d{2}\s*(?:AM|PM))?'
    r'|\d{1,2}/\d{1,2}/\d{2,4}'
    r'|\d{1,2}\.\d{1,2}\.\d{2,4}'
    r')\b',
    re.IGNORECASE,
)
DEADLINE_CONTEXT_RE = re.compile(
    r'(?:due|deadline|submit(?:ted)?|submission)\s*[:\-–]?\s*(.{0,80})',
    re.IGNORECASE
)

EXPLICIT_TASK_RE = re.compile(
    r'\b(?:assignment\s*\d+|quiz\s*\d+|test\s*\d+|lab\s*test|mid\s*term|final\s+exam|final\s+examination|'
    r'group\s+project|mini\s+project|project\s+report|project\s+presentation|proposal)\b',
    re.IGNORECASE
)

RESOURCE_NOISE_RE = re.compile(
    r'^(announcement|notice|welcome|news|forum|lecture|slide|chapter|week|topic|template|sample|exercise|'
    r'tutorial|note|reading|handout|material|reference|rubric|syllabus|coursework|solution|answers?|recording|'
    r'calendar|guide|introduction|overview|slides?)\b',
    re.IGNORECASE
)

PAGE_TEXT_TASK_RE = re.compile(
    r'\b(?:assignment(?:\s+\d+)?|project(?:\s+group)?|quiz(?:\s+\d+)?|test(?:\s+\d+)?|'
    r'exam|proposal|presentation|final\s+project|final\s+exam|report)\b',
    re.IGNORECASE
)
PAGE_TEXT_PROSE_PREFIX_RE = re.compile(
    r'^(?:please|dear|this|that|these|those|you|your|students|student|kindly|for\s+all|all\s+students)\b',
    re.IGNORECASE
)


def _norm_resource_label(text):
    text = unquote(text or "")
    text = text.split("#", 1)[0].split("?", 1)[0]
    text = os.path.basename(text)
    text = re.sub(r'\.(pdf|docx?|pptx?|ppt|txt|zip|rar)$', '', text, flags=re.IGNORECASE)
    text = text.replace("_", " ").replace("-", " ")
    text = re.sub(r'\s+', ' ', text).strip()
    return text


def _extract_due_hint(text):
    if not text:
        return None
    match = DATE_RE.search(text)
    return match.group(0) if match else None


def _resource_hints(*parts):
    blobs = [p for p in parts if p]
    combined = " | ".join(blobs)
    filename_due = _extract_due_hint(_norm_resource_label(combined))
    if filename_due:
        return filename_due
    return _extract_due_hint(combined)


def _is_low_signal_resource(text):
    return bool(RESOURCE_NOISE_RE.search(text or ""))


def _skip(reason, task_name):
    print(f"    ~ skip {reason}: {task_name[:80]}")


def _clean_page_text_task(task_name):
    task_name = html.unescape(task_name or "")
    task_name = re.sub(r'\s+', ' ', task_name).strip(" -:\t")
    task_name = re.sub(r'^(?:assessment|task)\s*[:\-]\s*', '', task_name, flags=re.IGNORECASE)
    return task_name[:80]


def _purge_manual_placeholder_rows(conn, course_name):
    rows = conn.execute(
        "SELECT id, task, due FROM deadlines WHERE course = ? AND status != 'Done' AND source = 'manual'",
        (course_name,),
    ).fetchall()
    removed = 0
    for row_id, task, due in rows:
        lower_task = (task or "").lower()
        lower_due = (due or "").lower()
        if "check vle" not in lower_task and "see vle" not in lower_task and "check vle" not in lower_due:
            continue
        conn.execute("DELETE FROM deadlines WHERE id = ?", (row_id,))
        removed += 1
    if removed:
        conn.commit()
        print(f"  Purged {removed} manual placeholder row(s) for {course_name}")
    return removed


def _extract_page_text_deadlines(body_text):
    if not body_text:
        return []

    found = []
    seen = set()
    lines = [re.sub(r'\s+', ' ', (line or "")).strip() for line in body_text.splitlines()]
    lines = [line for line in lines if line]

    patterns = [
        (
            re.compile(
                r'(?ims)(?:^|\n)\s*([^\n]{0,80}?\b(?:Final Project|Project Group|Group Project|Assignment(?:\s+\d+)?|'
                r'Quiz(?:\s+\d+)?|Test(?:\s+\d+)?|Proposal|Presentation|Exam)\b[^\n]{0,20})[^\n]*\n?.{0,800}?'
                r'(?:deadline(?:\s+for\s+submission)?|due|submission\s+deadline|date)\s*[:\-]?\s*'
                r'([A-Za-z]+\s+\d{1,2},\s*\d{4}[^.\n]*|\d{1,2}/\d{1,2}/\d{2,4}[^.\n]*|\d{1,2}\s+[A-Za-z]{3,10}\s+\d{4}[^.\n]*)'
            ),
            lambda m: m.group(1),
            lambda m: m.group(2),
        ),
    ]

    for pattern, task_getter, due_getter in patterns:
        for match in pattern.finditer(body_text):
            task_name = _clean_page_text_task(task_getter(match))
            due = due_getter(match).strip()
            if not task_name or not DATE_RE.search(due):
                continue
            key = (task_name.lower(), due)
            if key in seen:
                continue
            seen.add(key)
            found.append((task_name, due[:80]))

    for idx, line in enumerate(lines):
        if len(line) < 4 or len(line) > 120:
            continue
        if not PAGE_TEXT_TASK_RE.search(line):
            continue
        if PAGE_TEXT_PROSE_PREFIX_RE.search(line):
            continue
        if _is_low_signal_resource(line):
            continue

        task_name = _clean_page_text_task(line)
        due = None
        for neighbor in lines[idx: min(idx + 6, len(lines))]:
            if neighbor == line:
                continue
            if re.search(r'\b(?:deadline|due|submission|date)\b', neighbor, re.IGNORECASE):
                due_match = DATE_RE.search(neighbor)
                if due_match:
                    due = due_match.group(0)
                    break
            if not due:
                due_match = DATE_RE.search(neighbor)
                if due_match and len(neighbor) <= 80:
                    due = due_match.group(0)
                    break
        if not due:
            continue
        key = (task_name.lower(), due)
        if key in seen:
            continue
        seen.add(key)
        found.append((task_name, due[:80]))

    deduped = []
    for task_name, due in found:
        skip = False
        for idx, (existing_task, existing_due) in enumerate(deduped):
            if due != existing_due:
                same_task = task_name.lower() == existing_task.lower()
                if same_task and (due in existing_due or existing_due in due):
                    if len(due) > len(existing_due):
                        deduped[idx] = (existing_task, due)
                    skip = True
                    break
                continue
            low_task = task_name.lower()
            low_existing = existing_task.lower()
            if low_task == low_existing:
                if len(due) > len(existing_due):
                    deduped[idx] = (existing_task, due)
                skip = True
                break
            if low_task in low_existing:
                skip = True
                break
        if not skip:
            deduped = [
                (existing_task, existing_due)
                for existing_task, existing_due in deduped
                if not (existing_due == due and existing_task.lower() in task_name.lower())
            ]
            deduped.append((task_name, due))

    return deduped


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
        url_name = _norm_resource_label(resource_url)
        hinted = _extract_due_hint(url_name)
        if hinted:
            return hinted
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


def _has_real_due(due):
    return bool(due and DATE_RE.search(due))


def _clean_clp_task_text(task_text):
    task_text = re.sub(r'^[•\-\*·]+\s*', '', task_text).strip()
    task_text = re.sub(r'[,;:]+\s*$', '', task_text).strip()
    task_text = re.sub(r'\s{2,}', ' ', task_text)
    return task_text


def _should_keep_clp_task(task_text, due):
    lower = task_text.lower()
    if not _has_real_due(due):
        return False
    generic = {
        "assignment", "project", "proposal", "presentation", "practical",
        "test", "quiz", "lab exercise", "assessment(s) lab report"
    }
    if lower in generic:
        return False
    if len(task_text) < 6:
        return False
    return True


def purge_noisy_clp_rows(conn):
    """Remove old low-confidence CLP rows that only carry placeholders."""
    rows = conn.execute(
        "SELECT id, task, due FROM deadlines WHERE status != 'Done' AND source = 'vle-clp'"
    ).fetchall()
    removed = 0
    for row_id, task, due in rows:
        task = _clean_clp_task_text(task or "")
        if _should_keep_clp_task(task, due):
            continue
        conn.execute("DELETE FROM deadlines WHERE id = ?", (row_id,))
        removed += 1
    if removed:
        conn.commit()
        print(f"  Purged {removed} noisy CLP row(s)")
    return removed


def extract_clp_deadlines(page, resource_url, course_code):
    """Read a Course Learning Plan and extract assessment names.
    CLPs are noisy, so only keep dated, specific assessment lines."""
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
                due = None
                for j in range(max(0, i-5), min(len(lines), i+6)):
                    d = DATE_RE.search(lines[j])
                    if d:
                        due = d.group(0)
                        break

                task_text = _clean_clp_task_text(line)[:80]
                norm = task_text.lower()
                if _should_keep_clp_task(task_text, due) and norm not in seen:
                    seen.add(norm)
                    tasks.append((task_text, due))

        print(f"    [CLP] Extracted {len(tasks)} assessment(s) from document")
    except Exception as e:
        print(f"    ! CLP extract error: {e}")
    return tasks


def extract_course_code(text):
    m = re.search(r'\b([A-Z]{2,3}\d{5})\b', text)
    return m.group(1) if m else None


def derive_course_key(text, href):
    code = extract_course_code(text)
    if code:
        return code
    label = re.sub(r'\s+', ' ', (text or '')).strip()
    label = re.sub(r'\s*\|\s*.*$', '', label)
    label = label[:80].strip()
    if not label:
        label = (href or '').rstrip('/').split('/')[-1]
    return label or href


def clean_name(name):
    name = re.sub(r'\s+is due\s*$', '', name, flags=re.IGNORECASE)
    name = re.sub(r'\s{2,}', ' ', name)
    return name.strip()


def try_sso_refresh(page, ctx):
    """Attempt SSO auto-refresh if Moodle session expired."""
    import time
    print("  Session expired — trying SSO auto-refresh...")
    page.goto(f"{VLE_BASE_URL}/auth/oidc/", timeout=30000)
    for _ in range(20):
        time.sleep(1)
        if VLE_BASE_URL.rstrip("/") + "/my" in page.url:
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
        ]:
            els = page.query_selector_all(sel)
            for el in els:
                txt = el.inner_text().strip()
                if DATE_RE.search(txt):
                    return txt[:80]

        # Try table rows looking for "due date"
        rows = page.query_selector_all('tr')
        for row in rows:
            txt = row.inner_text().lower()
            if 'due' in txt:
                val = row.inner_text().strip()
                if DATE_RE.search(val):
                    return val[:80]

        # Fallback: scan the entire body text line-by-line for a due date statement
        body_text = page.locator("body").inner_text()
        for line in body_text.splitlines():
            line_clean = line.strip()
            if not line_clean:
                continue
            line_lower = line_clean.lower()
            if 'due' in line_lower:
                # Look for date pattern in the same line (e.g. "Due: Sunday, 14 June 2026, 11:59 PM")
                if DATE_RE.search(line_clean):
                    return line_clean[:80]
    except Exception as e:
        print(f"    assignment page error: {e}")
    return "See VLE"


def retry_due_lookup(resource_url, task_name=""):
    """Best-effort due-date retry for a saved VLE activity/resource URL."""
    due = _resource_hints(task_name, resource_url)
    if due:
        return due[:80]
    if not resource_url or not os.path.exists(SESSION_FILE):
        return None

    browser = None
    try:
        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True, args=['--no-sandbox', '--disable-dev-shm-usage'])
            ctx = browser.new_context(storage_state=SESSION_FILE)
            page = ctx.new_page()
            if "/mod/assign/" in resource_url or "/mod/quiz/" in resource_url:
                due = scrape_assignment_page(page, resource_url)
                if due == "See VLE":
                    due = None
            else:
                due = read_pdf_deadline(page, resource_url)
            return due[:80] if due else None
    except Exception as e:
        print(f"    ! retry due lookup error: {e}")
        return None
    finally:
        if browser:
            try:
                browser.close()
            except Exception:
                pass


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
    activities = []  # list of (task_name, href, modtype, due_inline, title, aria)
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

            aria = item.get_attribute('aria-label') or ''
            title = item.get_attribute('title') or ''
            block_text = " ".join(filter(None, [
                task_name,
                href,
                aria,
                title,
                item.inner_text().strip() if hasattr(item, "inner_text") else "",
            ]))
            if not due_inline:
                due_inline = _extract_due_hint(block_text)
            if not modtype and _is_low_signal_resource(block_text):
                modtype = 'resource'

            activities.append((task_name, href, modtype, due_inline, title, aria))
        except Exception as e:
            print(f"    ! collect error: {e}")

    # ── Step 1b: scan raw page text for deadline blocks not exposed as activity cards ──
    try:
        body_text = page.locator("body").inner_text()
        for task_name, due in _extract_page_text_deadlines(body_text):
            source = f'vle-{course_name.lower()}'
            if add_if_new(conn, task_name, course_name, due[:80], source):
                added.append((task_name, course_code, due[:80]))
                print(f"    + [page-text] {task_name[:50]}  due: {due[:30]}")
    except Exception as e:
        print(f"    ! page-text scan error: {e}")

    # ── Step 2: process each activity, navigating only when needed ──
    # For PE (WEB20202): skip items from other sections. Arif is in L07.
    pe_section_re = re.compile(r'\bL0[0-9]\b', re.IGNORECASE)

    for task_name, href, modtype, due_inline, title, aria in activities:
        try:
            if not task_name or len(task_name) < 3:
                continue

            is_assign_type = modtype in ('assign', 'quiz')
            is_keyword_match = ASSIGNMENT_KEYWORDS.search(task_name)
            resource_blob = " ".join(filter(None, [
                task_name,
                href,
                modtype,
                title,
                aria,
                _norm_resource_label(href),
            ]))

            # Skip study materials (Sample Proposal, Exercise files, Lecture Notes etc.)
            # These match keywords like "exercise" but aren't graded submissions.
            if modtype == 'resource' and _is_low_signal_resource(resource_blob):
                _skip("low-signal resource", task_name)
                continue

            # PE section filter: keep only L07 and generic (no section suffix) items
            if course_code == 'WEB20202':
                m = pe_section_re.search(task_name)
                if m and m.group(0).upper() != 'L07':
                    continue

            # Skip Moodle announcement forum posts (not actual tasks)
            if task_name.startswith('📢') or re.match(r'^Announcement[s]?[\s:–—]', task_name, re.IGNORECASE):
                _skip("announcement", task_name)
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
                    # First try filename/link hints before downloading content.
                    pdf_due = _resource_hints(task_name, href, title, aria)
                    if not pdf_due:
                        pdf_due = read_pdf_deadline(page, href)
                    if pdf_due:
                        due = pdf_due
                except Exception as e:
                    print(f"    ! resource read error: {e}")

            # CLP documents: extract deadline lines as separate tasks
            if is_clp:
                if course_code == 'WEB20202':
                    # Skip PE CLP entirely to avoid parsing slide-deck templates and garbage lines as tasks
                    _skip("PE CLP", task_name)
                    continue
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

            # Avoid adding plain resource titles with no actual date.
            if modtype in ('resource', 'page', 'url') and not due and not is_clp:
                if is_keyword_match:
                    ops_db.enqueue_evidence_item(
                        source_type="vle",
                        course=course_name,
                        title=task_name,
                        message=href,
                        reason_code="missing_due",
                        evidence_preview=resource_blob[:400],
                    )
                _skip("undated resource", task_name)
                continue

            # For assign/quiz, keep undated items only if they look explicitly actionable.
            if modtype in ('assign', 'quiz') and not due and not EXPLICIT_TASK_RE.search(task_name):
                ops_db.enqueue_evidence_item(
                    source_type="vle",
                    course=course_name,
                    title=task_name,
                    message=href,
                    reason_code="weak_due_signal",
                    evidence_preview=resource_blob[:400],
                )
                _skip("weak undated activity", task_name)
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
    if not added and course_code != 'WEB20202':
        print(f"    [fallback] 0 tasks found — reading all PDFs/pages for project info...")
        for task_name, href, modtype, _, title, aria in activities:
            if modtype not in ('resource', 'page', 'url') or not href:
                continue
            # Skip things that are clearly not assignment documents (study/lecture materials)
            if _is_low_signal_resource(task_name) or _is_low_signal_resource(_norm_resource_label(href)):
                _skip("low-signal fallback resource", task_name)
                continue
            if not re.search(r'project|assignment|quiz|test|proposal|brief', task_name, re.IGNORECASE):
                _skip("fallback missing project/assignment keywords", task_name)
                continue
            try:
                pdf_due = _resource_hints(task_name, href, title, aria)
                if not pdf_due:
                    pdf_due = read_pdf_deadline(page, href)
                if pdf_due and add_if_new(conn, task_name, course_name, pdf_due, f'vle-{course_name.lower()}'):
                    added.append((task_name, course_code, pdf_due))
                    print(f"    + [fallback] {task_name[:50]}  due: {pdf_due[:30]}")
            except Exception as e:
                print(f"    ! fallback error for {task_name[:40]}: {e}")

    if any(has_concrete_due(due) for _, _, due in added):
        _purge_manual_placeholder_rows(conn, course_name)

    return added


# ── Main ──────────────────────────────────────────────────────────────────────

def run():
    ts = datetime.now().strftime('%H:%M:%S')
    print(f"[{ts}] VLE Deep Scraper starting...")
    tg("🔄 <b>VLE Deep Scraper</b>\nLoading course pages...")
    ops_db.init()
    ops_db.record_system_health("vle_scraper", "ok", "starting")

    if not os.path.exists(SESSION_FILE):
        tg("❌ storageState.json not found.")
        ops_db.record_system_health("vle_scraper", "error", "storageState.json missing")
        return

    conn = init_db()
    purge_noisy_clp_rows(conn)
    all_added = []

    with sync_playwright() as p:
        b = p.chromium.launch(headless=True, args=['--no-sandbox', '--disable-dev-shm-usage'])
        ctx = b.new_context(storage_state=SESSION_FILE)
        page = ctx.new_page()

        # Load dashboard
        print("Loading VLE...")
        page.goto(f"{VLE_BASE_URL}/my/", timeout=60000)
        page.wait_for_load_state("networkidle", timeout=20000)

        if "microsoftonline.com" in page.url:
            tg("⚠️ <b>Session Expired (Microsoft)</b>\nRun get_session.py locally.")
            ops_db.record_system_health("vle_scraper", "error", "session expired via microsoftonline")
            b.close(); conn.close(); return

        if "login/index.php" in page.url:
            if not try_sso_refresh(page, ctx):
                tg("⚠️ <b>Session Expired</b>\nRun get_session.py locally.")
                ops_db.record_system_health("vle_scraper", "error", "session expired login/index.php")
                b.close(); conn.close(); return
            page.goto(f"{VLE_BASE_URL}/my/", timeout=30000)
            page.wait_for_load_state("networkidle", timeout=15000)

        # Discover course URLs from the full courses page only.
        # The dashboard contains repeated cards and timeline links that can
        # misassociate a course code with the wrong href.
        course_map = {}  # course key -> url
        page.goto(f"{VLE_BASE_URL}/my/courses.php", timeout=20000)
        page.wait_for_load_state("networkidle", timeout=10000)
        links = page.query_selector_all('a[href*="course/view.php"]')
        for link in links:
            href = link.get_attribute('href') or ''
            text = (link.inner_text() + " " + href)
            code = extract_course_code(text)
            if COURSES and code and code not in COURSES:
                continue
            key = code or derive_course_key(text, href)
            if key and key not in course_map:
                course_map[key] = href
                print(f"  Found course: {key} → {href}")

        print(f"\nCourses found: {list(course_map.keys())}")
        if COURSES:
            print(f"Missing: {[c for c in COURSES if c not in course_map]}")

        # Deep-scrape each course
        for course_key, url in course_map.items():
            added = scrape_course(page, url, course_key, conn)
            all_added.extend(added)

        b.close()
    conn.close()

    # ── Build unified report ──────────────────────────────────
    conn2 = sqlite3.connect(DEADLINES_DB)
    pending = conn2.execute(
        "SELECT id, course, task, due, source FROM deadlines WHERE status != 'Done'"
    ).fetchall()
    conn2.close()
    from gemini_dashboard import deduplicate_tasks
    pending = deduplicate_tasks(pending)
    pending = [row for row in pending if has_concrete_due(row[3]) and is_active_due(row[3])]

    # Group pending by course
    by_course = {}
    for id_str, course, task, due, source in pending:
        by_course.setdefault(course, []).append((id_str, task, due, source))

    lines = [f"📚 <b>VLE Deep Scrape Done</b>  {len(all_added)} new task(s)\n"]
    lines.append(f"<b>Current Pending Tasks ({len(pending)})</b>\n")

    for code, tasks in by_course.items():
        lines.append(f"<b>── {code} ──</b>")
        for id_str, task, due, source in tasks:
            lines.append(f"  {id_str}. {task[:55]}\n      📅 {due[:50]}")
        lines.append("")

    if not all_added:
        lines.append("<i>No new VLE tasks were found in this pass. This usually means the pending list is unchanged, not that the scraper failed.</i>")

    lines.append("\n<i>/tasks — full task list  |  /summary — focused dashboard  |  /scrape — rescan now</i>")

    tg("\n".join(lines))
    ops_db.record_system_health(
        "vle_scraper",
        "ok",
        f"new_tasks={len(all_added)}; pending={len(pending)}; courses={len(course_map)}",
    )
    print(f"\nDone. {len(all_added)} new, {len(pending)} total pending.")


if __name__ == "__main__":
    run()
