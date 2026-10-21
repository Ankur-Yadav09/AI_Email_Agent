import asyncio
import uuid

import pytest
from langgraph.checkpoint.memory import MemorySaver
from langgraph.types import Command

import agents.email_workflow as wf
from agents.email_agent import URGENT_WORDS
from models.email_state import EmailState
from utils.reply_builder import build_handoff_reply, support_text

EMAIL = "Subject: Update address\n\nPlease update my home address."


def test_handoff_reply_wording_and_name():
    reply = build_handoff_reply(EMAIL, "Ankur Yadav")
    assert reply.startswith("Subject: Re: Update address\n\nDear Ankur Yadav,")
    assert "We are unable to complete this request automatically." in reply
    assert "Please contact customer support for assistance." in reply  # default wording
    assert reply.endswith("Thank you,\nCustomer Support Team")
    assert "within" not in reply and "call you" not in reply  # promises nothing


def test_support_text_is_configurable(monkeypatch):
    monkeypatch.setenv("SUPPORT_CONTACT_TEXT", "  Please log in to net banking\n or call 1800-000-000.  ")
    assert support_text() == "Please log in to net banking or call 1800-000-000."
    assert "Please log in to net banking or call 1800-000-000." in build_handoff_reply(EMAIL, "A")
    assert "customer support for assistance" not in build_handoff_reply(EMAIL, "A")


def test_handoff_without_subject_or_name_and_with_tone():
    reply = build_handoff_reply("Please update my address", "", "negative")
    assert reply.startswith("Subject: Your request\n\nDear Customer,")
    assert "We are sorry for the inconvenience" in reply


@pytest.mark.parametrize("text, urgent", [
    ("There is an unauthorised transaction on my card", True),
    ("I think this is fraud", True),
    ("my card was stolen yesterday", True),
    ("I lost my card", True),
    ("my card is blocked", True),
    ("I want to dispute this charge", True),
    ("I was hacked", True),
    ("Please share my balance", False),
    ("Statement for April please", False),
    ("We are running a hackathon", False),
])
def test_urgent_words(text, urgent):
    assert bool(URGENT_WORDS.search(text)) is urgent


def test_fraud_email_is_urgent_even_if_the_model_failed(monkeypatch):
    monkeypatch.setattr(wf, "identify_intent", lambda text, attachment_text="": {"intents": [], "error": "Unable to classify"})
    out = wf.classify_email(EmailState(email_text="Subject: help\n\nSomeone made an unauthorised transaction"), None)
    assert out["sentiment"] == "urgent" and out["intents"] == []

    monkeypatch.setattr(wf, "identify_intent", lambda text, attachment_text="": {"intents": [], "sentiment": "neutral"})
    assert wf.classify_email(EmailState(email_text=EMAIL), None)["sentiment"] == "neutral"


def _run(workflow, payload, config):
    return asyncio.run(workflow.ainvoke(payload, config=config))


def test_unsupported_request_waits_for_review_and_can_be_approved(monkeypatch):
    monkeypatch.setenv("SUPPORT_CONTACT_TEXT", "Please call 1800-000-000.")
    monkeypatch.setattr(wf, "identify_intent", lambda text, attachment_text="": {"intents": [], "sentiment": "neutral", "entities": {}})
    workflow = wf.build_email_workflow(MemorySaver())
    config = {"configurable": {"thread_id": f"handoff-{uuid.uuid4()}"}}

    result = _run(workflow, {"email_text": EMAIL, "sender_email": "demo@example.com",
                             "verified_customer_id": "CUST001", "customer_name": "Ankur Yadav"}, config)

    # it no longer ends silently: it pauses for a person, with the hand-off draft
    interrupt = result["__interrupt__"][0].value
    assert "Dear Ankur Yadav," in interrupt["draft"] and "Please call 1800-000-000." in interrupt["draft"]

    final = _run(workflow, Command(resume={"approved": True, "feedback": ""}), config)
    assert final["approval_status"] == "approved" and "Please call 1800-000-000." in final["draft"]


def test_fraud_report_is_never_answered_with_account_data(monkeypatch):
    """The real model tagged a fraud email as get_transactions; that must become a hand-off."""
    monkeypatch.setattr(
        wf, "identify_intent",
        lambda text, attachment_text="": {"intents": ["get_transactions"], "sentiment": "negative", "entities": {}},
    )
    text = "Subject: Help\n\nThere is an unauthorised transaction on my card, I think it is fraud!"
    out = wf.classify_email(EmailState(email_text=text), None)
    assert out["intents"] == [] and out["sentiment"] == "urgent"
    assert wf.route_request(EmailState(email_text=text, intents=out["intents"])) == "unsupported"

    # an ordinary card question is untouched
    plain = wf.classify_email(EmailState(email_text="Subject: x\n\nShow my last 3 card transactions"), None)
    assert plain["intents"] == ["get_transactions"]
