import asyncio
import uuid

from fastapi.testclient import TestClient

from agents.email_workflow import supported_requests
from main import app
from models.email_state import EmailState
from utils.database import initialize_database

initialize_database()
HEADERS = {"X-Demo-Customer-ID": "CUST001"}


def run(intents, entities):
    state = EmailState(
        email_text="x",
        intents=intents,
        entities=entities,
        verified_customer_id="CUST001",
    )
    return asyncio.run(supported_requests(state))


def test_scenario_5_valid_account_invalid_card():
    result = run(
        ["check_balance", "get_transactions"],
        {"account_number": "1234567890", "card_number": "999999999"},
    )
    assert result["status"] == "partial_success"
    assert [r["intent"] for r in result["tool_results"]] == ["check_balance"]
    assert "recheck the card" in result["errors"][0]


def test_all_valid():
    result = run(
        ["check_balance", "get_transactions"],
        {"account_number": "1234567890", "card_number": "4567XXXX8901"},
    )
    assert result["status"] == "banking_data_retrieved"


def test_rest_validate_endpoints():
    client = TestClient(app)
    ok = client.post("/api/accounts/validate", json={"account_number": "1234567890"}, headers=HEADERS)
    assert ok.json() == {"status": "VALID", "customer_name": "Demo Customer"}
    bad = client.post("/api/cards/validate", json={"card_number": "999999999"}, headers=HEADERS)
    assert bad.json()["status"] == "INVALID"


def test_results_keep_intent_order_when_run_in_parallel():
    result = run(
        ["get_transactions", "check_balance"],
        {"account_number": "1234567890", "card_number": "4567XXXX8901"},
    )
    assert [r["intent"] for r in result["tool_results"]] == ["get_transactions", "check_balance"]


def test_timeout_is_retried_once_then_reported(monkeypatch):
    import agents.email_workflow as wf

    monkeypatch.setattr(wf, "TOOL_TIMEOUT_SECONDS", 0.0001)
    state = EmailState(email_text="x", intents=["check_balance"], verified_customer_id="CUST001")
    thread_id = f"test-timeout-{uuid.uuid4()}"  # audit.log persists, so use a fresh id per run
    config = {"configurable": {"thread_id": thread_id}}
    result = asyncio.run(wf.supported_requests(state, config)) # type: ignore

    assert result["status"] == "failed"
    from utils.logging_config import LOG_DIR
    lines = [l for l in (LOG_DIR / "audit.log").read_text(encoding="utf-8").splitlines() if thread_id in l]
    assert sum('"event": "tool_timeout"' in l for l in lines) == 2  # first try + one retry
