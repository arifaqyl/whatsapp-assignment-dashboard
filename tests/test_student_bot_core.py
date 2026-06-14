import unittest
import importlib
import sys
import types
import sqlite3
import tempfile
import json
from pathlib import Path
from datetime import date

from deadline_utils import parse_due_date, tasks_match
import deadlines
import gemini_dashboard
from gemini_dashboard import deduplicate_tasks
from whatsapp_filters import is_relevant_message
import whatsapp_deadlines
from whatsapp_deadlines import infer_due_date, should_create_deadline


FIXTURES = json.loads((Path(__file__).parent / "fixtures" / "whatsapp_cases.json").read_text(encoding="utf-8"))


def _load_webhook_receiver():
    flask_mod = types.ModuleType("flask")

    class _DummyFlask:
        def __init__(self, *args, **kwargs):
            pass

        def route(self, *args, **kwargs):
            def decorator(fn):
                return fn

            return decorator

    flask_mod.Flask = _DummyFlask
    flask_mod.request = types.SimpleNamespace(get_json=lambda force=True: {})
    flask_mod.jsonify = lambda obj=None, **kwargs: obj if obj is not None else kwargs
    sys.modules.setdefault("flask", flask_mod)
    return importlib.import_module("webhook_receiver")


def _load_vle_scraper():
    config_mod = types.ModuleType("config")
    config_mod.BOT_TOKEN = ""
    config_mod.CHAT_ID = ""
    sys.modules.setdefault("config", config_mod)

    if "playwright" not in sys.modules:
        playwright_mod = types.ModuleType("playwright")
        sync_api_mod = types.ModuleType("playwright.sync_api")
        sync_api_mod.sync_playwright = lambda: None
        playwright_mod.sync_api = sync_api_mod
        sys.modules["playwright"] = playwright_mod
        sys.modules["playwright.sync_api"] = sync_api_mod

    return importlib.import_module("vle_scraper")


def _load_get_session():
    config_mod = types.ModuleType("config")
    config_mod.VLE_BASE_URL = "https://vle.unikl.edu.my"
    config_mod.VLE_EMAIL = "user@example.com"
    config_mod.VLE_PASSWORD = "secret"
    sys.modules["config"] = config_mod

    if "playwright" not in sys.modules:
        playwright_mod = types.ModuleType("playwright")
        sync_api_mod = types.ModuleType("playwright.sync_api")
        class DummyTimeoutError(Exception):
            pass
        sync_api_mod.TimeoutError = DummyTimeoutError
        sync_api_mod.sync_playwright = lambda: None
        playwright_mod.sync_api = sync_api_mod
        sys.modules["playwright"] = playwright_mod
        sys.modules["playwright.sync_api"] = sync_api_mod
    else:
        sync_api_mod = sys.modules["playwright.sync_api"]
        if not hasattr(sync_api_mod, "TimeoutError"):
            class DummyTimeoutError(Exception):
                pass
            sync_api_mod.TimeoutError = DummyTimeoutError

    if "get_session" in sys.modules:
        return importlib.reload(sys.modules["get_session"])
    return importlib.import_module("get_session")


def _load_bot():
    config_mod = types.ModuleType("config")
    config_mod.BOT_TOKEN = "token"
    config_mod.CHAT_ID = "716509225"
    config_mod.WAHA_URL = "http://localhost:2785"
    config_mod.WAHA_SESSION = "default"
    config_mod.WAHA_API_KEY = ""
    config_mod.WAHA_PAIR_NUMBER = ""
    sys.modules["config"] = config_mod

    requests_mod = types.ModuleType("requests")
    requests_mod.post = lambda *args, **kwargs: None
    sys.modules["requests"] = requests_mod

    vle_login_mod = types.ModuleType("vle_login")
    vle_login_mod.login_state = {
        "status": "idle",
        "message": "",
        "error": "",
        "code": None,
        "thread": None,
    }
    vle_login_mod.start_login_thread = lambda: True
    sys.modules["vle_login"] = vle_login_mod

    get_session_mod = types.ModuleType("get_session")
    get_session_mod.probe_saved_session = lambda: {"status": "expired"}
    get_session_mod.probe_login_flow = lambda max_seconds=8: {"status": "needs_approval"}
    sys.modules["get_session"] = get_session_mod

    waha_status_mod = types.ModuleType("waha_status")
    waha_status_mod.build_whatsapp_warning = lambda *args, **kwargs: ""
    waha_status_mod.get_session_status = lambda *args, **kwargs: {}
    sys.modules["waha_status"] = waha_status_mod

    if "bot" in sys.modules:
        return importlib.reload(sys.modules["bot"])
    return importlib.import_module("bot")


