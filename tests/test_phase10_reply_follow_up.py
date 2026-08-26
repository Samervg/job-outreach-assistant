import base64
import json
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from email import policy
from email.parser import BytesParser
from pathlib import Path
from unittest.mock import patch

from fastapi import HTTPException

import backend.applications as applications
import backend.database as database
import backend.services.reply_intelligence as reply_intelligence
from backend.application_semantics import application_matches_filter
from backend.applications import FollowUpSendRequest, ReplyAnalysisDecision
from backend.services.follow_up import evaluate_follow_up
from backend.services.gmail_service import (
    GmailReplyContent,
    GmailReplyResult,
    GmailSendError,
    GmailSendResult,
    send_thread_follow_up,
)
from backend.services.reply_intelligence import ReplyAnalysis, analyze_reply
from frontend.application_state import application_metrics, should_show_ai_approval


class MockResponse:
    def __init__(self, content):
        self.content = content

    def raise_for_status(self):
        return None

    def json(self):
        return {"message": {"content": json.dumps(self.content)}}


class ReplyIntelligenceTests(unittest.TestCase):
    def test_all_supported_reply_classifications(self):
        cases = {
            "rejection": "rejected",
            "interview": "interview",
            "positive_interest": "replied",
            "more_information": "replied",
            "neutral": "replied",
            "automated_reply": "replied",
            "unclear": "replied",
        }
        for classification, expected_status in cases.items():
            with self.subTest(classification=classification), patch.object(
                reply_intelligence.requests,
                "post",
                return_value=MockResponse(
                    {
                        "classification": classification,
                        "confidence": 0.91,
                        "reason": "Kısa ve doğrulanabilir neden.",
                    }
                ),
            ):
                result = analyze_reply("Gerçek yanıt örneği")
                self.assertEqual(result.classification, classification)
                self.assertEqual(result.suggested_status, expected_status)

    def test_low_confidence_is_preserved_without_forcing_a_status(self):
        with patch.object(
            reply_intelligence.requests,
            "post",
            return_value=MockResponse(
                {"classification": "unclear", "confidence": 0.2, "reason": "Belirsiz."}
            ),
        ):
            result = analyze_reply("Belirsiz içerik")
        self.assertEqual(result.confidence, 0.2)
        self.assertEqual(result.classification, "unclear")

    def test_malformed_ollama_response_is_rejected(self):
        with patch.object(
            reply_intelligence.requests,
            "post",
            return_value=MockResponse({"classification": "invented"}),
        ):
            with self.assertRaises(reply_intelligence.ReplyAnalysisError):
                analyze_reply("Yanıt")

    def test_reply_body_is_sent_only_to_local_ollama_and_not_logged(self):
        secret_body = "PRIVATE REPLY BODY 123"
        with patch.object(
            reply_intelligence.requests,
            "post",
            return_value=MockResponse(
                {"classification": "neutral", "confidence": 0.8, "reason": "Nötr."}
            ),
        ) as post:
            analyze_reply(secret_body)
        called_url = post.call_args.args[0]
        self.assertTrue(called_url.startswith("http://127.0.0.1:"))
        self.assertIn(secret_body, post.call_args.kwargs["json"]["messages"][1]["content"])


class AiApprovalVisibilityTests(unittest.TestCase):
    def test_same_status_hides_redundant_approval(self):
        self.assertFalse(
            should_show_ai_approval(
                "replied",
                "replied",
                ["replied", "interview", "rejected", "offer"],
            )
        )

    def test_different_valid_status_keeps_approval_available(self):
        allowed = ["replied", "interview", "rejected", "offer"]
        self.assertTrue(should_show_ai_approval("replied", "interview", allowed))
        self.assertTrue(should_show_ai_approval("replied", "rejected", allowed))

    def test_invalid_transition_never_shows_approval(self):
        self.assertFalse(
            should_show_ai_approval(
                "interview", "replied", ["interview", "rejected", "offer"]
            )
        )


