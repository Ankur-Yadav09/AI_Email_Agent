
from pydantic import BaseModel, EmailStr, Field


class EmailProcessRequest(BaseModel):
    """Input received by the email processing API."""

    email_text: str = Field(
        min_length=1,
        max_length=20000,
        description="The original email received from the customer."
    )

    sender_email: EmailStr | None = Field(
        default=None,
        description="Sender email supplied by the email ingestion layer."
    )


class EmailProcessResponse(BaseModel):
    """Response returned when an email is submitted for processing."""

    email_id: str = Field(
        description="Unique identifier assigned to this email workflow."
    )

    status: str = Field(
        description="Current processing status of the email."
    )

    message: str = Field(
        description="Human-readable processing result."
    )


from pydantic import BaseModel, Field


class EmailReviewRequest(BaseModel):
    """Human review decision for a generated email draft."""

    approved: bool = Field(
        description="True to approve the draft, False to reject it."
    )

    feedback: str = Field(
        default="",
        max_length=2000,
        description="Optional review comments or rejection reason."
    )

    edited_draft: str | None = Field(
        default=None,
        max_length=20000,
        description="Optional revised draft supplied by the reviewer."
    )


class EmailReviewResponse(BaseModel):
    """Result of reviewing an email draft."""

    email_id: str
    status: str
    message: str
    delivery_status: str = "not_sent"