class DeadlineUtilsTests(unittest.TestCase):
    def test_parse_due_date_variants(self):
        self.assertEqual(parse_due_date("05 Jun 2026"), date(2026, 6, 5))
        self.assertEqual(parse_due_date("5 June 2026"), date(2026, 6, 5))
        self.assertEqual(parse_due_date("05/06/26"), date(2026, 6, 5))
        self.assertEqual(parse_due_date("9.6.2026"), date(2026, 6, 9))
        self.assertEqual(parse_due_date("Monday, June 8, 2026"), date(2026, 6, 8))
        self.assertTrue(parse_due_date("See VLE").year >= 9999)

    def test_tasks_match_sensitive_to_numbers(self):
        self.assertTrue(tasks_match("Assignment 2", "assignment 2"))
        self.assertFalse(tasks_match("Assignment 2", "Assignment 3"))


class WhatsappDeadlineTests(unittest.TestCase):
    def setUp(self):
        self.project_progress_case = FIXTURES["project_progress_reminder"]

    def test_infer_due_date_from_weekday_with_context(self):
        inferred = infer_due_date("please submit by Friday", "2026-06-03T10:00:00")
        self.assertEqual(inferred, date(2026, 6, 5))

    def test_infer_due_date_prefers_reschedule_target_date(self):
        inferred = infer_due_date(
            "school visit cancelled from 11 Jun 2026 to 19 Jun 2026",
            "2026-06-01T10:00:00",
        )
        self.assertEqual(inferred, date(2026, 6, 19))

    def test_infer_due_date_handles_instead_of_phrasing(self):
        case = FIXTURES["school_visit_reschedule"]
        inferred = infer_due_date(
            case["message"],
            case["timestamp_iso"],
        )
        self.assertEqual(inferred, date(2026, 6, 19))

    def test_infer_due_date_handles_dotted_numeric_exam_date(self):
        case = FIXTURES["oosad_exam_dotted_date"]
        inferred = infer_due_date(case["message"], case["timestamp_iso"])
        self.assertEqual(inferred, date(2026, 6, 9))

    def test_infer_due_date_uses_first_date_in_exam_date_list(self):
        case = FIXTURES["oosad_exam_date_list"]
        inferred = infer_due_date(case["message"], case["timestamp_iso"])
        self.assertEqual(inferred, date(2026, 6, 9))

    def test_infer_due_date_handles_oop_detail_block(self):
        case = FIXTURES["oop_exam_detail_block"]
        inferred = infer_due_date(case["message"], case["timestamp_iso"])
        self.assertEqual(inferred, date(2026, 6, 8))

    def test_should_create_deadline_rejects_noise(self):
        self.assertFalse(
            should_create_deadline(
                "OOSAD",
                "i'm from bo1, yesterday i got PE event and today i got presentation for subject OOP",
                date(2026, 6, 5),
            )
        )

    def test_should_create_deadline_accepts_lecture_detail_updates(self):
        self.assertTrue(
            should_create_deadline(
                "DATABASE BO1",
                "Database lecture details final 19 Jun 2026 venue updated",
                date(2026, 6, 19),
            )
        )

    def test_should_create_deadline_accepts_dotted_exam_notice(self):
        self.assertTrue(
            should_create_deadline(
                "OOSAD March 2026 MIIT",
                "Pls take note Group B01, exam on 9.6.2026, 1.00-2.15, venue 1807 ya",
                date(2026, 6, 9),
                reference_date=date(2026, 6, 1),
            )
        )

    def test_should_create_deadline_accepts_oop_detail_block(self):
        self.assertTrue(
            should_create_deadline(
                "OOP Group A1",
                "Monday, June 8, 2026.\n\nDetails:\n\nTime: 2:30 PM\nDuration: 1 hour 30 minutes\nPlace: lab\nFormat: 65 questions, 80 marks",
                date(2026, 6, 8),
                reference_date=date(2026, 6, 1),
            )
        )

    def test_should_create_deadline_rejects_online_class_notice(self):
        self.assertFalse(
            should_create_deadline(
                "Professional English 1 L07",
                "Salam & good evening all... The class will be conducted online tomorrow morning.",
                date(2026, 6, 20),
            )
        )

    def test_extract_structured_deadlines_from_multi_part_message(self):
        case = self.project_progress_case
        entries = whatsapp_deadlines._extract_structured_deadlines(
            case["group_name"],
            case["message"],
            case["timestamp_iso"],
        )
        self.assertEqual(
            entries,
            [(item["task"], date.fromisoformat(item["due"])) for item in case["expected_deadlines"]],
        )

    def test_sync_message_creates_multiple_deadlines_from_multi_part_message(self):
        case = self.project_progress_case
        with tempfile.TemporaryDirectory() as td:
            db_path = Path(td) / "deadlines.db"
            conn = sqlite3.connect(db_path)
            conn.execute(
                """
                CREATE TABLE deadlines (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    task TEXT NOT NULL,
                    course TEXT NOT NULL,
                    due TEXT NOT NULL,
                    status TEXT DEFAULT 'Pending',
                    source TEXT DEFAULT 'manual',
                    added TEXT DEFAULT CURRENT_TIMESTAMP
                )
                """
            )
            conn.commit()
            conn.close()

            old_db = deadlines.DB
            deadlines.DB = str(db_path)
            try:
                created = whatsapp_deadlines.sync_message(
                    case["group_name"],
                    case["message"],
                    case["timestamp_iso"],
                )
                self.assertEqual(len(created), 3)

                conn = sqlite3.connect(db_path)
                rows = conn.execute(
                    "SELECT task, due FROM deadlines ORDER BY due, task"
                ).fetchall()
                conn.close()
                self.assertEqual(
                    rows,
                    [(item["task"], date.fromisoformat(item["due"]).strftime("%d %b %Y")) for item in case["expected_deadlines"]],
                )
            finally:
                deadlines.DB = old_db

    def test_whatsapp_reschedule_updates_existing_due(self):
        with tempfile.TemporaryDirectory() as td:
            db_path = Path(td) / "deadlines.db"
            conn = sqlite3.connect(db_path)
            conn.execute(
                """
                CREATE TABLE deadlines (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    task TEXT NOT NULL,
                    course TEXT NOT NULL,
                    due TEXT NOT NULL,
                    status TEXT DEFAULT 'Pending',
                    source TEXT DEFAULT 'manual',
                    added TEXT DEFAULT CURRENT_TIMESTAMP
                )
                """
            )
            conn.execute(
                "INSERT INTO deadlines (task, course, due, source) VALUES (?,?,?,?)",
                ("School visit activity", "PE", "11 Jun 2026", "whatsapp"),
            )
            conn.commit()
            conn.close()

            old_db = deadlines.DB
            deadlines.DB = str(db_path)
            try:
                row_id, status = deadlines.add(
                    "School visit activity",
                    "PE",
                    "19 Jun 2026",
                    source="whatsapp-reschedule",
                )
                self.assertEqual(status, "updated")
                self.assertIsNotNone(row_id)

                conn = sqlite3.connect(db_path)
                row = conn.execute("SELECT due, source FROM deadlines WHERE id = 1").fetchone()
                conn.close()
                self.assertEqual(row[0], "19 Jun 2026")
                self.assertEqual(row[1], "whatsapp-reschedule")
            finally:
                deadlines.DB = old_db

    def test_whatsapp_exam_update_prefers_earlier_corrected_due(self):
        with tempfile.TemporaryDirectory() as td:
            db_path = Path(td) / "deadlines.db"
            conn = sqlite3.connect(db_path)
            conn.execute(
                """
                CREATE TABLE deadlines (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    task TEXT NOT NULL,
                    course TEXT NOT NULL,
                    due TEXT NOT NULL,
                    status TEXT DEFAULT 'Pending',
                    source TEXT DEFAULT 'manual',
                    added TEXT DEFAULT CURRENT_TIMESTAMP
                )
                """
            )
            conn.execute(
                "INSERT INTO deadlines (task, course, due, source) VALUES (?,?,?,?)",
                ("Exam 9 & 10 June 2026", "OOSAD", "10 Jun 2026", "whatsapp"),
            )
            conn.commit()
            conn.close()

            old_db = deadlines.DB
            deadlines.DB = str(db_path)
            try:
                row_id, status = deadlines.add(
                    "Exam",
                    "OOSAD",
                    "09 Jun 2026",
                    source="whatsapp",
                )
                self.assertEqual(status, "updated")
                self.assertIsNotNone(row_id)

                conn = sqlite3.connect(db_path)
                row = conn.execute("SELECT due, source FROM deadlines WHERE id = 1").fetchone()
                conn.close()
                self.assertEqual(row[0], "09 Jun 2026")
                self.assertEqual(row[1], "whatsapp")
            finally:
                deadlines.DB = old_db

    def test_whatsapp_cancel_removes_existing_deadline(self):
        with tempfile.TemporaryDirectory() as td:
            db_path = Path(td) / "deadlines.db"
            conn = sqlite3.connect(db_path)
            conn.execute(
                """
                CREATE TABLE deadlines (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    task TEXT NOT NULL,
                    course TEXT NOT NULL,
                    due TEXT NOT NULL,
                    status TEXT DEFAULT 'Pending',
                    source TEXT DEFAULT 'manual',
                    added TEXT DEFAULT CURRENT_TIMESTAMP
                )
                """
            )
            conn.execute(
                "INSERT INTO deadlines (task, course, due, source) VALUES (?,?,?,?)",
                ("School visit activity", "PE", "11 Jun 2026", "whatsapp"),
            )
            conn.commit()
            conn.close()

            old_db = deadlines.DB
            deadlines.DB = str(db_path)
            try:
                removed = whatsapp_deadlines.sync_message(
                    "Project Proposal PE Group 1",
                    "school visit cancelled 11 Jun 2026",
                    "2026-06-01T10:00:00",
                )
                self.assertTrue(removed)

                conn = sqlite3.connect(db_path)
                rows = conn.execute("SELECT id FROM deadlines").fetchall()
                conn.close()
                self.assertEqual(rows, [])
            finally:
                deadlines.DB = old_db


