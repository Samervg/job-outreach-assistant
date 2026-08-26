import base64
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from fastapi import HTTPException

import backend.applications as applications
import backend.database as database
from backend.services.gmail_service import (
    GmailReadError,
    GmailReplyContent,
    get_latest_reply_content,
)


def encoded(text: str) -> str:
    return base64.urlsafe_b64encode(text.encode("utf-8")).decode("ascii").rstrip("=")


def message(message_id, timestamp, sender, payload, subject="Re: Application"):
    payload = dict(payload)
    payload["headers"] = [
        {"name": "From", "value": sender},
        {"name": "Subject", "value": subject},
    ]
    return {
        "id": message_id,
        "threadId": "thread-1",
        "internalDate": str(timestamp),
        "payload": payload,
    }


class Result:
    def __init__(self, value):
        self.value = value

    def execute(self):
        return self.value


class MessagesApi:
    def __init__(self, original, calls):
        self.original = original
        self.calls = calls

    def get(self, **kwargs):
        self.calls.append(("messages.get", kwargs))
        return Result(self.original)


class ThreadsApi:
    def __init__(self, thread, calls):
        self.thread = thread
        self.calls = calls

    def get(self, **kwargs):
        self.calls.append(("threads.get", kwargs))
        return Result(self.thread)


class Service:
    def __init__(self, original, replies, returned_thread_id="thread-1"):
        self.calls = []
        self.messages_api = MessagesApi(original, self.calls)
        self.threads_api = ThreadsApi(
            {"id": returned_thread_id, "messages": [original, *replies]}, self.calls
        )

    def users(self):
        return self

    def messages(self):
        return self.messages_api

    def threads(self):
        return self.threads_api


class ReplyBodyExtractionTests(unittest.TestCase):
    def setUp(self):
        self.original = message(
            "sent-1",
            1_000,
            "Me <me@example.com>",
            {"mimeType": "text/plain", "body": {"data": encoded("sent")}},
            "Application",
        )

    def read(self, replies, **kwargs):
        service = Service(self.original, replies, **kwargs)
        result = get_latest_reply_content(
            message_id="sent-1",
            thread_id="thread-1",
            service=service,
            account_email="me@example.com",
        )
        return result, service

    def reply(self, payload, timestamp=2_000, sender="HR <hr@example.com>"):
        return message("reply-1", timestamp, sender, payload)

    def test_plain_text_and_base64url_decoding(self):
        result, _ = self.read([
            self.reply({"mimeType": "text/plain", "body": {"data": encoded("Merhaba şğü")}})
        ])
        self.assertEqual(result.body_text, "Merhaba şğü")

    def test_html_only_is_safely_converted(self):
        html = (
            "<style>x{}</style><script>bad()</script><p>Merhaba <b>Samet</b></p>"
            "<div class='gmail_quote'>Eski mesaj</div>"
        )
        result, _ = self.read([
            self.reply({"mimeType": "text/html", "body": {"data": encoded(html)}})
        ])
        self.assertIn("Merhaba", result.body_text)
        self.assertIn("Samet", result.body_text)
        self.assertNotIn("bad()", result.body_text)
        self.assertNotIn("<b>", result.body_text)
        self.assertNotIn("Eski mesaj", result.body_text)

    def test_multipart_alternative_prefers_plain(self):
        payload = {
            "mimeType": "multipart/alternative",
            "parts": [
                {"mimeType": "text/html", "body": {"data": encoded("<p>HTML</p>")}},
                {"mimeType": "text/plain", "body": {"data": encoded("PLAIN")}},
            ],
        }
        result, _ = self.read([self.reply(payload)])
        self.assertEqual(result.body_text, "PLAIN")

    def test_nested_multipart_is_read(self):
        payload = {
            "mimeType": "multipart/mixed",
            "parts": [{
                "mimeType": "multipart/alternative",
                "parts": [{"mimeType": "text/plain", "body": {"data": encoded("Nested")}}],
            }],
        }
        result, _ = self.read([self.reply(payload)])
        self.assertEqual(result.body_text, "Nested")

    def test_quoted_previous_message_is_removed_conservatively(self):
        body = "Yeni yanıt\n\nOn Tue, Samet wrote:\n> Eski mesaj"
        result, _ = self.read([
            self.reply({"mimeType": "text/plain", "body": {"data": encoded(body)}})
        ])
        self.assertEqual(result.body_text, "Yeni yanıt")

    def test_latest_external_reply_selected_and_own_message_ignored(self):
        first = self.reply(
            {"mimeType": "text/plain", "body": {"data": encoded("First")}}, 2_000
        )
        own = message(
            "own-2", 3_000, "Me <me@example.com>",
            {"mimeType": "text/plain", "body": {"data": encoded("Mine")}},
        )
        latest = message(
            "reply-3", 4_000, "Manager <manager@example.com>",
            {"mimeType": "text/plain", "body": {"data": encoded("Latest")}},
        )
        result, service = self.read([first, own, latest])
        self.assertEqual(result.body_text, "Latest")
        self.assertEqual(result.sender, "Manager <manager@example.com>")
        self.assertEqual([name for name, _ in service.calls], ["messages.get", "threads.get"])
        self.assertEqual(service.calls[1][1]["id"], "thread-1")
        self.assertEqual(service.calls[1][1]["format"], "full")

    def test_thread_mismatch_is_rejected_before_thread_read(self):
        service = Service(self.original, [])
        with self.assertRaises(GmailReadError):
            get_latest_reply_content(
                message_id="sent-1",
                thread_id="unrelated",
                service=service,
                account_email="me@example.com",
            )
        self.assertEqual([name for name, _ in service.calls], ["messages.get"])

    def test_mismatched_thread_response_is_rejected(self):
        reply = self.reply({"mimeType": "text/plain", "body": {"data": encoded("Hi")}})
        with self.assertRaises(GmailReadError):
            self.read([reply], returned_thread_id="other-thread")