class ApplicationMetricTests(unittest.TestCase):
    def test_historical_funnel_metrics_allow_cross_card_counting(self):
        applications = [
            {
                "status": "rejected",
                "sent_at": "2026-01-01",
                "replied_at": "2026-01-02",
                "reply_count": 1,
            },
            {
                "status": "rejected",
                "sent_at": "2026-01-01",
                "replied_at": None,
                "reply_count": 0,
            },
            {
                "status": "interview",
                "sent_at": "2026-01-01",
                "replied_at": "2026-01-02",
                "reply_count": 1,
            },
            {
                "status": "sent",
                "sent_at": "2026-01-01",
                "replied_at": None,
                "reply_count": 0,
            },
            {"status": "draft", "sent_at": None, "replied_at": None, "reply_count": 0},
            {
                "status": "offer",
                "sent_at": "2026-01-01",
                "replied_at": "2026-01-02",
                "reply_count": 2,
            },
        ]
        metrics = application_metrics(applications)
        self.assertEqual(metrics["sent_total"], 5)
        self.assertEqual(metrics["reply_total"], 3)
        self.assertEqual(metrics["draft"], 1)
        self.assertEqual(metrics["sent"], 1)
        self.assertEqual(metrics["replied"], 0)
        self.assertEqual(metrics["interview"], 1)
        self.assertEqual(metrics["rejected"], 2)
        self.assertEqual(metrics["offer"], 1)
        self.assertEqual(metrics["total"], 6)

    def test_rejected_without_reply_metadata_is_not_inferred_as_reply(self):
        applications = [{
            "status": "rejected",
            "sent_at": "2026-01-01",
            "replied_at": None,
            "reply_count": 0,
        }]
        metrics = application_metrics(applications)
        self.assertEqual(metrics["sent_total"], 1)
        self.assertEqual(metrics["reply_total"], 0)
        self.assertEqual(metrics["rejected"], 1)

    def test_draft_and_failed_without_sent_date_do_not_count_as_sent(self):
        metrics = application_metrics(
            [
                {"status": "draft", "sent_at": None},
                {"status": "failed", "sent_at": None},
            ]
        )
        self.assertEqual(metrics["sent_total"], 0)
        self.assertEqual(metrics["reply_total"], 0)

    def test_filter_predicates_use_fields_not_ids_or_company_data(self):
        record = {
            "id": 987654,
            "company_name": "Arbitrary Future Company",
            "status": "rejected",
            "sent_at": "2030-04-01T00:00:00+00:00",
            "reply_count": 1,
            "replied_at": "2030-04-02T00:00:00+00:00",
        }
        self.assertTrue(application_matches_filter(record, "sent"))
        self.assertTrue(application_matches_filter(record, "replied"))
        self.assertTrue(application_matches_filter(record, "rejected"))
        self.assertFalse(application_matches_filter(record, "interview"))