class DashboardDedupTests(unittest.TestCase):
    def test_deduplicate_tasks_prefers_concrete_due_and_better_source(self):
        rows = [
            (1, "PE", "Assignment 4", "See VLE", "vle-clp"),
            (2, "PE", "Assignment 4: Resume, Cover Letter & Mock Job Interview", "14 Jun 2026", "whatsapp"),
        ]
        deduped = deduplicate_tasks(rows)
        self.assertEqual(len(deduped), 1)
        row = deduped[0]
        self.assertEqual(row[1], "PE")
        self.assertIn("Assignment 4", row[2])
        self.assertEqual(row[3], "14 Jun 2026")
        self.assertEqual(row[4], "whatsapp")

    def test_wa_summary_helpers_trim_greeting_and_keep_range(self):
        message = (
            "PROJECT PROGRESS SUBMISSION & ASSESSMENT REMINDER\n\n"
            "Dear BO1 and BO2,\n\n"
            "Submission Deadline: 5 June 2026 (Friday)\n\n"
            "Project Presentation\n"
            "Presentations will commence during Week 14 (8 - 12 June 2026).\n"
        )
        self.assertEqual(gemini_dashboard._wa_range_hint(message), "8-12 Jun 2026")
        self.assertEqual(
            gemini_dashboard._summarize_wa_message(message, limit=160),
            "Deadline 5 June 2026 (Friday) | Presentation 8 - 12 June 2026",
        )

    def test_wa_summary_helpers_capture_exam_details(self):
        message = "Pls take note Group B01, exam on 9.6.2026, 1.00-2.15, venue 1807 ya"
        summary = gemini_dashboard._summarize_wa_message(message, limit=160)
        self.assertIn("Exam", summary)
        self.assertIn("Time 1.00-2.15", summary)
        self.assertIn("Place 1807", summary)

    def test_wa_summary_helpers_capture_oop_detail_block(self):
        message = (
            "Monday, June 8, 2026.\n\nDetails:\n\nTime: 2:30 PM\nDuration: 1 hour 30 minutes\n"
            "Place: lab\nFormat: 65 questions, 80 marks"
        )
        summary = gemini_dashboard._summarize_wa_message(message, limit=200)
        self.assertIn("Exam", summary)
        self.assertIn("Time 2:30 PM", summary)
        self.assertIn("Duration 1 hour 30 minutes", summary)
        self.assertIn("Place lab", summary)
        self.assertIn("Format 65 questions", summary)


