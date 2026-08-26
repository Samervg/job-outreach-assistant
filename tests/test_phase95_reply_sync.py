import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from fastapi import HTTPException

import backend.applications as applications
import backend.database as database
from backend.services.gmail_service import (
    GmailReadError,
    GmailReplyResult,
    check_thread_replies,
)


def message(message_id, timestamp, sender, subject="Re: Application", snippet=""):
    return {
        "id": message_id,
        "threadId": "thread-1",
        "internalDate": str(timestamp),
        "snippet": snippet,
        "payload": {
            "headers": [
                {"name": "From", "value": sender},
                {"name": "Subject", "value": subject},
            ]
        },
    }


class ExecuteResult:
    def __init__(self, result):
        self.result = result

    def execute(self):
        return self.result


class FakeMessagesApi:
    def __init__(self, original, calls):
        self.original = original
        self.calls = calls

    def get(self, **kwargs):
        self.calls.append(("message.get", kwargs))
        return ExecuteResult(self.original)


class FakeThreadsApi:
    def __init__(self, thread, calls):
        self.thread = thread
        self.calls = calls

    def get(self, **kwargs):
        self.calls.append(("thread.get", kwargs))
        return ExecuteResult(self.thread)


class FakeGmailService:
    def __init__(self, original, messages):
        self.calls = []
        self.messages_api = FakeMessagesApi(original, self.calls)
        self.threads_api = FakeThreadsApi(
            {"id": "thread-1", "messages": messages}, self.calls
        )

    def users(self):
        return self

    def messages(self):
        return self.messages_api

    def threads(self):
        return self.threads_api


class GmailReplyDetectionTests(unittest.TestCase):
    def setUp(self):
        self.original = message(
            "sent-1", 1_000, "Me <me@example.com>", "Application", "sent"
        )

    def check(self, messages):
        service = FakeGmailService(self.original, messages)
        result = check_thread_replies(
            message_id="sent-1",
            service=service,
            account_email="me@example.com",
        )
        return result, service

    def test_sent_message_with_no_reply(self):
        result, service = self.check([self.original])
        self.assertFalse(result.has_reply)
        self.assertEqual(result.reply_count, 0)
        self.assertEqual(
            [name for name, _ in service.calls], ["message.get", "thread.get"]
        )

    def test_one_external_reply_and_short_metadata(self):
        reply = message(
            "reply-1", 2_000, "Recruiter <hr@example.com>", snippet="Thanks for applying"
        )
        result, _ = self.check([self.original, reply])
        self.assertTrue(result.has_reply)
        self.assertEqual(result.reply_count, 1)
        self.assertEqual(result.latest_reply_from, "Recruiter <hr@example.com>")
        self.assertEqual(result.latest_reply_subject, "Re: Application")
        self.assertEqual(result.latest_reply_snippet, "Thanks for applying")

    def test_multiple_replies_select_latest_and_ignore_our_own_later_message(self):
        first = message("reply-1", 2_000, "hr@example.com", snippet="First")
        own = message("own-2", 3_000, "Me <me@example.com>", snippet="My follow-up")
        latest = message("reply-2", 4_000, "manager@example.com", snippet="Latest")
        result, _ = self.check([latest, own, self.original, first])
        self.assertEqual(result.reply_count, 2)
        self.assertEqual(result.latest_reply_from, "manager@example.com")
        self.assertEqual(result.latest_reply_snippet, "Latest")

    def test_only_relevant_message_and_thread_are_inspected(self):
        result, service = self.check([self.original])
        self.assertFalse(result.has_reply)
        self.assertEqual(service.calls[0][1]["id"], "sent-1")
        self.assertEqual(service.calls[1][1]["id"], "thread-1")
        self.assertFalse(any(name.endswith("list") for name, _ in service.calls))

    def test_mismatched_stored_thread_is_rejected_without_inspecting_it(self):
        service = FakeGmailService(self.original, [self.original])
        with self.assertRaises(GmailReadError):
            check_thread_replies(
                message_id="sent-1",
                thread_id="unrelated-thread",
                service=service,
                account_email="me@example.com",
            )
        self.assertEqual([name for name, _ in service.calls], ["message.get"])


