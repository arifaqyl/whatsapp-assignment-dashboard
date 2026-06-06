import unittest
import importlib
import sys
import types
import sqlite3
import tempfile
from pathlib import Path
from datetime import date

from deadline_utils import parse_due_date, tasks_match
import deadlines
from gemini_dashboard import deduplicate_tasks
import whatsapp_deadlines
from whatsapp_deadlines import infer_due_date, should_create_deadline


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


class DeadlineUtilsTests(unittest.TestCase):
    def test_parse_due_date_variants(self):
        self.assertEqual(parse_due_date("05 Jun 2026"), date(2026, 6, 5))
        self.assertEqual(parse_due_date("5 June 2026"), date(2026, 6, 5))
        self.assertEqual(parse_due_date("05/06/26"), date(2026, 6, 5))
        self.assertTrue(parse_due_date("See VLE").year >= 9999)

    def test_tasks_match_sensitive_to_numbers(self):
        self.assertTrue(tasks_match("Assignment 2", "assignment 2"))
        self.assertFalse(tasks_match("Assignment 2", "Assignment 3"))


class WhatsappDeadlineTests(unittest.TestCase):
    def test_infer_due_date_from_weekday_with_context(self):
        inferred = infer_due_date("please submit by Friday", "2026-06-03T10:00:00")
        self.assertEqual(inferred, date(2026, 6, 5))

    def test_infer_due_date_prefers_reschedule_target_date(self):
        inferred = infer_due_date(
            "school visit cancelled from 11 Jun 2026 to 19 Jun 2026",
            "2026-06-01T10:00:00",
        )
        self.assertEqual(inferred, date(2026, 6, 19))

    def test_should_create_deadline_rejects_noise(self):
        self.assertFalse(
            should_create_deadline(
                "OOSAD",
                "i'm from bo1, yesterday i got PE event and today i got presentation for subject OOP",
                date(2026, 6, 5),
            )
        )

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
                first = webhook_receiver.save_message("DATABASE BO1", "Aina", "deadline 14 Jun 2026", 1717500000, "{}")
                second = webhook_receiver.save_message("DATABASE BO1", "Aina", "deadline 14 Jun 2026", 1717500000, "{}")
                self.assertTrue(first)
                self.assertFalse(second)
            finally:
                webhook_receiver.DB_PATH = old_path


if __name__ == "__main__":
    unittest.main()