class ReplyContentEndpointTests(unittest.TestCase):
    def setUp(self):
        self.temp_directory = tempfile.TemporaryDirectory()
        self.db_path = Path(self.temp_directory.name) / "reply-content.db"
        self.database_patch = patch.object(database, "DATABASE_PATH", self.db_path)
        self.database_patch.start()
        database.initialize_database()
        now = "2026-08-26T10:00:00+00:00"
        with database.get_connection() as connection:
            connection.execute(
                """
                INSERT INTO outreach (
                    id, company_id, company_name, recipient_email, position,
                    subject, body, status, sent_at, gmail_message_id,
                    gmail_thread_id, replied_at, reply_count, notes,
                    created_at, updated_at
                ) VALUES (1, 1, 'Acme', 'hr@example.com', 'Engineer',
                          'Application', 'Body', 'interview', ?, 'sent-1',
                          'thread-1', ?, 1, 'Keep me', ?, ?)
                """,
                (now, now, now, now),
            )
            connection.execute(
                """
                INSERT INTO outreach (
                    id, company_id, company_name, recipient_email, position,
                    subject, body, status, created_at, updated_at
                ) VALUES (2, 1, 'No Gmail', 'x@example.com', 'Engineer',
                          'Draft', 'Body', 'draft', ?, ?)
                """,
                (now, now),
            )

    def tearDown(self):
        self.database_patch.stop()
        self.temp_directory.cleanup()

    def test_content_is_read_only_and_not_persisted(self):
        before = applications.get_application(1).model_dump()
        reply = GmailReplyContent(
            sender="hr@example.com",
            subject="Re: Application",
            received_at="2026-08-27T10:00:00+00:00",
            body_text="Görüşelim.",
            thread_id="thread-1",
        )
        with patch.object(applications, "get_latest_reply_content", return_value=reply):
            response = applications.get_application_reply_content(1)
        after = applications.get_application(1).model_dump()
        self.assertEqual(response.body_text, "Görüşelim.")
        self.assertEqual(response.model_dump(by_alias=True)["from"], "hr@example.com")
        self.assertEqual(before, after)
        with database.get_connection() as connection:
            columns = {row[1] for row in connection.execute("PRAGMA table_info(outreach)")}
            raw = json.dumps(dict(connection.execute("SELECT * FROM outreach WHERE id=1").fetchone()))
        self.assertFalse(any("body_text" in name or "reply_body" in name for name in columns))
        self.assertNotIn("Görüşelim.", raw)

    def test_missing_gmail_message_id_is_rejected(self):
        with self.assertRaises(HTTPException) as raised:
            applications.get_application_reply_content(2)
        self.assertEqual(raised.exception.status_code, 400)


if __name__ == "__main__":
    unittest.main()
