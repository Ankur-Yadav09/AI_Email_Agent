import json

import agents.email_agent as agent
from utils.reply_builder import build_reply, inr

BALANCE = {"intent": "check_balance", "data": {"account_reference": "1234567890", "balance": 25000.0, "currency": "INR"}}
CARD = {
    "intent": "get_transactions",
    "data": {
        "card_reference": "4567XXXX8901",
        "transactions": [
            {"transaction_id": "T1", "date": "2026-10-12", "description": "Grocery Store", "amount": 1250.0, "transaction_type": "debit"},
        ],
    },
}
STATEMENT = {
    "intent": "request_statement",
    "data": {"statement_reference": "STMT-ABCDEF123456", "start_date": "2026-04-01", "end_date": "2026-04-30", "status": "submitted"},
}


def test_inr_uses_indian_grouping():
    assert inr(25000) == "INR 25,000.00"
    assert inr(125000) == "INR 1,25,000.00"
    assert inr(12345678.5) == "INR 1,23,45,678.50"
    assert inr(999) == "INR 999.00"


def test_success_reply_has_name_masked_numbers_and_signoff():
    reply = build_reply("Subject: Balance Enquiry\n\nbalance please", "Ankur Yadav", [BALANCE, CARD, STATEMENT], [])
    assert reply.startswith("Subject: Re: Balance Enquiry\n\nDear Ankur Yadav,")
    assert "We have processed your request successfully." in reply
    assert "Account Number: XXXX7890" in reply and "1234567890" not in reply
    assert "Available Balance: INR 25,000.00" in reply
    assert "12 Oct 2026  Grocery Store  INR 1,250.00 (debit)" in reply
    assert "Card Number: XXXX8901" in reply
    assert "Period: 01 Apr 2026 to 30 Apr 2026" in reply and "STMT-ABCDEF123456" in reply
    assert reply.endswith("Thank you,\nCustomer Support Team")


def test_scenario_5_partial_failure():
    errors = ["get_transactions: The card number provided could not be validated. Please recheck the card details and resend your request."]
    reply = build_reply("Subject: x\n\ny", "Ankur Yadav", [BALANCE], errors)
    assert "We have processed part of your request." in reply
    assert "Available Balance: INR 25,000.00" in reply
    assert "Credit Card Transactions:\n  The card number provided could not be validated. Please recheck the card details" in reply


def test_missing_details_and_unknown_errors_become_customer_text():
    reply = build_reply("hi", "", [], ["check_balance: Account not specified.", "request_statement: MCP tool execution failed."])
    assert "We were unable to complete your request." in reply
    assert "Please reply with your account number." in reply
    assert "MCP" not in reply  # internal wording never reaches the customer
    assert "Dear Customer," in reply and "Subject: Your banking request" in reply


def test_tone_lines_make_no_promises():
    assert "We are sorry for the inconvenience" in build_reply("x", "A", [BALANCE], [], "negative")
    assert "We understand that this is urgent." in build_reply("x", "A", [BALANCE], [], "urgent")
    assert "urgent" not in build_reply("x", "A", [BALANCE], [], "neutral")


def test_intent_guard_drops_intents_the_email_never_asked_for(monkeypatch):
    llm = {"intents": ["check_balance", "get_transactions", "request_statement"], "sentiment": "neutral"}
    monkeypatch.setattr(agent, "generate_response", lambda prompt, **kw: json.dumps(llm))

    only_balance = agent.identify_intent("Subject: Balance Enquiry\n\nPlease share my balance for account 1234567890.")
    assert only_balance["intents"] == ["check_balance"]

    everything = agent.identify_intent("My balance, my last card purchases and a statement for April please")
    assert everything["intents"] == ["check_balance", "get_transactions", "request_statement"]