class VleScraperHeuristicsTests(unittest.TestCase):
    def test_resource_hints_uses_pdf_name_before_download(self):
        vle_scraper = _load_vle_scraper()
        self.assertEqual(
            vle_scraper._resource_hints("Project Brief", "https://vle.example.com/file/Final_Project_Deadline_14_Jun_2026.pdf"),
            "14 Jun 2026",
        )

    def test_low_signal_resource_detection(self):
        vle_scraper = _load_vle_scraper()
        self.assertTrue(vle_scraper._is_low_signal_resource("Week 3 Lecture Notes"))
        self.assertFalse(vle_scraper._is_low_signal_resource("Assignment 4 Submission Brief"))

    def test_clp_filter_rejects_placeholder_lines(self):
        vle_scraper = _load_vle_scraper()
        self.assertFalse(vle_scraper._should_keep_clp_task("assignment", "See VLE"))
        self.assertTrue(vle_scraper._should_keep_clp_task("Assignment 4: Resume, Cover Letter & Mock Job Interview", "14 Jun 2026"))

    def test_extract_page_text_deadlines_finds_deadline_block(self):
        vle_scraper = _load_vle_scraper()
        body_text = (
            "Final Project\n"
            "Please complete the report carefully.\n"
            "Deadline for submission: June 18, 2026 10:00 AM\n"
        )
        self.assertEqual(
            vle_scraper._extract_page_text_deadlines(body_text),
            [("Final Project", "June 18, 2026 10:00 AM")],
        )

    def test_extract_page_text_deadlines_finds_task_then_nearby_date_line(self):
        vle_scraper = _load_vle_scraper()
        body_text = (
            "OOSAD Exam\n"
            "Date: 9 June 2026\n"
            "Venue: Dewan\n"
        )
        self.assertEqual(
            vle_scraper._extract_page_text_deadlines(body_text),
            [("OOSAD Exam", "9 June 2026")],
        )

    def test_extract_page_text_deadlines_handles_dotted_numeric_dates(self):
        vle_scraper = _load_vle_scraper()
        body_text = (
            "OOSAD Exam\n"
            "Date: 9.6.2026\n"
            "Venue: Dewan\n"
        )
        self.assertEqual(
            vle_scraper._extract_page_text_deadlines(body_text),
            [("OOSAD Exam", "9.6.2026")],
        )

    def test_purge_manual_placeholder_rows_removes_check_vle_rows(self):
        vle_scraper = _load_vle_scraper()
        conn = sqlite3.connect(":memory:")
        conn.execute(
            """
            CREATE TABLE deadlines (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                task TEXT NOT NULL,
                course TEXT NOT NULL,
                due TEXT NOT NULL,
                status TEXT DEFAULT 'Pending',
                source TEXT DEFAULT 'manual',
                added TEXT DEFAULT CURRENT_TIMESTAMP
            )
            """
        )
        conn.execute(
            "INSERT INTO deadlines (task, course, due, source) VALUES (?,?,?,?)",
            ("Assignments (check VLE/OOSAD)", "OOSAD", "~1 Jun 2026", "manual"),
        )
        conn.execute(
            "INSERT INTO deadlines (task, course, due, source) VALUES (?,?,?,?)",
            ("Exam", "OOSAD", "09 Jun 2026", "vle-oosad"),
        )
        conn.commit()

        removed = vle_scraper._purge_manual_placeholder_rows(conn, "OOSAD")
        rows = conn.execute("SELECT task, source FROM deadlines ORDER BY id").fetchall()
        conn.close()

        self.assertEqual(removed, 1)
        self.assertEqual(rows, [("Exam", "vle-oosad")])


