import os
import sqlite3
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from streamlit.testing.v1 import AppTest

import backend.applications as applications
import backend.database as database
import backend.outreach as outreach
from backend.applications import ApplicationUpdate
from backend.status_history import (
    add_status_history,
    first_reached_at,
    has_ever_reached,
)
from frontend import api_client


class StatusHistoryTests(unittest.TestCase):
    def setUp(self):
        self.temp_directory = tempfile.TemporaryDirectory()
        self.db_path = Path(self.temp_directory.name) / "history.db"
        self.db_patch = patch.object(database, "DATABASE_PATH", self.db_path)
        self.db_patch.start()
        database.initialize_database()

    def tearDown(self):
        self.db_patch.stop()
        self.temp_directory.cleanup()

    def _seed_application(self, status="sent", row_id=1):
        now = "2026-08-27T10:00:00+00:00"
        with database.get_connection() as connection:
            connection.execute(
                """
                INSERT INTO outreach (
                    id, company_id, company_name, recipient_email, position,
                    subject, body, status, sent_at, created_at, updated_at
                ) VALUES (?, 1, 'Acme', 'hr@example.com', 'Engineer',
                          'Subject', 'Body', ?, ?, ?, ?)
                """,
                (row_id, status, now if status != "draft" else None, now, now),
            )
            add_status_history(
                connection, row_id, None, status, "system", "Baseline", now
            )

    def test_new_generated_application_creates_initial_draft_event_atomically(self):
        now = "2026-08-27T10:00:00+00:00"
        with database.get_connection() as connection:
            connection.execute(
                """
                INSERT INTO user_profile (
                    id, name, target_job_title, professional_summary,
                    created_at, updated_at
                ) VALUES (1, 'Test', 'Engineer', 'Summary', ?, ?)
                """,
                (now, now),
            )
            connection.execute(
                """
                INSERT INTO companies (
                    id, name, website, contact_email, target_position,
                    created_at, updated_at
                ) VALUES (1, 'Acme', NULL, 'hr@example.com', 'Engineer', ?, ?)
                """,
                (now, now),
            )
        with patch.object(
            outreach,
            "generate_email",
            return_value=SimpleNamespace(subject="Subject", body="Body"),
        ):
            draft = outreach.generate_draft(outreach.DraftGenerateRequest(company_id=1))

        history = applications.get_application_history(draft.id)
        self.assertEqual(len(history), 1)
        self.assertIsNone(history[0].from_status)
        self.assertEqual(history[0].to_status, "draft")
        self.assertEqual(history[0].source, "system")
        self.assertEqual(history[0].note, "Başvuru oluşturuldu.")

    def test_noop_does_not_append_history_and_order_is_deterministic(self):
        self._seed_application()
        before = applications.get_application_history(1)
        applications.update_application(1, ApplicationUpdate(status="sent"))
        after = applications.get_application_history(1)
        self.assertEqual([item.id for item in after], [item.id for item in before])
        self.assertEqual(
            [(item.changed_at, item.id) for item in after],
            sorted((item.changed_at, item.id) for item in after),
        )

    def test_correction_is_append_only_and_removes_reversed_milestone(self):
        self._seed_application()
        applications.update_application(1, ApplicationUpdate(status="replied"))
        applications.update_application(1, ApplicationUpdate(status="interview"))
        corrected = applications.update_application(
            1, ApplicationUpdate(status="replied")
        )
        history = applications.get_application_history(1)

        self.assertEqual(corrected.status, "replied")
        self.assertEqual(len(history), 4)
        self.assertEqual(history[-1].source, "user_correction")
        with database.get_connection() as connection:
            self.assertFalse(has_ever_reached(connection, 1, "interview"))
            self.assertIsNone(first_reached_at(connection, 1, "interview"))
            self.assertTrue(has_ever_reached(connection, 1, "replied"))
            self.assertIsNotNone(first_reached_at(connection, 1, "replied"))

    def test_status_and_history_roll_back_together(self):
        self._seed_application()
        with patch.object(
            applications, "add_status_history", side_effect=sqlite3.DatabaseError("boom")
        ):
            with self.assertRaises(sqlite3.DatabaseError):
                applications.update_application(
                    1, ApplicationUpdate(status="replied")
                )
        self.assertEqual(applications.get_application(1).status, "sent")
        self.assertEqual(len(applications.get_application_history(1)), 1)

    def test_foreign_key_and_controlled_source_are_enforced(self):
        with database.get_connection() as connection:
            with self.assertRaises(sqlite3.IntegrityError):
                add_status_history(
                    connection, 999, None, "draft", "system", "Missing"
                )
        self._seed_application()
        with database.get_connection() as connection:
            with self.assertRaises(ValueError):
                add_status_history(
                    connection, 1, "sent", "replied", "arbitrary", "Invalid"
                )

    def test_reinitialization_does_not_add_or_fabricate_more_events(self):
        self._seed_application(status="interview")
        database.initialize_database()
        database.initialize_database()
        history = applications.get_application_history(1)
        self.assertEqual(len(history), 1)
        self.assertEqual(history[0].to_status, "interview")


