import uuid

import pytest
from fastapi.testclient import TestClient

import agents.email_workflow as wf
import utils.mailbox as mailbox
from agents.email_agent import extract_entities
from main import app
from scripts.make_sample_inbox import build_eml
from utils.email_reader import parse_eml

GOOD_AUTH = "mx.google.com; dkim=pass header.i=@example.com header.s=sel; spf=pass smtp.mailfrom=demo@example.com"


@pytest.fixture(autouse=True)
def mail_env(monkeypatch):
    monkeypatch.setenv("MAIL_ADDRESS", "bank@gmail.com")
    monkeypatch.setenv("MAIL_APP_PASSWORD", "app password")
    monkeypatch.delenv("REQUIRE_SENDER_AUTH", raising=False)


def message(sender="demo@example.com", auth=GOOD_AUTH, message_id=None, extra=None):
    headers = {"Message-ID": message_id or f"<{uuid.uuid4()}@example.com>", **(extra or {})}
    if auth:
        headers["Authentication-Results"] = auth
    return build_eml(sender, "Balance please", "Balance for account 1234567890", headers=headers)


# ---------- sender authentication / automated mail ----------

def test_authenticated_only_when_gmail_verified_the_same_domain():
    assert parse_eml(message()).authenticated
    assert not parse_eml(message(auth=None)).authenticated
    # DKIM passed, but for the attacker's domain, not the From domain
    assert not parse_eml(message(auth="mx.google.com; dkim=pass header.i=@evil.com")).authenticated
    # a header added by the sender (not by Gmail) is never trusted
    assert not parse_eml(message(auth="mx.evil.com; dkim=pass header.i=@example.com")).authenticated


def test_automated_mail_detected():
    assert parse_eml(message(sender="mailer-daemon@googlemail.com")).automated
    assert parse_eml(message(extra={"Auto-Submitted": "auto-replied"})).automated
    assert parse_eml(message(extra={"Precedence": "bulk"})).automated
    assert not parse_eml(message()).automated


# ---------- helpers & SMTP ----------

def test_split_subject_and_reply_subject():
    assert mailbox.split_subject("Subject: Your balance\n\nDear Customer", "x") == ("Your balance", "Dear Customer")
    assert mailbox.split_subject("**Subject:** Hi\nBody", "x") == ("Hi", "Body")
    assert mailbox.split_subject("Dear Customer", "Re: A") == ("Re: A", "Dear Customer")
    assert mailbox.reply_subject("Balance") == "Re: Balance"
    assert mailbox.reply_subject("Re: Balance") == "Re: Balance"


def test_send_reply_builds_threaded_message(monkeypatch):
    sent = {}

    class FakeSMTP:
        def __init__(self, host, port, timeout):
            sent["server"] = (host, port)

        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

        def starttls(self, context):
            sent["tls"] = True

        def login(self, user, password):
            sent["login"] = user

        def send_message(self, msg):
            sent["msg"] = msg

    monkeypatch.setattr(mailbox.smtplib, "SMTP", FakeSMTP)
    mailbox.send_reply("demo@example.com", "Re: Hi\nBcc: evil@x.com", "Body", in_reply_to="<abc@x>")

    msg = sent["msg"]
    assert sent["server"] == ("smtp.gmail.com", 587) and sent["tls"] and sent["login"] == "bank@gmail.com"
    assert msg["To"] == "demo@example.com" and msg["In-Reply-To"] == "<abc@x>"
    assert msg["Auto-Submitted"] == "auto-replied"
    assert "\n" not in msg["Subject"] and msg["Bcc"] is None  # header injection blocked


# ---------- end to end: poll -> process -> approve -> send ----------

@pytest.fixture
def fakes(monkeypatch):
    state = {"sent": [], "seen": [], "inbox": []}
    monkeypatch.setattr(mailbox, "fetch_unseen", lambda: state["inbox"])
    monkeypatch.setattr(mailbox, "mark_seen", lambda uids: state["seen"].extend(uids))
    monkeypatch.setattr(mailbox, "send_reply", lambda *args: state["sent"].append(args))
    monkeypatch.setattr(
        wf, "identify_intent",
        lambda text, attachment_text="": {
            "intents": ["check_balance"], "sentiment": "neutral",
            "entities": extract_entities(text, {}, attachment_text),
        },
    )
    return state