class ReplySyncEndpointTests(unittest.TestCase):
    def setUp(self):
        self.temp_directory = tempfile.TemporaryDirectory()
        self.db_path = Path(self.temp_directory.name) / "reply.db"
        self.database_patch = patch.object(database, "DATABASE_PATH", self.db_path)
        self.database_patch.start()
        database.initialize_database()
        now = "2026-08-26T10:00:00+00:00"
        with database.get_connection() as connection:
            for row_id, row_status, gmail_id in (
                (1, "sent", "sent-1"),
                (2, "interview", "sent-2"),
                (3, "rejected", "sent-3"),
                (4, "offer", "sent-4"),
                (5, "sent", None),
            ):
                connection.execute(
                    """
                    INSERT INTO outreach (
                        id, company_id, company_name, recipient_email, position,
                        subject, body, status, sent_at, gmail_message_id,
                        created_at, updated_at
                    ) VALUES (?, 1, 'Acme', 'hr@example.com', 'Engineer',
                              'Application', 'Body', ?, ?, ?, ?, ?)
                    """,
                    (row_id, row_status, now, gmail_id, now, now),
                )

    def tearDown(self):
        self.database_patch.stop()
        self.temp_directory.cleanup()

    @staticmethod
    def reply_result(has_reply=True):
        return GmailReplyResult(
            has_reply=has_reply,
            reply_count=1 if has_reply else 0,
            latest_reply_at="2026-08-27T10:00:00+00:00" if has_reply else None,
            latest_reply_from="hr@example.com" if has_reply else None,
            latest_reply_subject="Re: Application" if has_reply else None,
            latest_reply_snippet="Görüşmek isteriz." if has_reply else None,
            thread_id="thread-1",
        )

    def test_sent_becomes_replied_and_metadata_persists_after_restart(self):
        with patch.object(
            applications, "check_thread_replies", return_value=self.reply_result()
        ):
            response = applications.sync_application_reply(1)
        self.assertTrue(response.has_reply)
        self.assertEqual(response.application.status, "replied")
        self.assertEqual(response.application.reply_count, 1)
        database.initialize_database()
        loaded = applications.get_application(1)
        self.assertEqual(loaded.status, "replied")
        self.assertEqual(loaded.latest_reply_from, "hr@example.com")
        self.assertEqual(loaded.gmail_thread_id, "thread-1")

    def test_advanced_statuses_are_preserved(self):
        for row_id, expected in ((2, "interview"), (3, "rejected"), (4, "offer")):
            with patch.object(
                applications, "check_thread_replies", return_value=self.reply_result()
            ):
                response = applications.sync_application_reply(row_id)
            self.assertEqual(response.application.status, expected)
            self.assertEqual(response.application.reply_count, 1)

    def test_no_reply_keeps_sent_status(self):
        with patch.object(
            applications,
            "check_thread_replies",
            return_value=self.reply_result(False),
        ):
            response = applications.sync_application_reply(1)
        self.assertFalse(response.has_reply)
        self.assertEqual(response.application.status, "sent")

    def test_missing_gmail_id_and_api_failure_are_safe(self):
        with self.assertRaises(HTTPException) as missing:
            applications.sync_application_reply(5)
        self.assertEqual(missing.exception.status_code, 400)

        with patch.object(
            applications,
            "check_thread_replies",
            side_effect=GmailReadError("Gmail yanıt bilgisi alınamadı."),
        ):
            with self.assertRaises(HTTPException) as failed:
                applications.sync_application_reply(1)
        self.assertEqual(failed.exception.status_code, 502)
        self.assertEqual(applications.get_application(1).status, "sent")


if __name__ == "__main__":
    unittest.main()
