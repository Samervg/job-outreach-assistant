import sqlite3
from contextlib import contextmanager
from collections.abc import Iterator

from backend.config import DATABASE_PATH


def _create_outreach_table(
    connection: sqlite3.Connection, table_name: str = "outreach"
) -> None:
    if table_name not in {"outreach", "outreach_upgrade"}:
        raise ValueError("Unexpected outreach table name.")

    connection.execute(
        f"""
        CREATE TABLE IF NOT EXISTS {table_name} (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            company_id INTEGER NOT NULL,
            company_name TEXT NOT NULL,
            recipient_email TEXT NOT NULL,
            position TEXT NOT NULL,
            subject TEXT NOT NULL,
            body TEXT NOT NULL,
            status TEXT NOT NULL DEFAULT 'draft'
                CHECK (status IN (
                    'draft', 'sent', 'failed', 'replied',
                    'interview', 'rejected', 'offer'
                )),
            sent_at TEXT,
            gmail_message_id TEXT,
            gmail_thread_id TEXT,
            error_message TEXT,
            replied_at TEXT,
            latest_reply_from TEXT,
            latest_reply_subject TEXT,
            latest_reply_snippet TEXT,
            reply_count INTEGER NOT NULL DEFAULT 0,
            notes TEXT NOT NULL DEFAULT '',
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        )
        """
    )


def _upgrade_outreach_table(connection: sqlite3.Connection) -> None:
    columns = {
        row["name"] for row in connection.execute("PRAGMA table_info(outreach)")
    }
    required_columns = {
        "sent_at", "gmail_message_id", "gmail_thread_id", "error_message",
        "replied_at", "latest_reply_from", "latest_reply_subject",
        "latest_reply_snippet", "reply_count", "notes",
    }
    table_sql_row = connection.execute(
        "SELECT sql FROM sqlite_master WHERE type = 'table' AND name = 'outreach'"
    ).fetchone()
    table_sql = table_sql_row["sql"] if table_sql_row else ""

    required_statuses = {"'replied'", "'interview'", "'rejected'", "'offer'"}
    if required_columns.issubset(columns) and all(
        status in table_sql for status in required_statuses
    ):
        return

    connection.execute("DROP TABLE IF EXISTS outreach_upgrade")
    _create_outreach_table(connection, "outreach_upgrade")
    sent_at = "sent_at" if "sent_at" in columns else "NULL"
    gmail_message_id = "gmail_message_id" if "gmail_message_id" in columns else "NULL"
    gmail_thread_id = "gmail_thread_id" if "gmail_thread_id" in columns else "NULL"
    error_message = "error_message" if "error_message" in columns else "NULL"
    replied_at = "replied_at" if "replied_at" in columns else "NULL"
    latest_reply_from = "latest_reply_from" if "latest_reply_from" in columns else "NULL"
    latest_reply_subject = "latest_reply_subject" if "latest_reply_subject" in columns else "NULL"
    latest_reply_snippet = "latest_reply_snippet" if "latest_reply_snippet" in columns else "NULL"
    reply_count = "reply_count" if "reply_count" in columns else "0"
    notes = "notes" if "notes" in columns else "''"
    connection.execute(
        f"""
        INSERT INTO outreach_upgrade (
            id, company_id, company_name, recipient_email, position,
            subject, body, status, sent_at, gmail_message_id, gmail_thread_id,
            error_message, replied_at, latest_reply_from, latest_reply_subject,
            latest_reply_snippet, reply_count, notes, created_at, updated_at
        )
        SELECT
            id, company_id, company_name, recipient_email, position,
            subject, body, status, {sent_at}, {gmail_message_id}, {gmail_thread_id},
            {error_message}, {replied_at}, {latest_reply_from},
            {latest_reply_subject}, {latest_reply_snippet}, {reply_count},
            {notes}, created_at, updated_at
        FROM outreach
        """
    )
    connection.execute("DROP TABLE outreach")
    connection.execute("ALTER TABLE outreach_upgrade RENAME TO outreach")


@contextmanager
def get_connection() -> Iterator[sqlite3.Connection]:
    """Provide one transaction-scoped connection and always close it."""
    DATABASE_PATH.parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(DATABASE_PATH, timeout=30)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA foreign_keys = ON")
    connection.execute("PRAGMA busy_timeout = 30000")
    connection.execute("PRAGMA synchronous = FULL")

    try:
        yield connection
        connection.commit()
    except Exception:
        connection.rollback()
        raise
    finally:
        connection.close()


def initialize_database() -> None:
    """Create the database and the tables implemented so far."""
    with get_connection() as connection:
        connection.execute("PRAGMA journal_mode = WAL")
        connection.execute("PRAGMA wal_autocheckpoint = 1000")
        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS user_profile (
                id INTEGER PRIMARY KEY CHECK (id = 1),
                name TEXT NOT NULL,
                target_job_title TEXT NOT NULL,
                professional_summary TEXT NOT NULL,
                linkedin_url TEXT,
                github_url TEXT,
                cv_file_path TEXT,
                cv_original_name TEXT,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            )
            """
        )
        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS companies (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT NOT NULL,
                website TEXT,
                contact_email TEXT NOT NULL,
                target_position TEXT NOT NULL,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            )
            """
        )
        _create_outreach_table(connection)
        _upgrade_outreach_table(connection)
        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS cv_analysis (
                id INTEGER PRIMARY KEY CHECK (id = 1),
                cv_file_path TEXT NOT NULL,
                analysis_json TEXT NOT NULL,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            )
            """
        )
        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS company_research (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                company_id INTEGER NOT NULL UNIQUE,
                company_website_snapshot TEXT NOT NULL,
                research_json TEXT NOT NULL,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                FOREIGN KEY (company_id) REFERENCES companies(id) ON DELETE CASCADE
            )
            """
        )