class GetSessionProbeTests(unittest.TestCase):
    def test_extract_number_match_value(self):
        get_session = _load_get_session()
        text = "Approve sign in request\nEnter the number shown to sign in.\n42"
        self.assertEqual(get_session._extract_number_match_value(text), "42")

    def test_probe_saved_session_reports_missing_file(self):
        get_session = _load_get_session()
        old_exists = get_session.os.path.exists
        try:
            get_session.os.path.exists = lambda path: False
            result = get_session.probe_saved_session()
        finally:
            get_session.os.path.exists = old_exists

        self.assertEqual(result["status"], "missing")
        self.assertFalse(result["exists"])
        self.assertIn("missing", result["detail"])

    def test_probe_saved_session_reports_expired_redirect(self):
        get_session = _load_get_session()

        class FakePage:
            def __init__(self, url):
                self.url = url

            def goto(self, *_args, **_kwargs):
                return None

            def wait_for_load_state(self, *_args, **_kwargs):
                return None

        class FakeContext:
            def __init__(self, url):
                self._url = url

            def new_page(self):
                return FakePage(self._url)

        class FakeBrowser:
            def __init__(self, url):
                self._url = url

            def new_context(self, **_kwargs):
                return FakeContext(self._url)

            def close(self):
                return None

        class FakePlaywright:
            def __init__(self, url):
                self.chromium = types.SimpleNamespace(launch=lambda **_kwargs: FakeBrowser(url))

            def __enter__(self):
                return self

            def __exit__(self, *_args):
                return False

        old_exists = get_session.os.path.exists
        old_getmtime = get_session.os.path.getmtime
        old_time = get_session.time.time
        old_sync = get_session.sync_playwright
        try:
            get_session.os.path.exists = lambda path: True
            get_session.os.path.getmtime = lambda path: 1000
            get_session.time.time = lambda: 1600
            get_session.sync_playwright = lambda: FakePlaywright(
                "https://vle.unikl.edu.my/login/index.php?loginredirect=1"
            )
            result = get_session.probe_saved_session()
        finally:
            get_session.os.path.exists = old_exists
            get_session.os.path.getmtime = old_getmtime
            get_session.time.time = old_time
            get_session.sync_playwright = old_sync

        self.assertEqual(result["status"], "expired")
        self.assertEqual(result["age_minutes"], 10)
        self.assertIn("redirects to login", result["detail"])

    def test_probe_login_flow_reports_needs_code(self):
        get_session = _load_get_session()

        class FakeLocator:
            def __init__(self, visible):
                self._visible = visible
                self.first = self

            def count(self):
                return 1 if self._visible else 0

            def is_visible(self):
                return self._visible

            def click(self, **_kwargs):
                return None

            def fill(self, *_args, **_kwargs):
                return None

        class FakePage:
            def __init__(self):
                self.url = "https://login.microsoftonline.com/example"

            def goto(self, *_args, **_kwargs):
                return None

            def locator(self, selector):
                otp_selectors = {
                    'input[name="otc"]',
                    'input[name="code"]',
                    'input[inputmode="numeric"]',
                    'input[autocomplete="one-time-code"]',
                    'input#idTxtBx_SAOTCC_OTC',
                }
                return FakeLocator(selector in otp_selectors)

            def wait_for_timeout(self, *_args, **_kwargs):
                return None

        class FakeContext:
            def new_page(self):
                return FakePage()

        class FakeBrowser:
            def new_context(self):
                return FakeContext()

            def close(self):
                return None

        class FakePlaywright:
            def __init__(self):
                self.chromium = types.SimpleNamespace(launch=lambda **_kwargs: FakeBrowser())

            def __enter__(self):
                return self

            def __exit__(self, *_args):
                return False

        old_sync = get_session.sync_playwright
        try:
            get_session.sync_playwright = lambda: FakePlaywright()
            result = get_session.probe_login_flow(max_seconds=1)
        finally:
            get_session.sync_playwright = old_sync

        self.assertEqual(result["status"], "needs_code")
        self.assertIn("OTP", result["detail"])

    def test_probe_login_flow_prefers_number_match_over_code(self):
        get_session = _load_get_session()

        class FakeLocator:
            def __init__(self, visible):
                self._visible = visible
                self.first = self

            def count(self):
                return 1 if self._visible else 0

            def is_visible(self):
                return self._visible

            def click(self, **_kwargs):
                return None

            def fill(self, *_args, **_kwargs):
                return None

        class FakePage:
            def __init__(self):
                self.url = "https://login.microsoftonline.com/example"

            def goto(self, *_args, **_kwargs):
                return None

            def locator(self, selector):
                visible_selectors = {
                    'input[name="code"]',
                    "text=/enter the number shown/i",
                }
                return FakeLocator(selector in visible_selectors)

            def wait_for_timeout(self, *_args, **_kwargs):
                return None

        class FakeContext:
            def new_page(self):
                return FakePage()

        class FakeBrowser:
            def new_context(self):
                return FakeContext()

            def close(self):
                return None

        class FakePlaywright:
            def __init__(self):
                self.chromium = types.SimpleNamespace(launch=lambda **_kwargs: FakeBrowser())

            def __enter__(self):
                return self

            def __exit__(self, *_args):
                return False

        old_sync = get_session.sync_playwright
        try:
            get_session.sync_playwright = lambda: FakePlaywright()
            result = get_session.probe_login_flow(max_seconds=1)
        finally:
            get_session.sync_playwright = old_sync

        self.assertEqual(result["status"], "needs_approval")
        self.assertIn("number-match", result["detail"])


