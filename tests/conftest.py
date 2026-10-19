import os
import tempfile

# Tests get their own throwaway databases, so they never depend on (or change) your real
# banking.db / email.db. Must run before any project module is imported.
os.environ["DATA_DIR"] = tempfile.mkdtemp(prefix="email_agent_tests_")

import pytest

MAIL_SETTINGS = (
    "MAIL_ADDRESS", "MAIL_APP_PASSWORD", "MAIL_ONLY_FROM", "MAIL_MAX_AGE_DAYS", "MAIL_POLL_SECONDS",
    "IMAP_HOST", "SMTP_HOST", "SMTP_PORT", "REQUIRE_SENDER_AUTH",
)


@pytest.fixture(autouse=True)
def no_real_mail_settings(monkeypatch):
    """utils.mailbox loads .env at import; clear it so tests never use real credentials or filters."""
    for name in MAIL_SETTINGS:
        monkeypatch.delenv(name, raising=False)
