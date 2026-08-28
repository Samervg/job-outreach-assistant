import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from fastapi import HTTPException

import backend.applications as applications
import backend.database as database
from backend.status_history import add_status_history
from frontend import api_client


class ApplicationDeletionTests(unittest.TestCase):
    def setUp(self):
        self.temp_directory = tempfile.TemporaryDirectory()
        self.db_path = Path(self.temp_directory.name) / "deletion.db"
        self.db_patch = patch.object(database, "DATABASE_PATH", self.db_path)
        self.db_patch.start()
        database.initialize_database()

    def tearDown(self):
        self.db_patch.stop()
        self.temp_directory.cleanup()

    def _seed_application(self, application_id: int, status: str) -> None:
        now = "2026-08-28T10:00:00+00:00"
        with database.get_connection() as connection:
            connection.execute(
                """
                INSERT INTO outreach (
                    id, company_id, company_name, recipient_email, position,
                    subject, body, status, sent_at, created_at, updated_at
                ) VALUES (?, 1, 'Acme', 'jobs@example.com', 'Engineer',
                          'Subject', 'Body', ?, ?, ?, ?)
                """,
                (
                    application_id,
                    status,
                    now if status not in {"draft", "failed"} else None,
                    now,
                    now,
                ),
            )
            add_status_history(
                connection,
                application_id,
                None,
                status,
                "system",
                "Test baseline",
                now,
            )

    def _row_counts(self, application_id: int) -> tuple[int, int]:
        with database.get_connection() as connection:
            outreach_count = connection.execute(
                "SELECT COUNT(*) FROM outreach WHERE id = ?", (application_id,)
            ).fetchone()[0]
            history_count = connection.execute(
                """
                SELECT COUNT(*) FROM application_status_history
                WHERE application_id = ?
                """,
                (application_id,),
            ).fetchone()[0]
        return outreach_count, history_count

    def test_delete_draft_removes_application_and_dependent_history(self):
        self._seed_application(1, "draft")

        response = applications.delete_application(1)

        self.assertTrue(response.deleted)
        self.assertEqual(response.application_id, 1)
        self.assertEqual(self._row_counts(1), (0, 0))

    def test_delete_failed_succeeds(self):
        self._seed_application(2, "failed")

        response = applications.delete_application(2)

        self.assertTrue(response.deleted)
        self.assertEqual(self._row_counts(2), (0, 0))

    def test_non_disposable_statuses_return_conflict(self):
        for application_id, status in enumerate(
            ["sent", "replied", "interview", "rejected", "offer"], start=10
        ):
            with self.subTest(status=status):
                self._seed_application(application_id, status)
                with self.assertRaises(HTTPException) as raised:
                    applications.delete_application(application_id)
                self.assertEqual(raised.exception.status_code, 409)
                self.assertEqual(self._row_counts(application_id), (1, 1))

    def test_nonexistent_application_returns_not_found(self):
        with self.assertRaises(HTTPException) as raised:
            applications.delete_application(999)

        self.assertEqual(raised.exception.status_code, 404)

    def test_transaction_rollback_restores_application_and_history(self):
        self._seed_application(20, "draft")
        with patch.object(
            applications,
            "_delete_outreach_row",
            side_effect=sqlite3.DatabaseError("forced failure"),
        ):
            with self.assertRaises(sqlite3.DatabaseError):
                applications.delete_application(20)

        self.assertEqual(self._row_counts(20), (1, 1))


class ApplicationDeletionClientTests(unittest.TestCase):
    def test_delete_client_uses_application_endpoint(self):
        response = Mock(ok=True)
        response.json.return_value = {"deleted": True, "application_id": 7}
        with patch.object(api_client.requests, "delete", return_value=response) as request:
            result, error = api_client.delete_application(7)

        self.assertIsNone(error)
        self.assertEqual(result, {"deleted": True, "application_id": 7})
        request.assert_called_once_with(
            f"{api_client.BACKEND_URL}/applications/7", timeout=10
        )


if __name__ == "__main__":
    unittest.main()