class BotMfaPromptTests(unittest.TestCase):
    def test_login_message_explains_number_match_without_code(self):
        bot = _load_bot()
        sent = []
        old_send = bot.send
        old_start = bot.start_login_thread
        try:
            bot.send = sent.append
            bot.start_login_thread = lambda: True
            bot.handle(
                {
                    "message": {
                        "text": "/login",
                        "chat": {"id": "716509225"},
                    }
                }
            )
        finally:
            bot.send = old_send
            bot.start_login_thread = old_start

        self.assertEqual(len(sent), 1)
        self.assertIn("needs_approval", sent[0])
        self.assertIn("If Microsoft shows a number", sent[0])
        self.assertIn("Only use <code>/code 123456</code>", sent[0])

    def test_vle_status_approval_message_says_no_code_needed_yet(self):
        bot = _load_bot()
        sent = []
        old_send = bot.send
        old_probe_saved = bot.probe_saved_session
        old_probe_flow = bot.probe_login_flow
        try:
            bot.send = sent.append
            bot.probe_saved_session = lambda: {
                "status": "expired",
                "age_minutes": 12,
                "detail": "redirects to login",
                "final_url": "https://vle.unikl.edu.my/login/index.php?loginredirect=1",
            }
            bot.probe_login_flow = lambda max_seconds=8: {
                "status": "needs_approval",
                "detail": "Microsoft approval required",
                "final_url": "https://login.microsoftonline.com/example",
            }
            bot.handle(
                {
                    "message": {
                        "text": "/vle_status",
                        "chat": {"id": "716509225"},
                    }
                }
            )
        finally:
            bot.send = old_send
            bot.probe_saved_session = old_probe_saved
            bot.probe_login_flow = old_probe_flow

        self.assertEqual(len(sent), 1)
        self.assertIn("needs_approval", sent[0])
        self.assertIn("enter that number in Authenticator", sent[0])
        self.assertIn("No <code>/code</code> needed yet", sent[0])

    def test_vle_status_prefers_valid_session_over_preview(self):
        bot = _load_bot()
        sent = []
        old_send = bot.send
        old_probe_saved = bot.probe_saved_session
        old_probe_flow = bot.probe_login_flow
        try:
            bot.send = sent.append
            bot.probe_saved_session = lambda: {
                "status": "valid",
                "age_minutes": 1,
                "detail": "saved session reaches /my/",
                "final_url": "https://vle.unikl.edu.my/my/",
            }
            bot.probe_login_flow = lambda max_seconds=8: {
                "status": "needs_approval",
                "detail": "Microsoft auth page is active; likely waiting for approval or next factor",
                "final_url": "https://login.microsoftonline.com/example",
            }
            bot.handle(
                {
                    "message": {
                        "text": "/vle_status",
                        "chat": {"id": "716509225"},
                    }
                }
            )
        finally:
            bot.send = old_send
            bot.probe_saved_session = old_probe_saved
            bot.probe_login_flow = old_probe_flow

        self.assertEqual(len(sent), 1)
        self.assertIn("Current session: <b>usable</b>. No login action needed right now.", sent[0])
        self.assertIn("Fresh-login preview: a brand new sign-in would ask for phone approval / number match.", sent[0])


