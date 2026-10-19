import asyncio
import uuid

from agents.email_workflow import supported_requests
from models.email_state import EmailState
from utils.logging_config import LOG_DIR, mask_text, mask_value, setup_logging


def test_mask_text():
    assert mask_text("account 1234567890") == "account XXXX7890"
    assert mask_text("card 4567XXXX8901") == "card XXXX8901"
    # money must survive
    assert mask_text("INR 25000.00 and 1,25,000") == "INR 25000.00 and 1,25,000"
    assert mask_value({"a": ["1234567890"]}) == {"a": ["XXXX7890"]}
    # ids used for audit correlation must never be altered
    for uid in ("d1d6c11b-3ab3-4c7e-bf68-909191e03412", "123456-1234-1234-1234-123456789012"):
        assert mask_text(f"email_id={uid}") == f"email_id={uid}"


def test_audit_log_has_no_raw_numbers():
    setup_logging()
    state = EmailState(
        email_text="x",
        intents=["check_balance", "get_transactions"],
        entities={"account_number": "1234567890", "card_number": "999999999"},
        verified_customer_id="CUST001",
    )
    thread_id = f"test-audit-{uuid.uuid4()}"  # audit.log persists, so use a fresh id per run
    config = {"configurable": {"thread_id": thread_id}}
    asyncio.run(supported_requests(state, config))

    lines = [
        line for line in (LOG_DIR / "audit.log").read_text(encoding="utf-8").splitlines()
        if thread_id in line
    ]
    assert any('"event": "tool_call"' in line for line in lines)
    assert any('"event": "requests_finished"' in line for line in lines)
    assert not any("1234567890" in line or "999999999" in line for line in lines)
