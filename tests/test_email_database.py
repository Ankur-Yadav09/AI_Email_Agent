
import uuid

from utils.email_database import (
    initialize_email_database,
    create_email_record,
    get_email_record,
    update_email_record,
)


def test_email_record_can_be_saved_and_retrieved():
    initialize_email_database()

    email_id = f"TEST-EMAIL-{uuid.uuid4()}"  # email.db persists between runs

    create_email_record(
        email_id=email_id,
        email_text="Please check my account balance.",
    )

    update_email_record(
        email_id,
        status="pending_review",
        intents=["check_balance"],
        draft="Your account balance is available for review.",
        errors=[],
    )

    record = get_email_record(email_id)

    assert record is not None
    assert record["email_text"] == "Please check my account balance."
    assert record["status"] == "pending_review"
    assert record["intents"] == ["check_balance"]
    assert record["draft"] != ""


def test_sentiment_is_stored_and_defaults_to_neutral():
    initialize_email_database()
    email_id = f"TEST-EMAIL-{uuid.uuid4()}"
    create_email_record(email_id=email_id, email_text="x")
    assert get_email_record(email_id)["sentiment"] == "neutral"

    update_email_record(email_id, sentiment="urgent")
    assert get_email_record(email_id)["sentiment"] == "urgent"
