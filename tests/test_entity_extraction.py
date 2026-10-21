import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))
    
from agents.email_agent import extract_entities


def test_scenario_4_account_and_card():
    text = (
        "Please share my account balance for account 1234567890 and also "
        "send last 3 transactions of my credit card 987654321."
    )
    e = extract_entities(text, {"transaction_count": 3})
    assert e["account_number"] == "1234567890"
    assert e["card_number"] == "987654321"
    assert e["transaction_count"] == 3


def test_masked_card_and_bad_llm_values():
    e = extract_entities(
        "last 5 transactions for card 4567XXXX8901",
        {"start_date": "April", "transaction_count": 99},
    )
    assert e["card_number"] == "4567XXXX8901"
    assert e["account_number"] is None
    assert e["start_date"] is None and e["transaction_count"] is None


def test_statement_dates():
    e = extract_entities(
        "statement for April 2026",
        {"start_date": "2026-04-01", "end_date": "2026-04-30"},
    )
    assert (e["start_date"], e["end_date"]) == ("2026-04-01", "2026-04-30")


def test_llm_outage_fails_safely(monkeypatch):
    import agents.email_agent as agent

    def boom(prompt, **kw):
        raise ConnectionError("ollama down")

    monkeypatch.setattr(agent, "generate_response", boom)
    result = agent.identify_intent("balance for account 1234567890")
    assert result["intents"] == []
    assert "error" in result


def test_priority_rule():
    from models.email_state import priority_for

    assert [priority_for(s) for s in ("neutral", "positive", "negative", "urgent")] == [
        "normal", "normal", "high", "high",
    ]