def test_poll_filters_unsafe_mail_then_approval_sends_reply(fakes):
    good_id = f"<{uuid.uuid4()}@example.com>"
    fakes["inbox"] = [
        ("1", message(message_id=good_id)),
        ("2", message(auth=None)),                                   # sender not verified
        ("3", message(auth="mx.google.com; dkim=pass header.i=@evil.com")),  # forged From
        ("4", message(sender="mailer-daemon@googlemail.com")),       # bounce
        ("5", message(sender="bank@gmail.com")),                     # our own mail
    ]

    with TestClient(app) as client:
        results = client.post("/emails/mailbox/poll").json()["results"]
        assert "email_id" in results[0] and results[0]["status"] == "pending_review"
        assert all("skipped" in r for r in results[1:])
        assert fakes["seen"] == ["1", "2", "3", "4", "5"]

        # the same message again is not processed twice
        fakes["inbox"] = [("1", message(message_id=good_id))]
        assert client.post("/emails/mailbox/poll").json()["results"][0]["skipped"] == "already processed"

        # nothing is sent until a human approves
        email_id = results[0]["email_id"]
        assert fakes["sent"] == []

        review = client.post(f"/emails/{email_id}/review", json={"approved": True}).json()
        assert review["delivery_status"] == "sent"
        to, subject, body, in_reply_to = fakes["sent"][0]
        assert (to, subject, in_reply_to) == ("demo@example.com", "Re: Balance please", good_id)
        assert body.startswith("Dear Demo Customer,") and "Available Balance: INR 25,000.00" in body

        assert client.post(f"/emails/{email_id}/review", json={"approved": True}).status_code == 409
        assert len(fakes["sent"]) == 1
        assert client.get(f"/emails/{email_id}").json()["delivery_status"] == "sent"


def test_rejection_sends_nothing_and_send_failure_keeps_approval(fakes, monkeypatch):
    fakes["inbox"] = [("1", message()), ("2", message())]

    with TestClient(app) as client:
        first, second = client.post("/emails/mailbox/poll").json()["results"]

        rejected = client.post(f"/emails/{first['email_id']}/review", json={"approved": False}).json()
        assert rejected["delivery_status"] == "not_sent" and fakes["sent"] == []

        def boom(*args):
            raise OSError("smtp down")

        monkeypatch.setattr(mailbox, "send_reply", boom)
        failed = client.post(f"/emails/{second['email_id']}/review", json={"approved": True}).json()
        assert failed["status"] == "approved" and failed["delivery_status"] == "send_failed"


def test_poll_requires_configuration(monkeypatch):
    monkeypatch.delenv("MAIL_APP_PASSWORD")
    with TestClient(app) as client:
        assert client.post("/emails/mailbox/poll").status_code == 503


def test_search_is_recent_only_and_newest_first(monkeypatch):
    from datetime import date

    assert mailbox.unseen_criteria(date(2026, 10, 19)) == ["UNSEEN", "SINCE", "17-Oct-2026"]
    assert mailbox.unseen_criteria(date(2026, 1, 1)) == ["UNSEEN", "SINCE", "30-Dec-2025"]
    monkeypatch.setenv("MAIL_MAX_AGE_DAYS", "7")
    assert mailbox.unseen_criteria(date(2026, 10, 19))[2] == "12-Oct-2026"

    uids = [b"9", b"100", b"7", b"42"]
    assert mailbox.newest_first(uids, 2) == [b"100", b"42"]  # numeric, not alphabetical


def test_only_from_allow_list(monkeypatch):
    from datetime import date

    day = date(2026, 10, 19)
    monkeypatch.setenv("MAIL_ONLY_FROM", "a@x.com")
    assert mailbox.unseen_criteria(day)[3:] == ["FROM", '"a@x.com"']

    monkeypatch.setenv("MAIL_ONLY_FROM", "a@x.com, b@y.com ,c@z.com")
    assert mailbox.unseen_criteria(day)[3:] == [
        "OR", "FROM", '"a@x.com"', "OR", "FROM", '"b@y.com"', "FROM", '"c@z.com"',
    ]

    monkeypatch.delenv("MAIL_ONLY_FROM")
    assert len(mailbox.unseen_criteria(day)) == 3


