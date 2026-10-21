"""Builds the customer reply from verified tool results with a fixed template.

Facts (names, amounts, numbers) never pass through an LLM, so they cannot be invented or garbled.
"""
import os
import re
from datetime import date

from utils.logging_config import mask_text

SECTION_TITLES = {
    "check_balance": "Account Balance",
    "get_transactions": "Credit Card Transactions",
    "request_statement": "Statement Request",
}

# One fixed line per sentiment. No promises of escalation, callbacks, compensation or timelines.
TONE_LINES = {
    "negative": "We are sorry for the inconvenience and understand your frustration.",
    "urgent": "We understand that this is urgent.",
    "positive": "Thank you for your kind words.",
}

# Internal reasons (see supported_requests) -> what the customer should be told.
CLARIFICATIONS = {
    "Account not specified.": "We could not tell which account you mean. Please reply with your account number.",
    "Card not specified.": "We could not tell which card you mean. Please reply with your card number.",
    "Statement period not specified.": "Please tell us the period you need, for example April 2026.",
}
DEFAULT_SUPPORT_TEXT = "Please contact customer support for assistance."
GENERIC_FAILURE ="We were unable to retrieve this information right now. Please try again later or contact customer support."


def inr(amount, currency: str = "INR") -> str:
    """25000 -> 'INR 25,000.00', 125000 -> 'INR 1,25,000.00' (Indian digit grouping)."""
    whole, _, fraction = f"{abs(float(amount)):.2f}".partition(".")
    if len(whole) > 3:
        head, tail, groups = whole[:-3], whole[-3:], []
        while len(head) > 2:
            groups.insert(0, head[-2:])
            head = head[:-2]
        whole = ",".join(([head] if head else []) + groups + [tail])
    return f"{currency} {whole}.{fraction}"


def _day(iso: str) -> str:
    try:
        return date.fromisoformat(iso).strftime("%d %b %Y")
    except ValueError:
        return iso


def _section(intent: str, data: dict) -> list[str]:
    currency = data.get("currency", "INR")
    if intent == "check_balance":
        return [
            f"  Account Number: {data['account_reference']}",
            f"  Available Balance: {inr(data['balance'], currency)}",
        ]
    if intent == "get_transactions":
        lines = [f"  Card Number: {data['card_reference']}"]
        lines += [
            f"  {_day(t['date'])}  {t['description']}  {inr(t['amount'], currency)} ({t['transaction_type']})"
            for t in data["transactions"]
        ] or ["  No recent transactions found."]
        return lines
    if intent == "request_statement":
        return [
            f"  Reference: {data['statement_reference']}",
            f"  Period: {_day(data['start_date'])} to {_day(data['end_date'])}",
            f"  Status: {data['status']}",
        ]
    return []


def _failure_text(reason: str) -> str:
    if reason in CLARIFICATIONS:
        return CLARIFICATIONS[reason]
    # Our own customer-facing messages ("The card number provided could not be validated...").
    return reason if reason.startswith("The ") else GENERIC_FAILURE


def original_subject(email_text: str) -> str:
    match = re.match(r"Subject:[ \t]*(.+)", email_text)
    return match.group(1).strip() if match else ""


def build_reply(
    email_text: str,
    customer_name: str,
    tool_results: list[dict],
    errors: list[str],
    sentiment: str = "neutral",
) -> str:
    subject = original_subject(email_text)
    lines = [
        f"Subject: {'Re: ' + subject if subject else 'Your banking request'}",
        "",
        f"Dear {customer_name or 'Customer'},",
        "",
    ]

    if sentiment in TONE_LINES:
        lines += [TONE_LINES[sentiment], ""]

    if tool_results and not errors:
        lines.append("We have processed your request successfully.")
    elif tool_results:
        lines.append("We have processed part of your request.")
    else:
        lines.append("We were unable to complete your request.")
    lines.append("")

    for result in tool_results:
        lines += [f"{SECTION_TITLES[result['intent']]}:", *_section(result["intent"], result["data"]), ""]

    for error in errors:
        intent, _, reason = error.partition(": ")
        if intent in SECTION_TITLES and reason:
            lines += [f"{SECTION_TITLES[intent]}:", f"  {_failure_text(reason)}", ""]
        else:
            lines += [GENERIC_FAILURE, ""]

    lines += ["Thank you,", "Customer Support Team"]
    return mask_text("\n".join(lines))


def support_text() -> str:
    """Tells a customer where to go when we cannot complete their request by email.

    Set SUPPORT_CONTACT_TEXT to the client's real channel, e.g. "Please log in to net banking or call 1800-XXX-XXXX."
    """
    return " ".join((os.environ.get("SUPPORT_CONTACT_TEXT") or DEFAULT_SUPPORT_TEXT).split())


def build_handoff_reply(email_text: str, customer_name: str, sentiment: str = "neutral") -> str:
    """Draft for a request the system cannot complete (out of scope, or the email could not be read).

    It only states what is true and points to the support channel. It promises nothing.
    """
    subject = original_subject(email_text)
    lines = [
        f"Subject: {'Re: ' + subject if subject else 'Your request'}",
        "",
        f"Dear {customer_name or 'Customer'},",
        "",
    ]
    if sentiment in TONE_LINES:
        lines += [TONE_LINES[sentiment], ""]
    lines += [
        "Thank you for contacting us. We are unable to complete this request automatically.",
        support_text(),
        "",
        "Thank you,",
        "Customer Support Team",
    ]
    return mask_text("\n".join(lines))