class HistoricalFilterEndpointTests(unittest.TestCase):
    def setUp(self):
        self.temp_directory = tempfile.TemporaryDirectory()
        self.db_path = Path(self.temp_directory.name) / "historical-filters.db"
        self.db_patch = patch.object(database, "DATABASE_PATH", self.db_path)
        self.db_patch.start()
        database.initialize_database()
        timestamp = "2031-01-01T00:00:00+00:00"
        records = [
            (7001, "rejected", timestamp, timestamp, 1),
            (7002, "rejected", timestamp, None, 0),
            (7003, "interview", timestamp, timestamp, 1),
            (7004, "sent", timestamp, None, 0),
            (7005, "draft", None, None, 0),
            (7006, "offer", timestamp, timestamp, 2),
            (7007, "failed", None, None, 0),
        ]
        with database.get_connection() as connection:
            for row_id, row_status, sent_at, replied_at, reply_count in records:
                connection.execute(
                    """
                    INSERT INTO outreach (
                        id, company_id, company_name, recipient_email, position,
                        subject, body, status, sent_at, replied_at, reply_count,
                        created_at, updated_at
                    ) VALUES (?, 1, ?, 'future@example.com', 'Engineer',
                              'Subject', 'Body', ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        row_id,
                        f"Future Company {row_id}",
                        row_status,
                        sent_at,
                        replied_at,
                        reply_count,
                        timestamp,
                        timestamp,
                    ),
                )

    def tearDown(self):
        self.db_patch.stop()
        self.temp_directory.cleanup()

    @staticmethod
    def ids(filter_name):
        return {item.id for item in applications.list_applications(filter_name)}

    def test_historical_and_current_status_filters_overlap_intentionally(self):
        self.assertEqual(self.ids("sent"), {7001, 7002, 7003, 7004, 7006})
        self.assertEqual(self.ids("replied"), {7001, 7003, 7006})
        self.assertEqual(self.ids("rejected"), {7001, 7002})
        self.assertEqual(self.ids("interview"), {7003})
        self.assertEqual(self.ids("offer"), {7006})
        self.assertEqual(self.ids("draft"), {7005})
        self.assertEqual(self.ids("failed"), {7007})
        self.assertEqual(len(self.ids("all")), 7)

class FollowUpEligibilityTests(unittest.TestCase):
    def application(self, **changes):
        now = datetime(2026, 8, 26, tzinfo=timezone.utc)
        data = {
            "status": "sent",
            "sent_at": (now - timedelta(days=7)).isoformat(),
            "gmail_message_id": "sent-1",
            "gmail_thread_id": "thread-1",
            "replied_at": None,
            "reply_count": 0,
            "follow_up_disabled": 0,
            "follow_up_count": 0,
            "last_follow_up_at": None,
            "ai_reply_classification": None,
        }
        data.update(changes)
        return data

    @staticmethod
    def settings(**changes):
        data = {"follow_up_enabled": 1, "follow_up_after_days": 7, "max_follow_ups": 1}
        data.update(changes)
        return data

    def test_sent_seven_days_is_eligible_but_six_days_waits(self):
        now = datetime(2026, 8, 26, tzinfo=timezone.utc)
        self.assertTrue(evaluate_follow_up(self.application(), self.settings(), now=now).eligible)
        six_days = self.application(sent_at=(now - timedelta(days=6)).isoformat())
        result = evaluate_follow_up(six_days, self.settings(), now=now)
        self.assertFalse(result.eligible)
        self.assertEqual(result.reason_code, "waiting_period")

    def test_never_eligible_statuses_and_safety_blocks(self):
        cases = [
            (self.application(status="rejected"), "status_not_sent"),
            (self.application(status="offer"), "status_not_sent"),
            (self.application(status="interview"), "status_not_sent"),
            (self.application(status="replied"), "status_not_sent"),
            (self.application(replied_at="2026-08-20T00:00:00+00:00", reply_count=1), "reply_detected"),
            (self.application(ai_reply_classification="unclear"), "reply_analysis_present"),
            (self.application(follow_up_disabled=1), "application_disabled"),
            (self.application(follow_up_count=1), "max_follow_ups_reached"),
            (self.application(gmail_message_id=None), "missing_gmail_identifiers"),
            (self.application(gmail_thread_id=None), "missing_gmail_identifiers"),
        ]
        for application, reason in cases:
            with self.subTest(reason=reason):
                result = evaluate_follow_up(application, self.settings(), now=datetime(2026, 8, 26, tzinfo=timezone.utc))
                self.assertFalse(result.eligible)
                self.assertEqual(result.reason_code, reason)

    def test_global_disabled_and_zero_max_are_blocked(self):
        self.assertEqual(
            evaluate_follow_up(self.application(), self.settings(follow_up_enabled=0)).reason_code,
            "globally_disabled",
        )
        self.assertEqual(
            evaluate_follow_up(self.application(), self.settings(max_follow_ups=0)).reason_code,
            "max_follow_ups_reached",
        )


class Execute:
    def __init__(self, result):
        self.result = result

    def execute(self):
        return self.result


class ThreadMessagesApi:
    def __init__(self):
        self.calls = []
        self.sent_body = None

    def get(self, **kwargs):
        self.calls.append(("get", kwargs))
        return Execute(
            {
                "id": "sent-1",
                "threadId": "thread-1",
                "payload": {"headers": [{"name": "Message-ID", "value": "<abc@test>"}]},
            }
        )

    def send(self, **kwargs):
        self.calls.append(("send", kwargs))
        self.sent_body = kwargs["body"]
        return Execute({"id": "follow-1", "threadId": "thread-1"})


class ThreadService:
    def __init__(self):
        self.api = ThreadMessagesApi()

    def users(self):
        return self

    def messages(self):
        return self.api


class GmailThreadingTests(unittest.TestCase):
    def test_follow_up_is_sent_in_verified_same_thread(self):
        service = ThreadService()
        result = send_thread_follow_up(
            recipient="hr@example.com",
            subject="Re: Application",
            body="Follow-up",
            original_message_id="sent-1",
            thread_id="thread-1",
            service=service,
            sender_email="me@example.com",
        )
        self.assertEqual(result.thread_id, "thread-1")
        self.assertEqual(service.api.sent_body["threadId"], "thread-1")
        raw = base64.urlsafe_b64decode(service.api.sent_body["raw"])
        mime = BytesParser(policy=policy.default).parsebytes(raw)
        self.assertEqual(mime["In-Reply-To"], "<abc@test>")
        self.assertFalse(any(name.endswith("list") for name, _ in service.api.calls))


class Phase10EndpointTests(unittest.TestCase):
    def setUp(self):
        self.temp_directory = tempfile.TemporaryDirectory()
        self.db_path = Path(self.temp_directory.name) / "phase10.db"
        self.db_patch = patch.object(database, "DATABASE_PATH", self.db_path)
        self.db_patch.start()
        database.initialize_database()
        now = datetime.now(timezone.utc)
        old = (now - timedelta(days=8)).isoformat()
        with database.get_connection() as connection:
            connection.execute(
                """
                INSERT INTO user_profile (
                    id, name, target_job_title, professional_summary,
                    created_at, updated_at
                ) VALUES (1, 'Samet', 'AI Engineer', 'Summary', ?, ?)
                """,
                (old, old),
            )
            connection.execute(
                """
                INSERT INTO outreach (
                    id, company_id, company_name, recipient_email, position,
                    subject, body, status, sent_at, gmail_message_id,
                    gmail_thread_id, created_at, updated_at
                ) VALUES (1, 1, 'Acme', 'hr@example.com', 'AI Engineer',
                          'Application', 'Original body', 'sent', ?, 'sent-1',
                          'thread-1', ?, ?)
                """,
                (old, old, old),
            )
            connection.execute(
                """
                INSERT INTO outreach (
                    id, company_id, company_name, recipient_email, position,
                    subject, body, status, sent_at, gmail_message_id,
                    gmail_thread_id, replied_at, reply_count, created_at, updated_at
                ) VALUES (2, 1, 'Acme', 'hr@example.com', 'AI Engineer',
                          'Application', 'Original body', 'replied', ?, 'sent-2',
                          'thread-2', ?, 1, ?, ?)
                """,
                (old, now.isoformat(), old, old),
            )

    def tearDown(self):
        self.db_patch.stop()
        self.temp_directory.cleanup()

    @staticmethod
    def no_reply():
        return GmailReplyResult(False, 0, None, None, None, None, "thread-1")

    @staticmethod
    def new_reply():
        return GmailReplyResult(
            True, 1, datetime.now(timezone.utc).isoformat(), "hr@example.com",
            "Re: Application", "Hello", "thread-1"
        )

    def test_ai_analysis_never_changes_status_and_does_not_persist_body(self):
        content = GmailReplyContent(
            "hr@example.com", "Re", datetime.now(timezone.utc).isoformat(),
            "PRIVATE FULL REPLY", "thread-2"
        )
        analysis = ReplyAnalysis("interview", "interview", 0.94, "Görüşme talebi var.")
        with (
            patch.object(applications, "get_latest_reply_content", return_value=content),
            patch.object(applications, "analyze_reply", return_value=analysis),
        ):
            response = applications.analyze_application_reply(2)
        self.assertEqual(response.suggested_status, "interview")
        stored = applications.get_application(2)
        self.assertEqual(stored.status, "replied")
        self.assertEqual(stored.ai_reply_classification, "interview")
        with database.get_connection() as connection:
            raw = json.dumps(dict(connection.execute("SELECT * FROM outreach WHERE id=2").fetchone()))
        self.assertNotIn("PRIVATE FULL REPLY", raw)

    def test_user_confirm_override_and_ignore(self):
        with database.get_connection() as connection:
            connection.execute(
                "UPDATE outreach SET ai_reply_classification='interview' WHERE id=2"
            )
        ignored = applications.decide_reply_analysis(2, ReplyAnalysisDecision(action="ignore"))
        self.assertEqual(ignored.status, "replied")
        confirmed = applications.decide_reply_analysis(2, ReplyAnalysisDecision(action="confirm"))
        self.assertEqual(confirmed.status, "interview")

        with database.get_connection() as connection:
            connection.execute(
                "UPDATE outreach SET status='replied' WHERE id=2"
            )
        overridden = applications.decide_reply_analysis(
            2, ReplyAnalysisDecision(action="change", status="rejected")
        )
        self.assertEqual(overridden.status, "rejected")

    def test_draft_generation_does_not_send(self):
        with (
            patch.object(applications, "generate_follow_up") as generator,
            patch.object(applications, "send_thread_follow_up") as sender,
        ):
            from backend.services.follow_up import FollowUpDraft
            generator.return_value = FollowUpDraft("Re: Application", "Follow-up body")
            draft = applications.generate_application_follow_up(1)
        self.assertEqual(draft.body, "Follow-up body")
        sender.assert_not_called()

    def test_explicit_approval_is_required(self):
        with self.assertRaises(HTTPException) as raised:
            applications.send_application_follow_up(
                1, FollowUpSendRequest(subject="Re: Application", body="Body", confirm_send=False)
            )
        self.assertEqual(raised.exception.status_code, 400)

    def test_new_reply_aborts_send_and_updates_metadata(self):
        with (
            patch.object(applications, "check_thread_replies", return_value=self.new_reply()),
            patch.object(applications, "send_thread_follow_up") as sender,
        ):
            with self.assertRaises(HTTPException) as raised:
                applications.send_application_follow_up(
                    1, FollowUpSendRequest(subject="Re: Application", body="Body", confirm_send=True)
                )
        self.assertEqual(raised.exception.status_code, 409)
        sender.assert_not_called()
        stored = applications.get_application(1)
        self.assertEqual(stored.status, "replied")
        self.assertEqual(stored.follow_up_count, 0)

    def test_success_increments_once_and_status_remains_sent(self):
        with (
            patch.object(applications, "check_thread_replies", return_value=self.no_reply()),
            patch.object(
                applications,
                "send_thread_follow_up",
                return_value=GmailSendResult("follow-1", "thread-1"),
            ) as sender,
        ):
            result = applications.send_application_follow_up(
                1, FollowUpSendRequest(subject="Re: Application", body="Body", confirm_send=True)
            )
        self.assertEqual(result.status, "sent")
        self.assertEqual(result.follow_up_count, 1)
        self.assertIsNotNone(result.last_follow_up_at)
        self.assertEqual(result.last_follow_up_gmail_message_id, "follow-1")
        self.assertEqual(sender.call_args.kwargs["thread_id"], "thread-1")

    def test_gmail_failure_does_not_increment_or_set_date(self):
        with (
            patch.object(applications, "check_thread_replies", return_value=self.no_reply()),
            patch.object(
                applications, "send_thread_follow_up", side_effect=GmailSendError("failed")
            ),
        ):
            with self.assertRaises(HTTPException):
                applications.send_application_follow_up(
                    1, FollowUpSendRequest(subject="Re: Application", body="Body", confirm_send=True)
                )
        stored = applications.get_application(1)
        self.assertEqual(stored.follow_up_count, 0)
        self.assertIsNone(stored.last_follow_up_at)


if __name__ == "__main__":
    unittest.main()
