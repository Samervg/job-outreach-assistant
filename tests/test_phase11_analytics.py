import os
import sys
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

from streamlit.testing.v1 import AppTest

import backend.database as database
import backend.applications as applications
import backend.services.application_analytics as analytics_service
from backend.services.application_analytics import (
    BASELINE_MIGRATION_NOTE,
    calculate_application_analytics,
)
from backend.services.follow_up import FollowUpEligibility
from backend.status_history import add_status_history
from frontend.application_state import format_duration_hours, format_percentage
from frontend import api_client


BASE_TIME = datetime(2031, 1, 1, tzinfo=timezone.utc)


class ApplicationAnalyticsTests(unittest.TestCase):
    def setUp(self):
        self.temp_directory = tempfile.TemporaryDirectory()
        self.db_path = Path(self.temp_directory.name) / "analytics.db"
        self.db_patch = patch.object(database, "DATABASE_PATH", self.db_path)
        self.db_patch.start()
        database.initialize_database()

    def tearDown(self):
        self.db_patch.stop()
        self.temp_directory.cleanup()

    def _timestamp(self, hours: float) -> str:
        return (BASE_TIME + timedelta(hours=hours)).isoformat()

    def _insert_application(
        self,
        application_id: int,
        *,
        status: str = "sent",
        sent_at: str | None = None,
        replied_at: str | None = None,
        reply_count: int = 0,
        gmail_ids: bool = False,
    ) -> None:
        created_at = self._timestamp(0)
        with database.get_connection() as connection:
            connection.execute(
                """
                INSERT INTO outreach (
                    id, company_id, company_name, recipient_email, position,
                    subject, body, status, sent_at, gmail_message_id,
                    gmail_thread_id, replied_at, reply_count, created_at, updated_at
                ) VALUES (?, 1, 'Acme', 'hr@example.com', 'Engineer',
                          'Subject', 'Body', ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    application_id,
                    status,
                    sent_at,
                    f"msg-{application_id}" if gmail_ids else None,
                    f"thread-{application_id}" if gmail_ids else None,
                    replied_at,
                    reply_count,
                    created_at,
                    created_at,
                ),
            )

    def _event(
        self,
        application_id: int,
        from_status: str | None,
        to_status: str,
        source: str,
        hour: float,
        note: str = "Event",
    ) -> None:
        with database.get_connection() as connection:
            add_status_history(
                connection,
                application_id,
                from_status,
                to_status,
                source,
                note,
                self._timestamp(hour),
            )

    def _calculate(self, now: datetime | None = None) -> dict:
        with database.get_connection() as connection:
            return calculate_application_analytics(connection, now=now)

    def test_funnel_rates_and_cross_metric_counting(self):
        for application_id in range(1001, 1011):
            self._insert_application(
                application_id,
                status=("replied" if application_id <= 1004 else "sent"),
                sent_at=self._timestamp(1),
                replied_at=(self._timestamp(5) if application_id <= 1004 else None),
                reply_count=1 if application_id <= 1004 else 0,
            )
            self._event(application_id, None, "draft", "system", 0)
            self._event(application_id, "draft", "sent", "gmail", 1)
            if application_id <= 1004:
                self._event(application_id, "sent", "replied", "gmail", 5)
            if application_id <= 1002:
                self._event(application_id, "replied", "interview", "user", 10)
            if application_id == 1001:
                self._event(application_id, "interview", "offer", "user", 20)

        result = self._calculate()
        self.assertEqual(result["counts"]["sent"], 10)
        self.assertEqual(result["counts"]["replied"], 4)
        self.assertEqual(result["counts"]["interview_reached"], 2)
        self.assertEqual(result["counts"]["offer_reached"], 1)
        self.assertEqual(result["rates"]["reply_rate"], 40.0)
        self.assertEqual(result["rates"]["reply_to_interview_rate"], 50.0)
        self.assertEqual(result["rates"]["application_to_interview_rate"], 20.0)
        self.assertEqual(result["rates"]["interview_to_offer_rate"], 50.0)
        self.assertEqual(result["counts"]["total"], 10)

    def test_correction_invalidates_interview_but_normal_later_status_does_not(self):
        self._insert_application(
            2001, status="rejected", sent_at=self._timestamp(1),
            replied_at=self._timestamp(3), reply_count=1
        )
        self._event(2001, None, "sent", "system", 1)
        self._event(2001, "sent", "replied", "gmail", 3)
        self._event(2001, "replied", "interview", "user", 5)
        self._event(2001, "interview", "rejected", "user", 8)

        self._insert_application(
            2002, status="replied", sent_at=self._timestamp(1),
            replied_at=self._timestamp(3), reply_count=1
        )
        self._event(2002, None, "sent", "system", 1)
        self._event(2002, "sent", "replied", "gmail", 3)
        self._event(2002, "replied", "interview", "user", 5)
        self._event(
            2002, "interview", "replied", "user_correction", 6,
            "Kullanıcı önceki durum seçimini düzeltti."
        )

        self._insert_application(2003, status="rejected", sent_at=self._timestamp(1))
        self._event(2003, None, "sent", "system", 1)
        self._event(2003, "sent", "interview", "user", 5)
        self._event(2003, "interview", "offer", "user", 7)
        self._event(2003, "offer", "rejected", "user", 9)

        result = self._calculate()
        self.assertEqual(result["counts"]["interview_reached"], 2)
        self.assertEqual(result["counts"]["offer_reached"], 1)

    def test_zero_denominators_return_null(self):
        self._insert_application(3001, status="draft")
        self._event(3001, None, "draft", "system", 0)
        rates = self._calculate()["rates"]
        self.assertTrue(all(value is None for value in rates.values()))

    def test_timing_uses_first_reply_and_excludes_missing_or_baseline_times(self):
        self._insert_application(
            4001, status="interview", sent_at=self._timestamp(0),
            replied_at=self._timestamp(72), reply_count=3
        )
        self._event(4001, None, "draft", "system", 0)
        self._event(4001, "draft", "sent", "gmail", 0.5)
        self._event(4001, "sent", "replied", "gmail", 24)
        self._event(4001, "replied", "interview", "user", 48)

        self._insert_application(
            4002, status="replied", sent_at=None,
            replied_at=self._timestamp(10), reply_count=1
        )
        self._event(4002, None, "replied", "system", 10)

        self._insert_application(
            4003, status="interview", sent_at=self._timestamp(0)
        )
        self._event(
            4003, None, "interview", "system", 80, BASELINE_MIGRATION_NOTE
        )

        result = self._calculate()
        self.assertEqual(result["timing"]["average_reply_time_hours"], 24.0)
        self.assertEqual(result["timing"]["median_reply_time_hours"], 24.0)
        self.assertEqual(result["timing"]["average_time_to_interview_hours"], 48.0)
        self.assertEqual(result["data_quality"]["baseline_only_migrated_records"], 1)
        self.assertEqual(result["data_quality"]["applications_with_full_history"], 2)

    def test_baseline_record_proves_only_its_known_state(self):
        self._insert_application(5001, status="interview", sent_at=self._timestamp(0))
        self._event(
            5001, None, "interview", "system", 20, BASELINE_MIGRATION_NOTE
        )
        result = self._calculate()
        self.assertEqual(result["counts"]["sent"], 1)
        self.assertEqual(result["counts"]["replied"], 0)
        self.assertEqual(result["counts"]["interview_reached"], 1)
        self.assertIsNone(result["timing"]["average_time_to_interview_hours"])

    def test_waiting_for_reply_uses_current_compatible_status(self):
        cases = [
            (6001, "sent", True, False),
            (6002, "sent", True, True),
            (6003, "rejected", True, False),
            (6004, "interview", True, False),
            (6005, "offer", True, False),
            (6006, "failed", False, False),
            (6007, "draft", False, False),
        ]
        for application_id, status, sent, replied in cases:
            self._insert_application(
                application_id,
                status=status,
                sent_at=self._timestamp(0) if sent else None,
                replied_at=self._timestamp(2) if replied else None,
                reply_count=1 if replied else 0,
            )
            self._event(application_id, None, status, "system", 0)
        self.assertEqual(self._calculate()["counts"]["waiting_for_reply"], 1)

    def test_follow_up_due_delegates_to_existing_eligibility_service(self):
        for application_id in (7001, 7002, 7003):
            self._insert_application(
                application_id, status="sent", sent_at=self._timestamp(0)
            )
            self._event(application_id, None, "sent", "system", 0)

        calls = []

        def fake_eligibility(application, settings, *, now=None):
            calls.append((application["id"], settings["follow_up_after_days"], now))
            return FollowUpEligibility(application["id"] == 7002, "test", "Test")

        fixed_now = datetime(2031, 1, 20, tzinfo=timezone.utc)
        with patch.object(
            analytics_service, "evaluate_follow_up", side_effect=fake_eligibility
        ):
            result = self._calculate(now=fixed_now)
        self.assertEqual(result["counts"]["follow_up_due"], 1)
        self.assertEqual([call[0] for call in calls], [7001, 7002, 7003])
        self.assertTrue(all(call[2] == fixed_now for call in calls))

    def test_frontend_formatters_use_dash_hours_and_days(self):
        self.assertEqual(format_percentage(None), "—")
        self.assertEqual(format_percentage(40), "%40.0")
        self.assertEqual(format_duration_hours(None), "—")
        self.assertEqual(format_duration_hours(12), "12.0 saat")
        self.assertEqual(format_duration_hours(53), "2.2 gün")

    def test_read_only_api_summary_returns_typed_shape(self):
        self._insert_application(8001, status="sent", sent_at=self._timestamp(0))
        self._event(8001, None, "sent", "system", 0)
        response = applications.get_application_analytics()
        self.assertEqual(response.counts.total, 1)
        self.assertEqual(response.counts.sent, 1)
        self.assertEqual(response.rates.reply_rate, 0.0)


class Phase11PageSmokeTests(unittest.TestCase):
    def test_profile_page_renders(self):
        project_root = Path(__file__).resolve().parents[1]
        frontend = project_root / "frontend"
        previous_directory = Path.cwd()
        os.chdir(frontend)
        sys.path.insert(0, str(frontend))
        previous_api_module = sys.modules.get("api_client")
        sys.modules["api_client"] = api_client
        try:
            with patch.object(api_client, "get_profile", return_value=(None, None)):
                app = AppTest.from_file(
                    str(frontend / "app_pages" / "profile.py")
                ).run(timeout=20)
            self.assertEqual(len(app.exception), 0)
        finally:
            os.chdir(previous_directory)
            sys.path.remove(str(frontend))
            if previous_api_module is None:
                sys.modules.pop("api_client", None)
            else:
                sys.modules["api_client"] = previous_api_module


if __name__ == "__main__":
    unittest.main()
