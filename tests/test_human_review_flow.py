import asyncio
import uuid

import pytest
from langgraph.checkpoint.memory import MemorySaver
from langgraph.types import Command

import agents.email_workflow as wf


@pytest.fixture
def workflow(monkeypatch):
    """Workflow with the LLM mocked; banking tools are the real local MCP server."""
    monkeypatch.setattr(
        wf, "identify_intent",
        lambda text, attachment_text="": {
            "intents": ["check_balance"],
            "sentiment": "neutral",
            "entities": {"account_number": "1234567890"},
        },
    )
    return wf.build_email_workflow(MemorySaver())


def start(workflow):
    config = {"configurable": {"thread_id": f"review-{uuid.uuid4()}"}}
    result = asyncio.run(workflow.ainvoke(
        {
            "email_text": "Balance for account 1234567890?",
            "sender_email": "demo@example.com",
            "verified_customer_id": "CUST001",
        },
        config=config,
    ))
    return config, result


def test_pauses_for_review_with_draft(workflow):
    _, result = start(workflow)
    interrupt = result["__interrupt__"][0].value
    assert interrupt["type"] == "email_draft_review"
    assert "Available Balance: INR 25,000.00" in interrupt["draft"]


def test_approve_with_edit(workflow):
    config, _ = start(workflow)
    final = asyncio.run(workflow.ainvoke(
        Command(resume={"approved": True, "edited_draft": "Edited by reviewer", "feedback": "ok"}),
        config=config,
    ))
    assert final["approval_status"] == "approved"
    assert final["draft"] == "Edited by reviewer"


def test_reject(workflow):
    config, _ = start(workflow)
    final = asyncio.run(workflow.ainvoke(
        Command(resume={"approved": False, "feedback": "wrong tone"}),
        config=config,
    ))
    assert final["approval_status"] == "rejected"
    assert final["reviewer_feedback"] == "wrong tone"


def test_urgent_sentiment_sets_tone_and_high_priority(monkeypatch):
    monkeypatch.setattr(
        wf, "identify_intent",
        lambda text, attachment_text="": {"intents": ["check_balance"], "sentiment": "urgent",
                      "entities": {"account_number": "1234567890"}},
    )
    workflow = wf.build_email_workflow(MemorySaver())

    _, result = start(workflow)

    interrupt = result["__interrupt__"][0].value
    assert "We understand that this is urgent." in interrupt["draft"]
    assert (interrupt["sentiment"], interrupt["priority"]) == ("urgent", "high")


def test_neutral_sentiment_adds_no_tone_rule_and_normal_priority(workflow, monkeypatch):
    _, result = start(workflow)
    assert result["__interrupt__"][0].value["priority"] == "normal"
