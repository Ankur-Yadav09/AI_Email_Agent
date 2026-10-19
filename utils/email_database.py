
import json
import os
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

# Store email.db in the project's root directory.
# This path works even when the application is launched from another directory.
PROJECT_ROOT = Path(__file__).resolve().parent.parent
DATABASE_PATH = Path(os.environ.get("DATA_DIR") or PROJECT_ROOT) / "email.db"


def get_connection():
    """Create a connection to the email database."""
    connection = sqlite3.connect(DATABASE_PATH, timeout=10)
    connection.row_factory = sqlite3.Row
    return connection


def initialize_email_database():
    """Create the email_records table if it does not exist."""
    with get_connection() as connection:
        connection.execute("""
            CREATE TABLE IF NOT EXISTS email_records (
                email_id TEXT PRIMARY KEY,
                email_text TEXT NOT NULL,
                status TEXT NOT NULL DEFAULT 'received',
                intents TEXT NOT NULL DEFAULT '[]',
                tool_results TEXT NOT NULL DEFAULT '[]',
                draft TEXT NOT NULL DEFAULT '',
                errors TEXT NOT NULL DEFAULT '[]',
                approval_status TEXT NOT NULL DEFAULT 'pending',
                reviewer_feedback TEXT NOT NULL DEFAULT '',
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            )
        """)

        # Migration: add columns that older databases do not have yet.
        columns = {row["name"] for row in connection.execute("PRAGMA table_info(email_records)")}
        for name, definition in (
            ("sentiment", "TEXT NOT NULL DEFAULT 'neutral'"),
            ("sender_email", "TEXT"),
            ("subject", "TEXT NOT NULL DEFAULT ''"),
            ("message_id", "TEXT"),  # Message-ID header; used for threading and de-duplication
            ("delivery_status", "TEXT NOT NULL DEFAULT 'not_sent'"),  # not_sent / sent / send_failed / ...
        ):
            if name not in columns:
                connection.execute(f"ALTER TABLE email_records ADD COLUMN {name} {definition}")


def create_email_record(
    email_id: str,
    email_text: str,
    sender_email: str | None = None,
    subject: str = "",
    message_id: str | None = None,
):
    """Save a newly received email before processing begins."""
    now = datetime.now(timezone.utc).isoformat()  # noqa: UP017

    with get_connection() as connection:
        connection.execute("""
            INSERT INTO email_records (
                email_id, email_text, sender_email, subject, message_id,
                created_at, updated_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?)
        """, (email_id, email_text, sender_email, subject, message_id, now, now))


def list_email_records(status: str | None = None, limit: int = 50) -> list[dict[str, Any]]:
    """The most recent emails (optionally one status), summarised for a work queue."""
    query = "SELECT email_id, status, subject, sender_email, sentiment, created_at FROM email_records"
    params: list[Any] = []
    if status:
        query += " WHERE status = ?"
        params.append(status)
    query += " ORDER BY created_at DESC LIMIT ?"
    params.append(limit)

    with get_connection() as connection:
        return [dict(row) for row in connection.execute(query, params).fetchall()]


def email_id_for_message_id(message_id: str) -> str | None:
    """The email_id already created for this Message-ID header, if any."""
    with get_connection() as connection:
        row = connection.execute(
            "SELECT email_id FROM email_records WHERE message_id = ?", (message_id,)
        ).fetchone()
    return row["email_id"] if row else None


def get_email_record(email_id: str) -> dict[str, Any] | None:
    """Fetch an email record and convert JSON columns into Python objects."""
    with get_connection() as connection:
        row = connection.execute(
            "SELECT * FROM email_records WHERE email_id = ?",
            (email_id,),
        ).fetchone()

    if row is None:
        return None

    record = dict(row)

    # Convert JSON text back into Python lists.
    for field in ("intents", "tool_results", "errors"):
        record[field] = json.loads(record[field])

    return record


def update_email_record(email_id: str, **fields):
    """Update only the supplied fields of an existing email record."""
    allowed_fields = {
        "status",
        "intents",
        "tool_results",
        "draft",
        "errors",
        "approval_status",
        "reviewer_feedback",
        "sentiment",
        "delivery_status",
    }

    updates = {}

    for field, value in fields.items():
        if field not in allowed_fields:
            raise ValueError(f"Unsupported email field: {field}")

        # Lists and dictionaries are stored as JSON strings in SQLite.
        if field in {"intents", "tool_results", "errors"}:
            value = json.dumps(value, ensure_ascii=False)

        updates[field] = value

    if not updates:
        return

    updates["updated_at"] = datetime.now(timezone.utc).isoformat()  # noqa: UP017

    assignments = ", ".join(f"{field} = ?" for field in updates)
    values = list(updates.values()) + [email_id]

    with get_connection() as connection:
        cursor = connection.execute(
            f"UPDATE email_records SET {assignments} WHERE email_id = ?",
            values,
        )

        if cursor.rowcount == 0:
            raise ValueError(f"Email record not found: {email_id}")
