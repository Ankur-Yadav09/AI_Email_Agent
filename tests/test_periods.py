import json
from datetime import date

import pytest

import agents.email_agent as agent
from agents.email_agent import extract_entities
from utils.periods import statement_period

TODAY = date(2026, 10, 20)


@pytest.mark.parametrize("text, expected", [
    # the business scenario, and its spelling variants
    ("Kindly send my bank statement for April 2026.", ((2026, 4, 1), (2026, 4, 30))),
    ("statement for apr 2026 please", ((2026, 4, 1), (2026, 4, 30))),
    ("statement for Sept, 2025", ((2025, 9, 1), (2025, 9, 30))),
    ("statement for February 2024", ((2024, 2, 1), (2024, 2, 29))),  # leap year
    ("statement for 04/2026", ((2026, 4, 1), (2026, 4, 30))),
    # relative
    ("please send last month's statement. I mean the statement for last month", ((2026, 9, 1), (2026, 9, 30))),
    ("statement for this month", ((2026, 10, 1), (2026, 10, 20))),
    # a bare month means the most recent one
    ("I need the statement for March", ((2026, 3, 1), (2026, 3, 31))),
    ("statement for the month of December", ((2025, 12, 1), (2025, 12, 31))),
    # explicit ranges, day-first
    ("statement from 01/04/2026 to 30/04/2026", ((2026, 4, 1), (2026, 4, 30))),
    ("statement from 2026-04-05 till 2026-04-20", ((2026, 4, 5), (2026, 4, 20))),
    ("statement from 1st April 2026 to 15 April 2026", ((2026, 4, 1), (2026, 4, 15))),
    ("statement April 15, 2026 until May 3, 2026", ((2026, 4, 15), (2026, 5, 3))),
    ("statement from 20/04/2026 to 05/04/2026", ((2026, 4, 5), (2026, 4, 20))),  # reversed order
    # the running month is clipped to today
    ("statement for October 2026", ((2026, 10, 1), (2026, 10, 20))),
])
def test_period_is_found(text, expected):
    start, end = expected
    assert statement_period(text, TODAY) == (date(*start), date(*end))


@pytest.mark.parametrize("text", [
    "send my statement",                                  # no period at all
    "statement as of 15 April 2026",                      # one day is not a period
    "statement for May 2027",                             # future
    "May I get my statement?",                            # the verb 'may', not the month
    "I will march to the bank. statement please",         # the verb 'march'
    "statement for 13/2026",                              # month 13 does not exist
    "statement from 31/02/2026 to 30/04/2026 please",     # impossible date leaves only one real date
])
def test_ambiguous_or_missing_gives_none(text):
    assert statement_period(text, TODAY) is None


def test_email_text_beats_the_llms_dates():
    entities = extract_entities(
        "Subject: Statement\n\nKindly send my bank statement for April 2026.",
        {"start_date": "2026-01-01", "end_date": "2026-01-31"},  # the model got it wrong
    )
    assert (entities["start_date"], entities["end_date"]) == ("2026-04-01", "2026-04-30")


def test_llm_dates_are_only_a_checked_fallback():
    llm = {"start_date": "2026-02-01", "end_date": "2026-02-28"}
    assert extract_entities("send statement of the quarter", llm)["start_date"] == "2026-02-01"

    backwards = {"start_date": "2026-03-01", "end_date": "2026-02-01"}
    entities = extract_entities("send statement of the quarter", backwards)
    assert entities["start_date"] is None and entities["end_date"] is None


def test_scenario_3_end_to_end_with_a_model_that_returns_no_dates(monkeypatch):
    """The 3B model returned no dates for this exact email; the parser must still find the period."""
    monkeypatch.setattr(
        agent, "generate_response",
        lambda prompt, **kw: json.dumps({"intents": ["request_statement"], "sentiment": "neutral"}),
    )
    result = agent.identify_intent("Subject: Statement request\n\nKindly send my bank statement for April 2026.")
    assert result["intents"] == ["request_statement"]
    assert (result["entities"]["start_date"], result["entities"]["end_date"]) == ("2026-04-01", "2026-04-30")


def test_scenario_3_produces_a_statement_reference_and_reply():
    """Email -> parsed period -> real MCP tool -> template reply, with only the LLM faked."""
    import asyncio

    from agents.email_workflow import supported_requests
    from models.email_state import EmailState
    from utils.database import initialize_database
    from utils.reply_builder import build_reply

    initialize_database()
    text = "Subject: Statement request\n\nKindly send my bank statement for April 2026."
    state = EmailState(
        email_text=text,
        intents=["request_statement"],
        entities=extract_entities(text, {}),
        verified_customer_id="CUST001",
    )
    result = asyncio.run(supported_requests(state))

    assert result["status"] == "banking_data_retrieved" and not result["errors"]
    data = result["tool_results"][0]["data"]
    assert (data["start_date"], data["end_date"]) == ("2026-04-01", "2026-04-30")
    assert data["statement_reference"].startswith("STMT-")

    reply = build_reply(text, result["customer_name"], result["tool_results"], result["errors"])
    assert "Period: 01 Apr 2026 to 30 Apr 2026" in reply and data["statement_reference"] in reply


@pytest.mark.parametrize("raw", [
    'Here is the result:\n{"intents": ["request_statement"], "sentiment": "neutral"}\n\nNote: values like {x} mean nothing.',
    '{"intents": ["request_statement"], "sentiment": "neutral"} } trailing',
    'I think {maybe} this: {"intents": ["request_statement"], "sentiment": "neutral"}',
])
def test_chatty_json_with_stray_braces_is_still_parsed(monkeypatch, raw):
    monkeypatch.setattr(agent, "generate_response", lambda prompt, **kw: raw)
    assert agent.identify_intent("Please send my account statement.")["intents"] == ["request_statement"]


def test_classifier_asks_ollama_for_json_only(monkeypatch):
    seen = {}
    monkeypatch.setattr(agent, "generate_response", lambda prompt, **kw: seen.update(kw) or '{"intents": []}')
    agent.identify_intent("hello")
    assert seen == {"json_mode": True}