def test_pending_queue_lists_urgent_first(monkeypatch):
    from utils.email_database import create_email_record, update_email_record

    tag = uuid.uuid4().hex[:8]
    ids = {}
    for name, sentiment in (("calm", "neutral"), ("angry", "urgent")):
        ids[name] = f"queue-{name}-{tag}"
        create_email_record(ids[name], "x", sender_email="demo@example.com", subject=f"{name} {tag}")
        update_email_record(ids[name], status="pending_review", sentiment=sentiment)
    done = f"queue-done-{tag}"
    create_email_record(done, "x", subject=f"done {tag}")
    update_email_record(done, status="approved")

    with TestClient(app) as client:
        body = client.get("/emails", params={"status": "pending_review", "limit": 200}).json()

    mine = [e for e in body["emails"] if tag in e["subject"]]
    assert [e["subject"].split()[0] for e in mine] == ["angry", "calm"]  # urgent first, no approved ones
    assert mine[0]["priority"] == "high" and mine[1]["priority"] == "normal"
    assert "sentiment" not in mine[0] and mine[0]["sender_email"] == "demo@example.com"


def test_empty_poll_explains_itself(monkeypatch):
    monkeypatch.setenv("MAIL_ADDRESS", "bank@gmail.com")
    monkeypatch.setenv("MAIL_APP_PASSWORD", "x")
    monkeypatch.setenv("MAIL_ONLY_FROM", "customer@gmail.com")
    monkeypatch.setattr(mailbox, "fetch_unseen", lambda: [])
    monkeypatch.setattr(mailbox, "mark_seen", lambda uids: None)
    monkeypatch.setattr(mailbox, "count_unseen", lambda with_senders=True: 7)

    with TestClient(app) as client:
        body = client.post("/emails/mailbox/poll").json()

    assert body["count"] == 0 and 'FROM "customer@gmail.com"' in body["searched"]
    assert "7 unread message(s)" in body["note"] and "other senders" in body["note"]

    monkeypatch.setattr(mailbox, "count_unseen", lambda with_senders=True: 0)
    with TestClient(app) as client:
        assert "MAIL_MAX_AGE_DAYS" in client.post("/emails/mailbox/poll").json()["note"]


def test_review_refuses_a_workflow_that_is_not_paused(fakes):
    """A pending record without a saved workflow must not be silently re-run and overwritten."""
    from utils.email_database import create_email_record, get_email_record, update_email_record

    email_id = f"orphan-{uuid.uuid4()}"
    create_email_record(email_id, "x", sender_email="demo@example.com", subject="orphan")
    update_email_record(email_id, status="pending_review", draft="Subject: keep me\n\nBody")

    with TestClient(app) as client:
        response = client.post(f"/emails/{email_id}/review", json={"approved": True})

    assert response.status_code == 409 and "saved workflow state" in response.json()["detail"]
    record = get_email_record(email_id)
    assert record["status"] == "pending_review" and record["draft"] == "Subject: keep me\n\nBody"
    assert fakes["sent"] == []


def test_out_of_scope_email_reaches_the_queue_flagged_and_gets_the_support_text(fakes, monkeypatch):
    """The 'update my address' case: no reply used to be sent at all."""
    monkeypatch.setenv("SUPPORT_CONTACT_TEXT", "Please visit a branch with a photo ID.")
    monkeypatch.setattr(wf, "identify_intent", lambda text, attachment_text="": {"intents": [], "sentiment": "neutral", "entities": {}})
    message_id = f"<{uuid.uuid4()}@example.com>"
    fakes["inbox"] = [("1", build_eml(
        "demo@example.com", "Update my address", "Please update my home address.",
        headers={"Message-ID": message_id, "Authentication-Results": GOOD_AUTH},
    ))]

    with TestClient(app) as client:
        result = client.post("/emails/mailbox/poll").json()["results"][0]
        assert result["status"] == "pending_review"

        queue = client.get("/emails", params={"status": "pending_review", "limit": 200}).json()["emails"]
        entry = next(e for e in queue if e["email_id"] == result["email_id"])
        assert entry["needs_person"] is True and "intents" not in entry

        assert fakes["sent"] == []  # still nothing sent without approval
        review = client.post(f"/emails/{result['email_id']}/review", json={"approved": True}).json()
        assert review["delivery_status"] == "sent"

    to, subject, body, in_reply_to = fakes["sent"][0]
    assert (to, subject, in_reply_to) == ("demo@example.com", "Re: Update my address", message_id)
    assert "Dear Demo Customer," in body and "Please visit a branch with a photo ID." in body


def test_normal_requests_are_not_flagged_as_needing_a_person(fakes):
    fakes["inbox"] = [("1", message())]
    with TestClient(app) as client:
        email_id = client.post("/emails/mailbox/poll").json()["results"][0]["email_id"]
        queue = client.get("/emails", params={"status": "pending_review", "limit": 200}).json()["emails"]
    assert next(e for e in queue if e["email_id"] == email_id)["needs_person"] is False
