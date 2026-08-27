import base64
import sqlite3
import tempfile
import time
import unittest
from datetime import datetime, timezone
from email import policy
from email.parser import BytesParser
from pathlib import Path
from unittest.mock import Mock, patch
from urllib.parse import parse_qs, urlparse

from fastapi import HTTPException

import backend.database as database
import backend.gmail as gmail_router
import backend.services.gmail_service as gmail_service
from backend.gmail import SendDraftRequest
from backend.services.gmail_service import (
    GmailConnectionStatus,
    GmailSendError,
    GmailSendResult,
)


class FakeMessages:
    def __init__(self, result):
        self.result = result
        self.sent_body = None

    def send(self, *, userId, body):
        self.user_id = userId
        self.sent_body = body
        return self

    def execute(self):
        return self.result


class FakeGmailService:
    def __init__(self, result=None):
        self.messages_api = FakeMessages(
            result or {"id": "gmail-message-123", "threadId": "thread-123"}
        )

    def users(self):
        return self

    def messages(self):
        return self.messages_api


class GmailServiceTests(unittest.TestCase):
    def test_real_google_scope_set_accepts_email_alias_and_extra_identity_scope(self):
        returned_scopes = {
            "email",
            "openid",
            "https://www.googleapis.com/auth/gmail.send",
            "https://www.googleapis.com/auth/gmail.readonly",
            "https://www.googleapis.com/auth/userinfo.email",
        }
        self.assertTrue(gmail_service._has_required_scopes(returned_scopes))

    def test_scope_normalization_never_makes_gmail_send_optional(self):
        returned_scopes = {
            "email",
            "openid",
            "https://www.googleapis.com/auth/userinfo.email",
        }
        self.assertFalse(gmail_service._has_required_scopes(returned_scopes))

    def test_gmail_readonly_scope_is_mandatory(self):
        returned_scopes = {
            "email",
            "openid",
            "https://www.googleapis.com/auth/gmail.send",
            "https://www.googleapis.com/auth/userinfo.email",
        }
        self.assertFalse(gmail_service._has_required_scopes(returned_scopes))

    def test_callback_missing_gmail_scope_is_rejected_before_token_exchange(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            state_path = root / "gmail_oauth_state.json"
            state_path.write_text(
                '{"state": "expected-state", "code_verifier": "' + "a" * 64 + '"}',
                encoding="utf-8",
            )
            callback = (
                "http://127.0.0.1:8000/gmail/auth/callback?"
                "state=expected-state&code=redacted&scope=email%20openid"
            )
            with (
                patch.object(gmail_service, "GMAIL_OAUTH_STATE_PATH", state_path),
                patch.object(gmail_service.Flow, "from_client_secrets_file") as flow,
            ):
                with self.assertRaises(gmail_service.GmailConfigurationError):
                    gmail_service.complete_oauth(callback, "expected-state")
            flow.assert_not_called()
            self.assertFalse(state_path.exists())

    def test_oauth_state_cannot_be_consumed_twice(self):
        with tempfile.TemporaryDirectory() as directory:
            state_path = Path(directory) / "gmail_oauth_state.json"
            state_path.write_text('{"state": "expected-state"}', encoding="utf-8")
            with patch.object(gmail_service, "GMAIL_OAUTH_STATE_PATH", state_path):
                gmail_service._consume_oauth_state()
                with self.assertRaises(gmail_service.GmailConfigurationError):
                    gmail_service._consume_oauth_state()

    def test_pkce_verifier_survives_separate_start_and_callback_flows(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            secret = root / "client_secret.json"
            state_path = root / "gmail_oauth_state.json"
            secret.write_text(
                """{
                  "web": {
                    "client_id": "test.apps.googleusercontent.com",
                    "client_secret": "not-a-real-secret",
                    "auth_uri": "https://accounts.google.com/o/oauth2/auth",
                    "token_uri": "https://oauth2.googleapis.com/token",
                    "redirect_uris": [
                      "http://127.0.0.1:8000/gmail/auth/callback"
                    ]
                  }
                }""",
                encoding="utf-8",
            )
            original_factory = gmail_service.Flow.from_client_secrets_file
            created_flows = []

            def recording_factory(*args, **kwargs):
                flow = original_factory(*args, **kwargs)
                created_flows.append(flow)
                return flow

            def fake_fetch_token(flow, **kwargs):
                self.assertIn("authorization_response", kwargs)
                self.assertTrue(flow.code_verifier)
                token = {
                    "access_token": "mock-access-token",
                    "token_type": "Bearer",
                    "scope": [
                        "email",
                        "openid",
                        "https://www.googleapis.com/auth/gmail.send",
                        "https://www.googleapis.com/auth/gmail.readonly",
                        "https://www.googleapis.com/auth/userinfo.email",
                    ],
                    "expires_at": time.time() + 3600,
                }
                scope_warning = Warning("Equivalent Google identity scope added")
                scope_warning.token = token
                scope_warning.new_scope = set(token["scope"])
                raise scope_warning

            with (
                patch.object(gmail_service, "GMAIL_CLIENT_SECRET_PATH", secret),
                patch.object(gmail_service, "GMAIL_OAUTH_STATE_PATH", state_path),
                patch.object(
                    gmail_service.Flow,
                    "from_client_secrets_file",
                    side_effect=recording_factory,
                ),
                patch.object(gmail_service.Flow, "fetch_token", new=fake_fetch_token),
                patch.object(gmail_service, "_save_credentials"),
                patch.object(gmail_service, "_fetch_account_email", return_value=None),
            ):
                authorization_url = gmail_service.start_oauth()
                query = parse_qs(urlparse(authorization_url).query)
                first_verifier = created_flows[0].code_verifier
                callback_url = (
                    "http://127.0.0.1:8000/gmail/auth/callback?"
                    f"state={query['state'][0]}&code=redacted&"
                    "scope=https%3A%2F%2Fwww.googleapis.com%2Fauth%2Fgmail.send"
                    "%20https%3A%2F%2Fwww.googleapis.com%2Fauth%2Fgmail.readonly"
                    "%20openid%20email"
                )
                gmail_service.complete_oauth(callback_url, query["state"][0])

            self.assertEqual(len(created_flows), 2)
            self.assertIsNot(created_flows[0], created_flows[1])
            self.assertEqual(created_flows[1].code_verifier, first_verifier)
            self.assertFalse(state_path.exists())

    def test_insecure_transport_is_enabled_only_for_loopback(self):
        with (
            patch.object(
                gmail_service,
                "GMAIL_REDIRECT_URI",
                "http://127.0.0.1:8000/gmail/auth/callback",
            ),
            patch.object(gmail_service, "ALLOW_INSECURE_OAUTH_LOOPBACK", True),
            patch.dict("os.environ", {}, clear=False),
        ):
            gmail_service._configure_oauth_transport()
            self.assertEqual(
                gmail_service.os.environ.get("OAUTHLIB_INSECURE_TRANSPORT"), "1"
            )

    def test_insecure_transport_is_rejected_for_public_http_host(self):
        with (
            patch.object(
                gmail_service,
                "GMAIL_REDIRECT_URI",
                "http://example.com/gmail/auth/callback",
            ),
            patch.object(gmail_service, "ALLOW_INSECURE_OAUTH_LOOPBACK", True),
            patch.dict("os.environ", {}, clear=False),
        ):
            with self.assertRaises(gmail_service.GmailConfigurationError):
                gmail_service._configure_oauth_transport()
            self.assertNotIn(
                "OAUTHLIB_INSECURE_TRANSPORT", gmail_service.os.environ
            )

    def test_requested_oauth_scopes_are_minimal_and_complete(self):
        with tempfile.TemporaryDirectory() as directory:
            secret = Path(directory) / "client_secret.json"
            secret.write_text(
                """{
                  "web": {
                    "client_id": "test.apps.googleusercontent.com",
                    "client_secret": "not-a-real-secret",
                    "auth_uri": "https://accounts.google.com/o/oauth2/auth",
                    "token_uri": "https://oauth2.googleapis.com/token",
                    "redirect_uris": [
                      "http://127.0.0.1:8000/gmail/auth/callback"
                    ]
                  }
                }""",
                encoding="utf-8",
            )
            with (
                patch.object(gmail_service, "GMAIL_CLIENT_SECRET_PATH", secret),
                patch.object(gmail_service, "_write_json"),
            ):
                authorization_url = gmail_service.start_oauth()

        requested = set(
            parse_qs(urlparse(authorization_url).query)["scope"][0].split()
        )
        self.assertEqual(
            requested,
            {
                "https://www.googleapis.com/auth/gmail.send",
                "https://www.googleapis.com/auth/gmail.readonly",
                "openid",
                "email",
            },
        )

    def test_missing_credentials_reports_disconnected(self):
        with tempfile.TemporaryDirectory() as directory:
            missing = Path(directory) / "missing.json"
            with patch.object(gmail_service, "GMAIL_CLIENT_SECRET_PATH", missing):
                status = gmail_service.get_gmail_status()
        self.assertFalse(status.connected)
        self.assertFalse(status.credentials_available)

    def test_credentials_without_token_reports_disconnected(self):
        with tempfile.TemporaryDirectory() as directory:
            secret = Path(directory) / "client_secret.json"
            secret.write_text("{}", encoding="utf-8")
            with (
                patch.object(gmail_service, "GMAIL_CLIENT_SECRET_PATH", secret),
                patch.object(gmail_service, "_load_credentials", return_value=None),
            ):
                status = gmail_service.get_gmail_status()
        self.assertFalse(status.connected)
        self.assertTrue(status.credentials_available)

    def test_connected_status_reads_local_account(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            secret = root / "client_secret.json"
            account = root / "account.json"
            secret.write_text("{}", encoding="utf-8")
            account.write_text('{"email": "me@example.com"}', encoding="utf-8")
            with (
                patch.object(gmail_service, "GMAIL_CLIENT_SECRET_PATH", secret),
                patch.object(gmail_service, "GMAIL_ACCOUNT_PATH", account),
                patch.object(gmail_service, "_load_credentials", return_value=Mock()),
            ):
                status = gmail_service.get_gmail_status()
        self.assertTrue(status.connected)
        self.assertEqual(status.email, "me@example.com")

    def test_expired_credentials_are_refreshed(self):
        with tempfile.TemporaryDirectory() as directory:
            token = Path(directory) / "token.json"
            token.write_text("{}", encoding="utf-8")
            credentials = Mock(
                expired=True,
                refresh_token="refresh-token",
                valid=True,
            )
            credentials.has_scopes.return_value = True
            credentials.to_json.return_value = '{"token": "new-token"}'
            with (
                patch.object(gmail_service, "GMAIL_TOKEN_PATH", token),
                patch.object(
                    gmail_service.Credentials,
                    "from_authorized_user_file",
                    return_value=credentials,
                ),
                patch.object(gmail_service, "Request", return_value=Mock()),
            ):
                loaded = gmail_service._load_credentials()
            credentials.refresh.assert_called_once()
            self.assertIs(loaded, credentials)

    def test_mime_send_uses_original_pdf_name(self):
        with tempfile.TemporaryDirectory() as directory:
            cv_path = Path(directory) / "internal-uuid.pdf"
            cv_path.write_bytes(b"%PDF-1.4 test")
            service = FakeGmailService()
            message_id = gmail_service.send_email(
                recipient="jobs@example.com",
                subject="Application",
                body="Hello",
                cv_path=cv_path,
                cv_original_name="My CV.pdf",
                service=service,
                sender_email="me@example.com",
            )
            raw = service.messages_api.sent_body["raw"]
            message = BytesParser(policy=policy.default).parsebytes(
                base64.urlsafe_b64decode(raw)
            )
        self.assertEqual(message_id.message_id, "gmail-message-123")
        self.assertEqual(message_id.thread_id, "thread-123")
        self.assertEqual(message["To"], "jobs@example.com")
        attachments = list(message.iter_attachments())
        self.assertEqual(len(attachments), 1)
        self.assertEqual(attachments[0].get_filename(), "My CV.pdf")


class SendDraftTests(unittest.TestCase):
    def setUp(self):
        self.temp_directory = tempfile.TemporaryDirectory()
        self.root = Path(self.temp_directory.name)
        self.uploads = self.root / "data" / "uploads"
        self.uploads.mkdir(parents=True)
        self.cv_path = self.uploads / "stored.pdf"
        self.cv_path.write_bytes(b"%PDF-1.4 test")
        self.db_path = self.root / "data" / "test.db"
        self.database_patch = patch.object(database, "DATABASE_PATH", self.db_path)
        self.upload_patch = patch.object(gmail_router, "UPLOAD_DIR", self.uploads)
        self.project_patch = patch.object(gmail_router, "PROJECT_ROOT", self.root)
        self.database_patch.start()
        self.upload_patch.start()
        self.project_patch.start()
        database.initialize_database()
        self._seed()

    def tearDown(self):
        self.project_patch.stop()
        self.upload_patch.stop()
        self.database_patch.stop()
        self.temp_directory.cleanup()

    def _seed(self):
        now = datetime.now(timezone.utc).isoformat()
        with database.get_connection() as connection:
            connection.execute(
                """
                INSERT INTO user_profile (
                    id, name, target_job_title, professional_summary,
                    linkedin_url, github_url, cv_file_path, cv_original_name,
                    created_at, updated_at
                ) VALUES (1, 'Test User', 'AI Engineer', 'Summary', NULL, NULL,
                          ?, 'Test CV.pdf', ?, ?)
                """,
                (str(self.cv_path), now, now),
            )
            connection.execute(
                """
                INSERT INTO outreach (
                    company_id, company_name, recipient_email, position,
                    subject, body, status, created_at, updated_at
                ) VALUES (1, 'Example', 'jobs@example.com', 'AI Engineer',
                          'Application', 'Hello', 'draft', ?, ?)
                """,
                (now, now),
            )

    def _send(self, confirmed=True):
        connected = GmailConnectionStatus(True, "me@example.com", True, "ok")
        with (
            patch.object(gmail_router, "_connection_status", return_value=connected),
            patch.object(
                gmail_router,
                "send_email",
                return_value=GmailSendResult("gmail-42", "thread-42"),
            ) as sender,
        ):
            result = gmail_router.send_draft(1, SendDraftRequest(confirm_send=confirmed))
        return result, sender

    def test_explicit_false_is_rejected_without_gmail_call(self):
        with patch.object(gmail_router, "send_email") as sender:
            with self.assertRaises(HTTPException) as context:
                gmail_router.send_draft(1, SendDraftRequest(confirm_send=False))
        self.assertEqual(context.exception.status_code, 400)
        sender.assert_not_called()

    def test_missing_cv_is_rejected(self):
        self.cv_path.unlink()
        with patch.object(gmail_router, "send_email") as sender:
            with self.assertRaises(HTTPException) as context:
                gmail_router.send_draft(1, SendDraftRequest(confirm_send=True))
        self.assertEqual(context.exception.status_code, 400)
        sender.assert_not_called()

    def test_invalid_recipient_is_rejected(self):
        with database.get_connection() as connection:
            connection.execute(
                "UPDATE outreach SET recipient_email = 'invalid' WHERE id = 1"
            )
        with patch.object(gmail_router, "send_email") as sender:
            with self.assertRaises(HTTPException) as context:
                gmail_router.send_draft(1, SendDraftRequest(confirm_send=True))
        self.assertEqual(context.exception.status_code, 400)
        sender.assert_not_called()

    def test_empty_subject_and_body_are_rejected(self):
        for column in ("subject", "body"):
            with self.subTest(column=column):
                with database.get_connection() as connection:
                    connection.execute(f"UPDATE outreach SET {column} = '' WHERE id = 1")
                with patch.object(gmail_router, "send_email") as sender:
                    with self.assertRaises(HTTPException) as context:
                        gmail_router.send_draft(1, SendDraftRequest(confirm_send=True))
                self.assertEqual(context.exception.status_code, 400)
                sender.assert_not_called()
                with database.get_connection() as connection:
                    connection.execute(
                        "UPDATE outreach SET subject = 'Application', body = 'Hello' WHERE id = 1"
                    )

    def test_success_is_saved_and_cannot_be_resent(self):
        result, sender = self._send()
        self.assertEqual(result.status, "sent")
        self.assertIsNotNone(result.sent_at)
        self.assertEqual(result.gmail_message_id, "gmail-42")
        self.assertEqual(result.gmail_thread_id, "thread-42")
        self.assertIsNone(result.error_message)
        with database.get_connection() as connection:
            history = connection.execute(
                "SELECT from_status, to_status, source FROM application_status_history WHERE application_id=1"
            ).fetchall()
        self.assertEqual([tuple(row) for row in history], [("draft", "sent", "gmail")])
        sender.assert_called_once()
        with patch.object(gmail_router, "send_email") as second_sender:
            with self.assertRaises(HTTPException) as context:
                gmail_router.send_draft(1, SendDraftRequest(confirm_send=True))
        self.assertEqual(context.exception.status_code, 409)
        second_sender.assert_not_called()

    def test_failed_send_is_saved_and_may_be_retried(self):
        connected = GmailConnectionStatus(True, "me@example.com", True, "ok")
        with (
            patch.object(gmail_router, "_connection_status", return_value=connected),
            patch.object(
                gmail_router,
                "send_email",
                side_effect=GmailSendError("Mock Gmail failure"),
            ),
        ):
            with self.assertRaises(HTTPException) as context:
                gmail_router.send_draft(1, SendDraftRequest(confirm_send=True))
        self.assertEqual(context.exception.status_code, 502)
        with database.get_connection() as connection:
            row = connection.execute("SELECT * FROM outreach WHERE id = 1").fetchone()
        self.assertEqual(row["status"], "failed")
        self.assertEqual(row["error_message"], "Mock Gmail failure")
        self.assertIsNone(row["sent_at"])
        self.assertIsNone(row["gmail_message_id"])
        with database.get_connection() as connection:
            history = connection.execute(
                "SELECT to_status, source FROM application_status_history WHERE application_id=1"
            ).fetchall()
        self.assertNotIn(("sent", "gmail"), [tuple(item) for item in history])
        retried, _ = self._send()
        self.assertEqual(retried.status, "sent")

    def test_database_integrity(self):
        connection = sqlite3.connect(self.db_path)
        try:
            result = connection.execute("PRAGMA integrity_check").fetchone()[0]
        finally:
            connection.close()
        self.assertEqual(result, "ok")


if __name__ == "__main__":
    unittest.main()