class ApplicationsHistoryPageTests(unittest.TestCase):
    def test_applications_page_renders_history_without_gmail_calls(self):
        project_root = Path(__file__).resolve().parents[1]
        frontend = project_root / "frontend"
        previous_directory = Path.cwd()
        application = {
            "id": 1,
            "company_id": 1,
            "company_name": "Acme",
            "recipient_email": "hr@example.com",
            "position": "Engineer",
            "subject": "Subject",
            "body": "Body",
            "status": "draft",
            "sent_at": None,
            "gmail_message_id": None,
            "gmail_thread_id": None,
            "error_message": None,
            "replied_at": None,
            "latest_reply_from": None,
            "latest_reply_subject": None,
            "latest_reply_snippet": None,
            "reply_count": 0,
            "follow_up_disabled": False,
            "follow_up_count": 0,
            "last_follow_up_at": None,
            "last_follow_up_gmail_message_id": None,
            "ai_reply_classification": None,
            "ai_reply_confidence": None,
            "ai_reply_reason": None,
            "ai_reply_analyzed_at": None,
            "notes": "",
            "created_at": "2026-08-27T10:00:00+00:00",
            "updated_at": "2026-08-27T10:00:00+00:00",
        }
        history = [{
            "id": 1,
            "application_id": 1,
            "from_status": None,
            "to_status": "draft",
            "source": "system",
            "note": "Başvuru oluşturuldu.",
            "changed_at": "2026-08-27T10:00:00+00:00",
        }]

        os.chdir(frontend)
        sys.path.insert(0, str(frontend))
        previous_api_module = sys.modules.get("api_client")
        sys.modules["api_client"] = api_client
        try:
            with patch.object(
                api_client,
                "get_follow_up_settings",
                return_value=({
                    "follow_up_enabled": True,
                    "follow_up_after_days": 7,
                    "max_follow_ups": 1,
                }, None),
            ), patch.object(
                api_client, "list_applications", return_value=([application], None)
            ), patch.object(
                api_client, "get_application", return_value=(application, None)
            ), patch.object(
                api_client, "get_application_history", return_value=(history, None)
            ), patch.object(
                api_client,
                "get_application_analytics",
                return_value=({
                    "counts": {
                        "total": 1, "draft": 1, "sent": 0, "replied": 0,
                        "interview_reached": 0, "rejected": 0, "failed": 0,
                        "offer_reached": 0, "waiting_for_reply": 0,
                        "follow_up_due": 0,
                    },
                    "rates": {
                        "reply_rate": None, "reply_to_interview_rate": None,
                        "application_to_interview_rate": None,
                        "interview_to_offer_rate": None,
                    },
                    "timing": {
                        "average_reply_time_hours": None,
                        "median_reply_time_hours": None,
                        "average_time_to_interview_hours": None,
                    },
                    "data_quality": {
                        "applications_with_full_history": 1,
                        "baseline_only_migrated_records": 0,
                    },
                }, None),
            ), patch.object(
                api_client,
                "get_follow_up_eligibility",
                return_value=({
                    "eligible": False,
                    "reason_code": "status_not_eligible",
                    "reason": "Uygun değil.",
                    "days_remaining": 0,
                }, None),
            ):
                app = AppTest.from_file(
                    str(frontend / "app_pages" / "applications.py")
                ).run(timeout=20)
            self.assertEqual(len(app.exception), 0)
            self.assertIn("Geçmiş", [item.label for item in app.tabs])
            self.assertIn("Başvuruyu sil", [item.label for item in app.button])
        finally:
            os.chdir(previous_directory)
            sys.path.remove(str(frontend))
            if previous_api_module is None:
                sys.modules.pop("api_client", None)
            else:
                sys.modules["api_client"] = previous_api_module


if __name__ == "__main__":
    unittest.main()
