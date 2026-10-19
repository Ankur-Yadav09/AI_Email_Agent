from pydantic import BaseModel, ConfigDict, Field

# Angry or urgent customers are flagged so a reviewer handles them first.
HIGH_PRIORITY_SENTIMENTS = {"negative", "urgent"}


def priority_for(sentiment: str) -> str:
    return "high" if sentiment in HIGH_PRIORITY_SENTIMENTS else "normal"


class EmailState(BaseModel):
    """Validated state for the email-processing workflow."""

    model_config = ConfigDict(extra="forbid")

    email_text: str = Field(
        default="",
        description="Original email received from the customer."
    )

    customer_name: str = Field(
        default="",
        description="Customer name returned by the validation tools, used in the greeting."
    )

    attachment_text: str = Field(
        default="",
        description="Text extracted from attachments; only used to find account/card numbers."
    )

    intents: list[str] = Field(
        default_factory=list,
        description="Banking intents identified in the email."
    )

    sentiment: str = Field(
        default="neutral",
        description="Customer sentiment: neutral, positive, negative or urgent."
    )

    entities: dict = Field(
        default_factory=dict,
        description="Extracted account/card numbers, dates, count and name."
    )

    tool_results: list[dict] = Field(
        default_factory=list,
        description="Results returned by authorized banking tools."
    )

    draft: str = Field(
        default="",
        description="Generated customer email response draft."
    )

    errors: list[str] = Field(
        default_factory=list,
        description="Errors encountered during workflow execution."
    )

    status: str = Field(
        default="received",
        description="Current workflow status."
    )

    approval_status: str = Field(
        default="pending",
        description="Review decision: pending, approved, or rejected."
    )

    reviewer_feedback: str = Field(
        default="",
        description="Reviewer's feedback or reason for rejection."
    )

    sender_email: str | None = Field(
        default=None,
        description="Sender email associated with the incoming message."
    )

    verified_customer_id: str | None = Field(
        default=None,
        description="Customer ID resolved by the backend identity lookup."
    )