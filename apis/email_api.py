
import logging
import uuid

from fastapi import APIRouter, HTTPException, Request, status
from httpcore2 import request
from langgraph.types import Command

from utils.database import get_connection

logger = logging.getLogger(__name__)

from models.email_models import (
    EmailProcessRequest,
    EmailProcessResponse,
    EmailReviewRequest,
    EmailReviewResponse,
)
from utils.email_database import (
    create_email_record,
    get_email_record,
    update_email_record,
)


def resolve_customer_id(sender_email: str | None) -> str | None:
    """Resolve a sender email using the registered customer records."""

    if not sender_email:
        return None

    with get_connection() as connection:
        customer = connection.execute(
            """
            SELECT customer_id
            FROM customers
            WHERE email = ?
            """,
            (sender_email.strip().lower(),),
        ).fetchone()

    if customer is None:
        return None

    return customer["customer_id"]

router = APIRouter(prefix="/emails", tags=["Email Agent"])

@router.post("/process", response_model=EmailProcessResponse)
async def process_email(
    request: EmailProcessRequest,
    http_request: Request,
):
    """
    Process a customer email through LangGraph.
    The workflow may pause for human review.
    No email is sent by this endpoint.
    """

    # Resolve the sender to a registered customer.
    customer_id = resolve_customer_id(request.sender_email)

    if customer_id is None:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Sender identity could not be verified.",
        )

    # Get the persistent workflow initialized by main.py.
    workflow = http_request.app.state.email_workflow

    email_id = str(uuid.uuid4())

    # Save the email before processing it.
    create_email_record(email_id, request.email_text)

    # Use the email ID as the thread ID so its workflow can be resumed later.
    config = {
        "configurable": {
            "thread_id": email_id,
        }
    }

    try:
        result = await workflow.ainvoke(
            {
                "email_text": request.email_text,
                "sender_email": str(request.sender_email).strip().lower(),
                "verified_customer_id": customer_id,
            },
            config=config,
        )

        # Check whether LangGraph paused for human review.
        is_pending_review = bool(result.get("__interrupt__"))

        if is_pending_review:
            final_status = "pending_review"
        else:
            final_status = result.get("status", "processed")

        update_email_record(
            email_id,
            status=final_status,
            intents=result.get("intents", []),
            tool_results=result.get("tool_results", []),
            draft=result.get("draft", ""),
            errors=result.get("errors", []),
            approval_status=result.get("approval_status", "pending"),
        )

        return EmailProcessResponse(
            email_id=email_id,
            status=final_status,
            message=(
                "Email processed and draft is waiting for human review."
                if is_pending_review
                else "Email processing completed."
            ),
        )

    except Exception as exc:
        update_email_record(
            email_id,
            status="failed",
            errors=[str(exc)],
        )
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Email processing failed.",
        ) from exc
    
@router.get("/{email_id}")
async def get_email(email_id: str):
    """Retrieve the current email workflow state."""

    record = get_email_record(email_id)

    if record is None:
        raise HTTPException(
            status_code=404,
            detail="Email not found",
        )

    return {
        "email_id": record["email_id"],
        "status": record["status"],
        "email_text": record["email_text"],
        "intents": record["intents"],
        "draft": record["draft"],
        "errors": record["errors"],
        "approval_status": record["approval_status"],
        "reviewer_feedback": record["reviewer_feedback"],
    }


@router.post(
    "/{email_id}/review",
    response_model=EmailReviewResponse,
)
async def review_email(
    email_id: str,
    request: EmailReviewRequest,
    http_request: Request,
):
    """Approve, edit, or reject a draft and resume LangGraph."""
    workflow = http_request.app.state.email_workflow
    
    # 1. Fetch the email record from SQLite.
    record = get_email_record(email_id)

    if record is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Email not found.",
        )

    # 2. Only allow reviews for emails awaiting human review.
    if record["status"] != "pending_review":
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Email is not awaiting review.",
        )

    # 3. An edited draft is only valid when approving.
    if not request.approved and request.edited_draft is not None:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="An edited draft can only be submitted with approval.",
        )

    # 4. Prepare the decision to resume the interrupted graph.
    decision = {
        "approved": request.approved,
        "feedback": request.feedback,
    }

    if request.edited_draft is not None:
        decision["edited_draft"] = request.edited_draft

    # 5. Resume LangGraph using the original thread ID.
    try:
        result = await workflow.ainvoke(
            Command(resume=decision),
            config={
                "configurable": {
                    "thread_id": email_id,
                }
            },
        )

    except Exception as exc:
        logger.exception(
            "Failed to resume email workflow. email_id=%s",
            email_id,
        )

        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Unable to process the review decision. Check server logs.",
        ) from exc

    
    # Use the status produced by the workflow when available.
    # Otherwise, derive it from the reviewer's decision.
    if request.approved:
        final_status = result.get("status", "approved")
    else:
        final_status = result.get("status", "rejected")

    # 7. Persist the latest workflow state in SQLite.
    try:
        update_email_record(
            email_id,
            status=final_status,
            intents=result.get("intents", record["intents"]),
            tool_results=result.get(
                "tool_results",
                record["tool_results"],
            ),
            draft=result.get("draft", record["draft"]),
            errors=result.get("errors", record["errors"]),
            approval_status=result.get(
                "approval_status",
                "approved" if request.approved else "rejected",
            ),
            reviewer_feedback=result.get(
                "reviewer_feedback",
                request.feedback,
            ),
        )

    except Exception as exc:
        logger.exception(
            "Workflow resumed but email record update failed. email_id=%s",
            email_id,
        )

        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=(
                "The review was processed, but its database update failed. "
                "Check server logs before retrying."
            ),
        ) from exc

    # 8. Return the review result.
    return EmailReviewResponse(
        email_id=email_id,
        status=final_status,
        message=(
            "Draft approved."
            if request.approved
            else "Draft rejected."
        ),
    )
