import sqlite3
import tempfile
import unittest
from pathlib import Path
import os
import base64
from datetime import datetime, timedelta
from werkzeug.test import Client
from werkzeug.wrappers import Response as WerkzeugResponse

import db
import deadlines
import deploy_ops_console
import run_ops_console
import ops_console.app as ops_app_module
import ops_console.services as ops_services
from ops_console.app import create_app


class OpsConsoleTests(unittest.TestCase):
    def setUp(self):
        self.tempdir = tempfile.TemporaryDirectory()
        root = Path(self.tempdir.name)
        self.messages_db = root / "messages.db"
        self.deadlines_db = root / "deadlines.db"

        self.old_messages_db = db.DB
        self.old_deadlines_db = deadlines.DB
        db.DB = str(self.messages_db)
        deadlines.DB = str(self.deadlines_db)

        db.init()
        deadlines.init()

        queue_id, _ = db.enqueue_evidence_item(
            source_type="whatsapp",
            source_row_id=12,
            group_name="DATABASE BO1",
            course="DATABASE",
            title="Aina in DATABASE BO1",
            message="deadline moved maybe next friday",
            reason_code="no_deadline_created",
            evidence_preview="deadline moved maybe next friday",
        )
        db.record_operator_action(queue_id, "queue_created", actor="system")
        db.record_system_health("webhook_receiver", "ok", "group=DATABASE BO1; saved=True")
        db.record_system_health("whatsapp_promotion", "error", "parser timeout")

        conn = sqlite3.connect(str(self.deadlines_db))
        conn.execute(
            "INSERT INTO deadlines (task, course, due, source) VALUES (?,?,?,?)",
            ("Assignment 4", "PE", "14 Jun 2026", "whatsapp"),
        )
        conn.commit()
        conn.close()

        conn = sqlite3.connect(str(self.messages_db))
        conn.execute(
            "INSERT INTO messages (id, timestamp, group_name, sender, message, raw_json, done) VALUES (?,?,?,?,?,?,0)",
            (
                12,
                "2026-06-14T10:00:00",
                "DATABASE BO1",
                "Aina",
                "deadline moved maybe next friday",
                '{"message_id":"abc123","source":"waha"}',
            ),
        )
        conn.commit()
        conn.close()

        self.app = create_app()
        self.client = self.app.test_client()

    def tearDown(self):
        db.DB = self.old_messages_db
        deadlines.DB = self.old_deadlines_db
        self.tempdir.cleanup()

    def test_queue_page_renders_pending_item(self):
        response = self.client.get("/queue")
        body = response.get_data(as_text=True)
        self.assertEqual(response.status_code, 200)
        self.assertIn("Unresolved Evidence Queue", body)
        self.assertIn("pending=1", body)
        self.assertIn("all=1", body)
        self.assertIn("no_deadline_created", body)
        self.assertIn("DATABASE BO1", body)
        self.assertIn("page 1 of 1", body)

    def test_health_page_renders_snapshots_and_counts(self):
        response = self.client.get("/health")
        body = response.get_data(as_text=True)
        self.assertEqual(response.status_code, 200)
        self.assertIn("System Health", body)
        self.assertIn("critical=1", body)
        self.assertIn("warn=0", body)
        self.assertIn("ok=2", body)
        self.assertIn("Attention", body)
        self.assertIn("webhook_receiver", body)
        self.assertIn("whatsapp_promotion", body)
        self.assertIn("whatsapp_promotion=critical", body)
        self.assertIn("whatsapp / no_deadline_created: 1", body)
        self.assertIn("pending deadlines: 1", body)
        self.assertIn("/queue?queue_status=pending", body)

    def test_health_page_marks_stale_webhook_receiver_critical(self):
        stale = (datetime.now() - timedelta(hours=3)).isoformat()
        conn = sqlite3.connect(str(self.messages_db))
        conn.execute(
            """
            UPDATE system_health
            SET last_status='ok', last_success_at=?, last_failure_at=NULL, updated_at=?
            WHERE component='webhook_receiver'
            """,
            (stale, stale),
        )
        conn.commit()
        conn.close()
        response = self.client.get("/health")
        body = response.get_data(as_text=True)
        self.assertIn("webhook_receiver=critical", body)

    def test_health_page_marks_stale_daily_digest_warn(self):
        stale = (datetime.now() - timedelta(hours=20)).isoformat()
        conn = sqlite3.connect(str(self.messages_db))
        conn.execute(
            """
            INSERT INTO system_health (component, last_status, last_success_at, last_failure_at, details, updated_at)
            VALUES (?,?,?,?,?,?)
            ON CONFLICT(component) DO UPDATE SET
                last_status=excluded.last_status,
                last_success_at=excluded.last_success_at,
                last_failure_at=excluded.last_failure_at,
                details=excluded.details,
                updated_at=excluded.updated_at
            """,
            ("daily_digest", "ok", stale, None, "digest stale", stale),
        )
        conn.commit()
        conn.close()
        response = self.client.get("/health")
        body = response.get_data(as_text=True)
        self.assertIn("daily_digest=warn", body)

    def test_api_ping_returns_ok_json(self):
        response = self.client.get("/api/ping")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json["status"], "ok")
        self.assertEqual(response.json["service"], "ops_console")

    def test_queue_item_page_renders_action_history(self):
        response = self.client.get("/queue/1")
        body = response.get_data(as_text=True)
        self.assertEqual(response.status_code, 200)
        self.assertIn("Queue Item #1", body)
        self.assertIn("queue_created", body)
        self.assertIn("deadline moved maybe next friday", body)
        self.assertIn("message_id=12", body)
        self.assertIn("abc123", body)
        self.assertIn("waha", body)

    def test_queue_item_page_includes_back_link_with_context(self):
        response = self.client.get("/queue/1?queue_status=pending&source=whatsapp&reason=no_deadline_created&page=2&page_size=25")
        body = response.get_data(as_text=True)
        self.assertEqual(response.status_code, 200)
        self.assertIn("/queue?queue_status=pending&amp;source=whatsapp&amp;reason=no_deadline_created&amp;page=2&amp;page_size=25", body)

    def test_vle_queue_item_page_renders_source_reference(self):
        conn = sqlite3.connect(str(self.messages_db))
        conn.execute("DELETE FROM evidence_queue")
        conn.commit()
        conn.close()
        queue_id, _ = db.enqueue_evidence_item(
            source_type="vle",
            course="DATABASE",
            title="Assignment Brief",
            message="https://vle.example.edu.my/mod/resource/view.php?id=999",
            reason_code="missing_due",
            evidence_preview="Assignment Brief | no due date found",
        )
        response = self.client.get(f"/queue/{queue_id}")
        body = response.get_data(as_text=True)
        self.assertEqual(response.status_code, 200)
        self.assertIn("Source Reference", body)
        self.assertIn("resource_url", body)
        self.assertIn("Assignment Brief", body)
        self.assertIn("host=vle.example.edu.my", body)
        self.assertIn("path=/mod/resource/view.php", body)
        self.assertIn("query=id=999", body)
        self.assertIn("mod/resource/view.php?id=999", body)

    def test_queue_page_filters_by_source(self):
        conn = sqlite3.connect(str(self.messages_db))
        conn.execute("DELETE FROM evidence_queue")
        conn.commit()
        conn.close()
        db.enqueue_evidence_item(
            source_type="whatsapp",
            source_row_id=12,
            group_name="DATABASE BO1",
            course="DATABASE",
            title="WhatsApp Item",
            message="deadline moved maybe next friday",
            reason_code="no_deadline_created",
            evidence_preview="deadline moved maybe next friday",
        )
        queue_id, _ = db.enqueue_evidence_item(
            source_type="vle",
            course="DATABASE",
            title="VLE Item",
            message="https://vle.example.edu.my/mod/resource/view.php?id=999",
            reason_code="missing_due",
            evidence_preview="Assignment Brief | no due date found",
        )
        response = self.client.get("/queue?source=vle")
        body = response.get_data(as_text=True)
        self.assertIn("VLE Item", body)
        self.assertNotIn("WhatsApp Item", body)

    def test_queue_page_count_chip_links_to_filtered_view(self):
        conn = sqlite3.connect(str(self.messages_db))
        conn.execute("DELETE FROM evidence_queue")
        conn.commit()
        conn.close()
        db.enqueue_evidence_item(
            source_type="vle",
            course="DATABASE",
            title="VLE Item",
            message="https://vle.example.edu.my/mod/resource/view.php?id=999",
            reason_code="missing_due",
            evidence_preview="Assignment Brief | no due date found",
        )
        response = self.client.get("/queue")
        body = response.get_data(as_text=True)
        self.assertIn("/queue?queue_status=pending&amp;source=vle&amp;reason=missing_due&amp;page=1&amp;page_size=10", body)

    def test_queue_page_filters_by_status(self):
        self.client.post("/queue/1/dismiss", follow_redirects=True)
        pending = self.client.get("/queue?queue_status=pending").get_data(as_text=True)
        dismissed = self.client.get("/queue?queue_status=dismissed").get_data(as_text=True)
        self.assertNotIn("DATABASE BO1", pending)
        self.assertIn("DATABASE BO1", dismissed)

    def test_queue_page_supports_pagination(self):
        conn = sqlite3.connect(str(self.messages_db))
        conn.execute("DELETE FROM evidence_queue")
        conn.commit()
        conn.close()
        for idx in range(1, 16):
            db.enqueue_evidence_item(
                source_type="whatsapp",
                source_row_id=idx,
                group_name="DATABASE BO1",
                course="DATABASE",
                title=f"Item {idx}",
                message=f"message {idx}",
                reason_code="no_deadline_created",
                evidence_preview=f"preview {idx}",
            )
        first_page = self.client.get("/queue?page=1&page_size=5")
        second_page = self.client.get("/queue?page=2&page_size=5")
        first_body = first_page.get_data(as_text=True)
        second_body = second_page.get_data(as_text=True)
        self.assertIn("page 1 of 3", first_body)
        self.assertIn("next", first_body)
        self.assertIn("page 2 of 3", second_body)
        self.assertIn("prev", second_body)

    def test_enqueue_updates_existing_pending_item(self):
        queue_id, status = db.enqueue_evidence_item(
            source_type="whatsapp",
            source_row_id=12,
            group_name="DATABASE BO1",
            course="DATABASE",
            title="Aina in DATABASE BO1",
            message="deadline shifted to maybe next monday",
            reason_code="no_deadline_created",
            evidence_preview="deadline shifted to maybe next monday",
        )
        self.assertEqual(status, "updated")
        items = db.get_queue_items()
        self.assertEqual(len(items), 1)
        self.assertEqual(items[0]["id"], queue_id)
        self.assertIn("next monday", items[0]["evidence_preview"])

    def test_dismiss_queue_item_changes_status_and_logs_action(self):
        response = self.client.post("/queue/1/dismiss", follow_redirects=True)
        self.assertEqual(response.status_code, 200)
        self.assertIn("status=dismissed", response.get_data(as_text=True))
        item = db.get_queue_item(1)
        self.assertEqual(item["status"], "dismissed")
        actions = db.get_recent_operator_actions()
        self.assertEqual(actions[0]["action_type"], "dismiss")

    def test_detail_action_redirect_stays_on_item_with_feedback(self):
        response = self.client.post(
            "/queue/1/approve",
            data={
                "task": "Assignment 9",
                "course": "DATABASE",
                "due": "19 Jun 2026",
                "return_to": "detail",
            },
            follow_redirects=True,
        )
        body = response.get_data(as_text=True)
        self.assertEqual(response.status_code, 200)
        self.assertIn("Queue Item #1", body)
        self.assertIn("status=approved", body)
        self.assertIn("19 Jun 2026", body)

    def test_queue_action_redirect_preserves_filter_context(self):
        conn = sqlite3.connect(str(self.messages_db))
        conn.execute("DELETE FROM evidence_queue")
        conn.commit()
        conn.close()
        queue_id, _ = db.enqueue_evidence_item(
            source_type="vle",
            course="DATABASE",
            title="VLE Item",
            message="https://vle.example.edu.my/mod/resource/view.php?id=999",
            reason_code="missing_due",
            evidence_preview="Assignment Brief | no due date found",
        )
        response = self.client.post(
            f"/queue/{queue_id}/dismiss",
            data={
                "return_to": "queue",
                "queue_status": "pending",
                "source": "vle",
                "reason": "missing_due",
                "page": "1",
                "page_size": "25",
            },
            follow_redirects=False,
        )
        location = response.headers["Location"]
        self.assertIn("/queue?", location)
        self.assertIn("queue_status=dismissed", location)
        self.assertIn("source=vle", location)
        self.assertIn("reason=missing_due", location)
        self.assertIn("page_size=25", location)

    def test_approve_queue_item_creates_deadline_and_resolves_queue(self):
        response = self.client.post(
            "/queue/1/approve",
            data={"task": "Assignment 9", "course": "DATABASE", "due": "19 Jun 2026"},
            follow_redirects=True,
        )
        self.assertEqual(response.status_code, 200)
        self.assertIn("status=approved", response.get_data(as_text=True))
        item = db.get_queue_item(1)
        self.assertEqual(item["status"], "resolved")
        self.assertEqual(item["proposed_task"], "Assignment 9")
        rows = deadlines.get_all()
        self.assertTrue(any(row[2] == "DATABASE" and row[1] == "Assignment 9" for row in rows))

    def test_retry_queue_item_reprocesses_saved_whatsapp_message(self):
        conn = sqlite3.connect(str(self.messages_db))
        conn.execute("DELETE FROM messages")
        conn.execute(
            "INSERT INTO messages (id, timestamp, group_name, sender, message, done) VALUES (?,?,?,?,?,0)",
            (
                55,
                "2026-06-14T10:00:00",
                "DATABASE BO1",
                "Aina",
                "Assignment 10 due 21 Jun 2026",
            ),
        )
        conn.commit()
        conn.close()

        conn = sqlite3.connect(str(self.messages_db))
        conn.execute("DELETE FROM evidence_queue")
        conn.commit()
        conn.close()
        queue_id, _ = db.enqueue_evidence_item(
            source_type="whatsapp",
            source_row_id=55,
            group_name="DATABASE BO1",
            course="DATABASE",
            title="Aina in DATABASE BO1",
            message="Assignment 10 due 21 Jun 2026",
            reason_code="no_deadline_created",
            evidence_preview="Assignment 10 due 21 Jun 2026",
        )

        response = self.client.post(f"/queue/{queue_id}/retry", follow_redirects=True)
        self.assertEqual(response.status_code, 200)
        self.assertIn("status=retried", response.get_data(as_text=True))
        item = db.get_queue_item(queue_id)
        self.assertEqual(item["status"], "resolved")
        rows = deadlines.get_all()
        self.assertTrue(any(row[1] == "Assignment 10 due 21 Jun 2026" or row[1] == "Assignment 10" for row in rows))

    def test_retry_queue_item_rechecks_vle_reference(self):
        conn = sqlite3.connect(str(self.messages_db))
        conn.execute("DELETE FROM evidence_queue")
        conn.commit()
        conn.close()
        queue_id, _ = db.enqueue_evidence_item(
            source_type="vle",
            course="DATABASE",
            title="Assignment Brief",
            message="https://vle.example.edu.my/mod/resource/view.php?id=999",
            reason_code="missing_due",
            evidence_preview="Assignment Brief | no due date found",
        )

        old_lookup = ops_services._retry_vle_due_lookup
        ops_services._retry_vle_due_lookup = lambda item: "23 Jun 2026"
        try:
            response = self.client.post(f"/queue/{queue_id}/retry", follow_redirects=True)
        finally:
            ops_services._retry_vle_due_lookup = old_lookup

        self.assertEqual(response.status_code, 200)
        self.assertIn("status=retried", response.get_data(as_text=True))
        item = db.get_queue_item(queue_id)
        self.assertEqual(item["status"], "resolved")
        self.assertEqual(item["proposed_due"], "23 Jun 2026")
        rows = deadlines.get_all()
        self.assertTrue(any(row[1] == "Assignment Brief" and row[2] == "DATABASE" and row[3] == "23 Jun 2026" for row in rows))

    def test_run_ops_console_uses_loopback_defaults(self):
        old_host = os.environ.pop("OPS_CONSOLE_HOST", None)
        old_port = os.environ.pop("OPS_CONSOLE_PORT", None)

        captured = {}

        class _DummyApp:
            def run(self, host, port, debug):
                captured["host"] = host
                captured["port"] = port
                captured["debug"] = debug

        old_create_app = run_ops_console.create_app
        run_ops_console.create_app = lambda: _DummyApp()
        try:
            run_ops_console.main()
        finally:
            run_ops_console.create_app = old_create_app
            if old_host is not None:
                os.environ["OPS_CONSOLE_HOST"] = old_host
            if old_port is not None:
                os.environ["OPS_CONSOLE_PORT"] = old_port

        self.assertEqual(captured["host"], "127.0.0.1")
        self.assertEqual(captured["port"], 8091)
        self.assertFalse(captured["debug"])

    def test_basic_auth_is_optional_when_env_unset(self):
        response = self.client.get("/api/ping")
        self.assertEqual(response.status_code, 200)

    def test_basic_auth_blocks_when_env_is_set(self):
        old_user = os.environ.get("OPS_CONSOLE_USERNAME")
        old_pass = os.environ.get("OPS_CONSOLE_PASSWORD")
        os.environ["OPS_CONSOLE_USERNAME"] = "admin"
        os.environ["OPS_CONSOLE_PASSWORD"] = "secret"
        authed_app = ops_app_module.create_app()
        authed_client = authed_app.test_client()
        try:
            denied = authed_client.get("/api/ping")
            self.assertEqual(denied.status_code, 401)

            token = base64.b64encode(b"admin:secret").decode("ascii")
            allowed = authed_client.get("/api/ping", headers={"Authorization": f"Basic {token}"})
            self.assertEqual(allowed.status_code, 200)
        finally:
            if old_user is None:
                os.environ.pop("OPS_CONSOLE_USERNAME", None)
            else:
                os.environ["OPS_CONSOLE_USERNAME"] = old_user
            if old_pass is None:
                os.environ.pop("OPS_CONSOLE_PASSWORD", None)
            else:
                os.environ["OPS_CONSOLE_PASSWORD"] = old_pass

    def test_prefix_middleware_supports_prefixed_queue_path(self):
        old_prefix = os.environ.get("OPS_CONSOLE_URL_PREFIX")
        os.environ["OPS_CONSOLE_URL_PREFIX"] = "/ops-console"
        prefixed_app = ops_app_module.create_app()
        prefixed_client = Client(prefixed_app.wsgi_app, WerkzeugResponse)
        try:
            response = prefixed_client.get("/ops-console/queue")
            body = response.get_data(as_text=True)
            self.assertEqual(response.status_code, 200)
            self.assertIn("/ops-console/queue/1", body)
            self.assertIn("action=\"/ops-console/queue/1/approve\"", body)
        finally:
            if old_prefix is None:
                os.environ.pop("OPS_CONSOLE_URL_PREFIX", None)
            else:
                os.environ["OPS_CONSOLE_URL_PREFIX"] = old_prefix

    def test_deploy_helper_builds_expected_remote_commands(self):
        commands = deploy_ops_console.build_remote_commands()
        self.assertIn("cd /root/student-bot && python3 -m pip install --break-system-packages -r requirements.txt", commands)
        self.assertIn("cd /root/student-bot && pm2 restart ops-console --update-env", commands)
        self.assertIn("curl -f http://127.0.0.1:8091/api/ping", commands)

    def test_deploy_helper_dry_run_does_not_require_secrets(self):
        old_dry_run = os.environ.get("OPS_DEPLOY_DRY_RUN")
        old_host = os.environ.pop("STUDENT_BOT_HOST", None)
        old_user = os.environ.pop("STUDENT_BOT_USER", None)
        old_password = os.environ.pop("STUDENT_BOT_PASSWORD", None)
        os.environ["OPS_DEPLOY_DRY_RUN"] = "1"
        try:
            deploy_ops_console.main()
        finally:
            if old_dry_run is None:
                os.environ.pop("OPS_DEPLOY_DRY_RUN", None)
            else:
                os.environ["OPS_DEPLOY_DRY_RUN"] = old_dry_run
            if old_host is not None:
                os.environ["STUDENT_BOT_HOST"] = old_host
            if old_user is not None:
                os.environ["STUDENT_BOT_USER"] = old_user
            if old_password is not None:
                os.environ["STUDENT_BOT_PASSWORD"] = old_password


if __name__ == "__main__":
    unittest.main()
