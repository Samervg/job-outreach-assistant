import asyncio
import sqlite3
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import requests

import backend.config as config
import backend.database as database
from backend.backup import create_database_backup
from backend.main import unexpected_error_handler
from backend.services.email_generator import OllamaUnavailableError, get_available_models


class ConfigurationHardeningTests(unittest.TestCase):
    def test_invalid_service_url_is_rejected(self):
        with patch.object(config, "OLLAMA_BASE_URL", "not-a-url"):
            with self.assertRaisesRegex(config.ConfigurationError, "OLLAMA_BASE_URL"):
                config.validate_configuration()

    def test_insecure_oauth_flag_rejects_public_http_redirect(self):
        with patch.object(
            config, "GMAIL_REDIRECT_URI", "http://example.com/gmail/callback"
        ), patch.object(config, "ALLOW_INSECURE_OAUTH_LOOPBACK", True):
            with self.assertRaisesRegex(
                config.ConfigurationError, "yalnızca loopback"
            ):
                config.validate_configuration()

    def test_boolean_and_positive_integer_parsing_are_strict(self):
        with patch.dict("os.environ", {"TEST_BOOLEAN": "sometimes"}):
            with self.assertRaisesRegex(config.ConfigurationError, "true/false"):
                config._read_bool("TEST_BOOLEAN", False)
        with patch.dict("os.environ", {"TEST_LIMIT": "0"}):
            with self.assertRaisesRegex(config.ConfigurationError, "pozitif"):
                config._positive_int("TEST_LIMIT", 5)


class DatabaseHardeningTests(unittest.TestCase):
    def setUp(self):
        self.temp_directory = tempfile.TemporaryDirectory()
        self.root = Path(self.temp_directory.name)
        self.database_path = self.root / "source.db"
        self.database_patch = patch.object(
            database, "DATABASE_PATH", self.database_path
        )
        self.database_patch.start()

    def tearDown(self):
        self.database_patch.stop()
        self.temp_directory.cleanup()

    def test_repeated_initialization_is_idempotent_and_valid(self):
        database.initialize_database()
        database.initialize_database()
        database.initialize_database()

        with database.get_connection() as connection:
            self.assertEqual(
                connection.execute("PRAGMA integrity_check").fetchone()[0], "ok"
            )
            self.assertEqual(
                connection.execute(
                    "SELECT COUNT(*) FROM follow_up_settings WHERE id = 1"
                ).fetchone()[0],
                1,
            )

    def test_manual_backup_is_consistent_and_preserves_source(self):
        database.initialize_database()
        with database.get_connection() as connection:
            connection.execute(
                """
                INSERT INTO companies (
                    name, website, contact_email, target_position,
                    created_at, updated_at
                ) VALUES ('Acme', NULL, 'jobs@example.com', 'Engineer',
                          '2026-08-29', '2026-08-29')
                """
            )

        backup = create_database_backup(
            self.database_path, self.root / "backups"
        )

        self.assertTrue(backup.is_file())
        self.assertTrue(self.database_path.is_file())
        connection = sqlite3.connect(backup)
        try:
            self.assertEqual(
                connection.execute("PRAGMA integrity_check").fetchone()[0], "ok"
            )
            self.assertEqual(
                connection.execute("SELECT COUNT(*) FROM companies").fetchone()[0],
                1,
            )
        finally:
            connection.close()

    def test_backup_missing_database_has_clear_error(self):
        with self.assertRaisesRegex(FileNotFoundError, "bulunamadı"):
            create_database_backup(
                self.root / "missing.db", self.root / "backups"
            )


class ErrorAndExternalServiceHardeningTests(unittest.TestCase):
    def test_unexpected_error_response_and_log_are_sanitized(self):
        request = SimpleNamespace(
            method="GET", url=SimpleNamespace(path="/safe-path")
        )
        secret = "secret-token-must-not-appear"
        with self.assertLogs("backend.main", level="ERROR") as logs:
            response = asyncio.run(
                unexpected_error_handler(request, RuntimeError(secret))
            )

        self.assertEqual(response.status_code, 500)
        self.assertNotIn(secret, response.body.decode())
        self.assertNotIn(secret, " ".join(logs.output))
        self.assertIn("RuntimeError", " ".join(logs.output))

    def test_ollama_timeout_becomes_safe_dependency_error(self):
        with patch(
            "backend.services.email_generator.requests.get",
            side_effect=requests.Timeout("private upstream detail"),
        ):
            with self.assertRaisesRegex(
                OllamaUnavailableError, "Ollama'ya bağlanılamadı"
            ):
                get_available_models()


if __name__ == "__main__":
    unittest.main()
