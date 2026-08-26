import sqlite3
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch

from fastapi import HTTPException
from pydantic import ValidationError

import backend.applications as applications
import backend.database as database
import backend.gmail as gmail_router
from backend.applications import ApplicationUpdate
from backend.gmail import SendDraftRequest


class Phase7MigrationTests(unittest.TestCase):
    def test_phase6_sent_record_survives_migration(self):
        with tempfile.TemporaryDirectory() as directory:
            db_path = Path(directory) / "phase6.db"
            connection = sqlite3.connect(db_path)
            connection.execute(
                """
                CREATE TABLE outreach (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    company_id INTEGER NOT NULL,
                    company_name TEXT NOT NULL,
                    recipient_email TEXT NOT NULL,
                    position TEXT NOT NULL,
                    subject TEXT NOT NULL,
                    body TEXT NOT NULL,
                    status TEXT NOT NULL DEFAULT 'draft'
                        CHECK (status IN ('draft', 'sent', 'failed')),
                    sent_at TEXT,
                    gmail_message_id TEXT,
                    error_message TEXT,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                )
                """
            )
            connection.execute(
                """
                INSERT INTO outreach (
                    id, company_id, company_name, recipient_email, position,
                    subject, body, status, sent_at, gmail_message_id,
                    error_message, created_at, updated_at
                ) VALUES (
                    7, 3, 'Existing Company', 'jobs@example.com', 'Engineer',
                    'Subject', 'Body', 'sent', '2026-08-25T10:00:00+00:00',
                    'gmail-real-id', NULL, '2026-08-25T09:00:00+00:00',
                    '2026-08-25T10:00:00+00:00'
                )
                """
            )
            connection.commit()
            connection.close()

            with patch.object(database, "DATABASE_PATH", db_path):
                database.initialize_database()
                with database.get_connection() as migrated:
                    row = migrated.execute(
                        "SELECT * FROM outreach WHERE id = 7"
                    ).fetchone()
                    integrity = migrated.execute(
                        "PRAGMA integrity_check"
                    ).fetchone()[0]

            self.assertEqual(row["status"], "sent")
            self.assertEqual(row["sent_at"], "2026-08-25T10:00:00+00:00")
            self.assertEqual(row["gmail_message_id"], "gmail-real-id")
            self.assertIsNone(row["error_message"])
            self.assertEqual(row["notes"], "")
            self.assertEqual(integrity, "ok")


class Phase7ApplicationTests(unittest.TestCase):
    def setUp(self):
        self.temp_directory = tempfile.TemporaryDirectory()
        self.db_path = Path(self.temp_directory.name) / "applications.db"
        self.database_patch = patch.object(database, "DATABASE_PATH", self.db_path)
        self.database_patch.start()
        database.initialize_database()
        self._seed()

    def tearDown(self):
        self.database_patch.stop()
        self.temp_directory.cleanup()

    def _seed(self):
        now = datetime.now(timezone.utc).isoformat()
        rows = [
            (1, "sent", now, "gmail-1"),
            (2, "draft", None, None),
            (3, "failed", None, None),
        ]
        with database.get_connection() as connection:
            for row_id, row_status, sent_at, gmail_id in rows:
                connection.execute(
                    """
                    INSERT INTO outreach (
                        id, company_id, company_name, recipient_email, position,
                        subject, body, status, sent_at, gmail_message_id,
                        error_message, notes, created_at, updated_at
                    ) VALUES (?, 1, ?, 'jobs@example.com', 'Engineer',
                              'Subject', 'Body', ?, ?, ?, ?, '', ?, ?)
                    """,
                    (
                        row_id,
                        f"Company {row_id}",
                        row_status,
                        sent_at,
                        gmail_id,
                        "Mock failure" if row_status == "failed" else None,
                        now,
                        now,
                    ),
                )

    def _set_status(self, value):
        with database.get_connection() as connection:
            connection.execute("UPDATE outreach SET status = ? WHERE id = 1", (value,))

    def test_manual_sent_to_replied_to_interview_to_rejected(self):
        replied = applications.update_application(
            1, ApplicationUpdate(status="replied")
        )
        self.assertEqual(replied.status, "replied")
        interview = applications.update_application(
            1, ApplicationUpdate(status="interview")
        )
        self.assertEqual(interview.status, "interview")
        rejected = applications.update_application(
            1, ApplicationUpdate(status="rejected")
        )
        self.assertEqual(rejected.status, "rejected")

    def test_manual_interview_to_offer(self):
        self._set_status("interview")
        offered = applications.update_application(
            1, ApplicationUpdate(status="offer")
        )
        self.assertEqual(offered.status, "offer")

    def test_invalid_status_and_invalid_transition_are_rejected(self):
        with self.assertRaises(ValidationError):
            ApplicationUpdate(status="unknown")
        with self.assertRaises(HTTPException) as context:
            applications.update_application(2, ApplicationUpdate(status="replied"))
        self.assertEqual(context.exception.status_code, 409)

    def test_notes_persist_after_restart(self):
        applications.update_application(
            1, ApplicationUpdate(notes="İlk görüşme 2 Eylül.")
        )
        database.initialize_database()
        loaded = applications.get_application(1)
        self.assertEqual(loaded.notes, "İlk görüşme 2 Eylül.")

    def test_filters_work(self):
        self.assertEqual(len(applications.list_applications("all")), 3)
        self.assertEqual(
            [item.id for item in applications.list_applications("sent")], [1]
        )
        self.assertEqual(
            [item.id for item in applications.list_applications("draft")], [2]
        )
        self.assertEqual(
            [item.id for item in applications.list_applications("failed")], [3]
        )
        with self.assertRaises(HTTPException) as context:
            applications.list_applications("invalid")
        self.assertEqual(context.exception.status_code, 422)

    def test_post_send_tracking_status_cannot_trigger_gmail_resend(self):
        self._set_status("replied")
        with patch.object(gmail_router, "send_email") as sender:
            with self.assertRaises(HTTPException) as context:
                gmail_router.send_draft(1, SendDraftRequest(confirm_send=True))
        self.assertEqual(context.exception.status_code, 409)
        sender.assert_not_called()


if __name__ == "__main__":
    unittest.main()