class WebhookParsingTests(unittest.TestCase):
    def test_extract_message_text_handles_multiple_payload_shapes(self):
        webhook_receiver = _load_webhook_receiver()
        msg = {"_data": {"body": "", "caption": "Attached brief"}}
        self.assertEqual(webhook_receiver._extract_message_text(msg), "Attached brief")

    def test_parse_waha_payload_and_monitored_group_match(self):
        webhook_receiver = _load_webhook_receiver()
        payload = {
            "event": "message",
            "payload": {
                "from": "120363413978925898@g.us",
                "timestamp": 1717500000,
                "_data": {"chat": {"name": "DATABASE BO1"}, "notifyName": "Aina", "body": "deadline 14 Jun 2026"},
            },
        }
        result = webhook_receiver.parse_waha_payload(payload)
        self.assertIsNotNone(result)
        is_group, group_name, sender, body, _ = result
        self.assertTrue(is_group)
        self.assertEqual(group_name, "DATABASE BO1")
        self.assertEqual(sender, "Aina")
        self.assertEqual(body, "deadline 14 Jun 2026")
        self.assertTrue(webhook_receiver.is_monitored_group("database bo1"))

    def test_save_message_deduplicates_exact_rows(self):
        webhook_receiver = _load_webhook_receiver()
        with tempfile.TemporaryDirectory() as td:
            db_path = Path(td) / "messages.db"
            conn = sqlite3.connect(db_path)
            conn.execute(
                """
                CREATE TABLE messages (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    timestamp TEXT NOT NULL,
                    group_name TEXT NOT NULL,
                    sender TEXT,
                    message TEXT NOT NULL,
                    raw_json TEXT,
                    done INTEGER DEFAULT 0
                )
                """
            )
            conn.commit()
            conn.close()

            old_path = webhook_receiver.DB_PATH
            webhook_receiver.DB_PATH = str(db_path)
            try:
                webhook_receiver.init_db()
                first, first_id = webhook_receiver.save_message("DATABASE BO1", "Aina", "deadline 14 Jun 2026", 1717500000, "{}")
                second, second_id = webhook_receiver.save_message("DATABASE BO1", "Aina", "deadline 14 Jun 2026", 1717500000, "{}")
                self.assertTrue(first)
                self.assertEqual(first_id, 1)
                self.assertFalse(second)
                self.assertEqual(second_id, 1)
            finally:
                webhook_receiver.DB_PATH = old_path

    def test_is_relevant_message_accepts_lecture_detail_updates(self):
        self.assertTrue(
            is_relevant_message(
                "DATABASE BO1",
                "Database lecture details final 19 Jun 2026 venue updated",
            )
        )


if __name__ == "__main__":
    unittest.main()
