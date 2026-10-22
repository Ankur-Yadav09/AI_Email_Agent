"""Real mailbox access: receive over IMAP, send over SMTP (defaults are for Gmail).

Configure with environment variables / .env (see .env.example). For Gmail use an
App Password (needs 2-Step Verification), never the account password.
"""
import imaplib
import os
from datetime import date, timedelta
import re
import smtplib
import ssl
from email.message import EmailMessage

from dotenv import load_dotenv

load_dotenv()

MAX_MESSAGES_PER_POLL = 10


def address() -> str:
    return os.environ.get("MAIL_ADDRESS", "").strip().lower()


def configured() -> bool:
    return bool(address() and os.environ.get("MAIL_APP_PASSWORD"))


def require_sender_auth() -> bool:
    """Only answer senders whose domain Gmail verified. Set REQUIRE_SENDER_AUTH=false only for testing."""
    return os.environ.get("REQUIRE_SENDER_AUTH", "true").strip().lower() != "false"


def _imap() -> imaplib.IMAP4_SSL:
    imap = imaplib.IMAP4_SSL(os.environ.get("IMAP_HOST") or "imap.gmail.com", timeout=30)
    imap.login(address(), os.environ["MAIL_APP_PASSWORD"])
    imap.select("INBOX")
    return imap


_MONTHS = ("Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec")


def unseen_criteria(today: date | None = None, with_senders: bool = True) -> list[str]:
    """IMAP search: unread mail from the last MAIL_MAX_AGE_DAYS days (default 2).

    The age limit keeps the agent away from a mailbox's old backlog. IMAP dates are day-granular.
    """
    days = int(os.environ.get("MAIL_MAX_AGE_DAYS") or 2)
    since = (today or date.today()) - timedelta(days=days)
    criteria = ["UNSEEN", "SINCE", f"{since.day:02d}-{_MONTHS[since.month - 1]}-{since.year}"]

    # Optional allow-list (MAIL_ONLY_FROM=a@x.com,b@y.com): other people's mail is never fetched or marked read.
    senders = [s.strip() for s in os.environ.get("MAIL_ONLY_FROM", "").split(",") if s.strip()]
    if senders and with_senders:
        clause = ["FROM", f'"{senders[-1]}"']
        for sender in reversed(senders[:-1]):
            clause = ["OR", "FROM", f'"{sender}"', *clause]  # IMAP OR takes exactly two terms
        criteria += clause
    return criteria


def count_unseen(with_senders: bool = True) -> int:
    """How many unread messages match (read-only; fetches nothing)."""
    imap = _imap()
    try:
        _, data = imap.uid("SEARCH", None, *unseen_criteria(with_senders=with_senders))  # type: ignore[arg-type]
        return len(data[0].split())
    finally:
        imap.logout()


def newest_first(uids: list[bytes], limit: int) -> list[bytes]:
    """UIDs grow over time, so the highest UIDs are the newest mail."""
    return sorted(uids, key=int, reverse=True)[:limit]


def fetch_unseen(limit: int = MAX_MESSAGES_PER_POLL) -> list[tuple[str, bytes]]:
    """(uid, raw message) for recent unread inbox mail, newest first. Uses PEEK so nothing is marked read yet."""
    imap = _imap()
    try:
        _, data = imap.uid("SEARCH", None, *unseen_criteria())  # type: ignore[arg-type]
        messages = []
        for uid in newest_first(data[0].split(), limit):
            _, parts = imap.uid("FETCH", uid.decode(), "(BODY.PEEK[])")
            messages.append((uid.decode(), parts[0][1]))
        return messages
    finally:
        imap.logout()


def mark_seen(uids: list[str]) -> None:
    if not uids:
        return
    imap = _imap()
    try:
        for uid in uids:
            imap.uid("STORE", uid, "+FLAGS", "(\\Seen)")
    finally:
        imap.logout()


def split_subject(draft: str, fallback: str) -> tuple[str, str]:
    """The LLM draft usually starts with 'Subject: ...'; separate it from the body."""
    lines = draft.strip().split("\n")
    match = re.match(r"^\**\s*subject\s*:\**\s*(.+)$", lines[0].strip(), re.I) if lines else None
    if match:
        return match.group(1).strip().strip("*").strip(), "\n".join(lines[1:]).strip()
    return fallback, draft.strip()


def reply_subject(original: str) -> str:
    original = " ".join(original.split())
    if not original:
        return "Your request"
    return original if original.lower().startswith("re:") else f"Re: {original}"


def send_reply(to: str, subject: str, body: str, in_reply_to: str | None = None) -> None:
    """Send a plain-text reply, threaded under the customer's message when possible."""
    message = EmailMessage()
    message["From"] = address()
    message["To"] = to
    message["Subject"] = " ".join(subject.split())  # one line: blocks header injection
    message["Auto-Submitted"] = "auto-replied"  # stops auto-responder loops (RFC 3834)
    if in_reply_to:
        message["In-Reply-To"] = in_reply_to
        message["References"] = in_reply_to
    message.set_content(body)

    with smtplib.SMTP(os.environ.get("SMTP_HOST") or "smtp.gmail.com", int(os.environ.get("SMTP_PORT") or 587), timeout=30) as smtp:
        smtp.starttls(context=ssl.create_default_context())
        smtp.login(address(), os.environ["MAIL_APP_PASSWORD"])
        smtp.send_message(message)
