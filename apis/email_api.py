
import asyncio
import logging
import uuid

from fastapi import APIRouter, HTTPException, Query, Request, status
from langgraph.types import Command

from models.email_state import priority_for
from utils.database import get_connection
from utils import mailbox
from utils.email_reader import INBOX_DIR, parse_eml, process_attachments
from utils.logging_config import audit

logger = logging.getLogger(__name__)

from models.email_models import (
    EmailProcessRequest,
    EmailProcessResponse,
    EmailReviewRequest,
    EmailReviewResponse,
)
from utils.email_database import (
    create_email_record,
    email_id_for_message_id,
    list_email_records,
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
    return await run_email(
        http_request.app.state.email_workflow,
        request.email_text,
        request.sender_email,
    )


async def run_email(
    workflow,
    email_text: str,
    sender_email,
    attachments=(),
    subject: str = "",
    message_id: str | None = None,
):
    """Shared by POST /emails/process and the inbox poller."""

    # Resolve the sender to a registered customer.
    customer_id = resolve_customer_id(sender_email)

    if customer_id is None:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Sender identity could not be verified.",
        )

    email_id = str(uuid.uuid4())

    # Save the email before processing it.
    create_email_record(
        email_id,
        email_text,
        sender_email=str(sender_email).strip().lower(),
        subject=subject,
        message_id=message_id,
    )
    audit("email_received", email_id=email_id, customer_id=customer_id)

    # Only after the sender is verified: save attachments and read their text (off the event loop).
    attachment_text = await asyncio.to_thread(process_attachments, email_id, list(attachments))

    # Use the email ID as the thread ID so its workflow can be resumed later.
    config = {
        "configurable": {
            "thread_id": email_id,
        }
    }

    try:
        result = await workflow.ainvoke(
            {
                "email_text": email_text,
                "attachment_text": attachment_text,
                "sender_email": str(sender_email).strip().lower(),
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
            sentiment=result.get("sentiment", "neutral"),
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
        logger.exception("Email processing failed. email_id=%s", email_id)
        audit("email_failed", email_id=email_id)
        update_email_record(
            email_id,
            status="failed",
            errors=[str(exc)],
        )
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Email processing failed.",
        ) from exc
    
@router.post("/inbox/poll")
async def poll_inbox(http_request: Request):
    """Process every .eml file in the simulated mailbox (data/inbox).

    Handled files move to processed/, rejected ones to failed/, so a re-poll never repeats them.
    """
    workflow = http_request.app.state.email_workflow
    INBOX_DIR.mkdir(parents=True, exist_ok=True)
    results = []

    for path in sorted(INBOX_DIR.glob("*.eml")):
        try:
            parsed = parse_eml(path.read_bytes())
            response = await run_email(
                workflow, parsed.email_text, parsed.sender, parsed.attachments,
                subject=parsed.subject, message_id=parsed.message_id,
            )
            results.append({"file": path.name, "email_id": response.email_id, "status": response.status})
            destination = "processed"
        except HTTPException as exc:
            results.append({"file": path.name, "error": exc.detail})
            destination = "failed"
        except Exception:  # noqa: BLE001
            logger.exception("Inbox file failed: %s", path.name)
            results.append({"file": path.name, "error": "Processing failed."})
            destination = "failed"

        (INBOX_DIR / destination).mkdir(exist_ok=True)
        path.replace(INBOX_DIR / destination / path.name)

    return {"count": len(results), "results": results}


_mailbox_lock = asyncio.Lock()  # a manual poll and the background poller must not overlap


async def _handle_mailbox_message(workflow, raw: bytes) -> dict:
    """Process one message from the real mailbox. Never raises."""
    parsed = parse_eml(raw)
    who = {"subject": parsed.subject[:80]}

    if parsed.automated or parsed.sender == mailbox.address():
        audit("mail_skipped", reason="automated or own message", message_id=parsed.message_id)
        return {**who, "skipped": "automated message"}

    if mailbox.require_sender_auth() and not parsed.authenticated:
        audit("mail_rejected", reason="sender not authenticated (DKIM/SPF)", message_id=parsed.message_id)
        return {**who, "skipped": "sender could not be authenticated"}

    if parsed.message_id and email_id_for_message_id(parsed.message_id):
        return {**who, "skipped": "already processed"}

    try:
        response = await run_email(
            workflow, parsed.email_text, parsed.sender, parsed.attachments,
            subject=parsed.subject, message_id=parsed.message_id,
        )
    except HTTPException as exc:
        return {**who, "error": exc.detail}
    except Exception:  # noqa: BLE001
        logger.exception("Mailbox message failed")
        return {**who, "error": "Processing failed."}
    return {**who, "email_id": response.email_id, "status": response.status}


async def poll_mailbox(workflow) -> list[dict]:
    """Fetch unread Gmail, process each message, then mark them read.

    Messages are marked read even if processing failed, so one bad message is not retried forever;
    the failure is in the audit log and email.db.
    """
    if not mailbox.configured():
        raise HTTPException(status_code=503, detail="Mailbox is not configured (set MAIL_ADDRESS and MAIL_APP_PASSWORD).")

    async with _mailbox_lock:
        try:
            messages = await asyncio.to_thread(mailbox.fetch_unseen)
        except Exception as exc:
            logger.exception("Mailbox fetch failed")
            raise HTTPException(status_code=502, detail="Could not read the mailbox. Check credentials and logs.") from exc

        results = [await _handle_mailbox_message(workflow, raw) for _, raw in messages]
        await asyncio.to_thread(mailbox.mark_seen, [uid for uid, _ in messages])
        return results


@router.post("/mailbox/poll")
async def poll_mailbox_endpoint(http_request: Request):
    """Read unread mail from the configured Gmail account and process it."""
    results = await poll_mailbox(http_request.app.state.email_workflow)
    body = {"count": len(results), "results": results, "searched": " ".join(mailbox.unseen_criteria())}

    # An empty poll is usually "sent from a different address" or "older than the age limit": say so.
    if not results:
        try:
            other = await asyncio.to_thread(mailbox.count_unseen, False)
        except Exception:  # noqa: BLE001
            other = 0
        body["note"] = (
            f"No unread mail matched the search. {other} unread message(s) in the period came from other "
            "senders and were ignored (MAIL_ONLY_FROM). Send from a registered customer address."
            if other
            else "No unread mail matched the search (unread, within MAIL_MAX_AGE_DAYS)."
        )
    return body


@router.get("")
async def list_emails(
    status_filter: str | None = Query(default=None, alias="status", description="e.g. pending_review"),
    limit: int = Query(default=50, ge=1, le=200),
):
    """Work queue: recent emails, optionally one status. High-priority first, then oldest first."""
    emails = list_email_records(status_filter, limit)
    for record in emails:
        record["priority"] = priority_for(record.pop("sentiment"))
    emails.sort(key=lambda e: (e["priority"] != "high", e["created_at"]))
    return {"count": len(emails), "emails": emails}


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
        "sentiment": record["sentiment"],
        "priority": priority_for(record["sentiment"]),
        "draft": record["draft"],
        "errors": record["errors"],
        "approval_status": record["approval_status"],
        "reviewer_feedback": record["reviewer_feedback"],
        "delivery_status": record["delivery_status"],
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

    # 8. A human approved the draft: send it to the customer (never sent automatically).
    delivery_status = "not_sent"
    if request.approved:
        delivery_status = await _deliver(email_id, record, result.get("draft", record["draft"]))

    return EmailReviewResponse(
        email_id=email_id,
        status=final_status,
        delivery_status=delivery_status,
        message=(
            "Draft rejected."
            if not request.approved
            else "Draft approved and reply sent." if delivery_status == "sent"
            else "Draft approved, but the reply could not be sent. Check the logs."
            if delivery_status == "send_failed"
            else "Draft approved."
        ),
    )


async def _deliver(email_id: str, record: dict, draft: str) -> str:
    """Send the approved draft to the original sender. A send failure never undoes the approval."""
    recipient = record.get("sender_email")

    if not recipient:
        status_ = "no_recipient"
    elif not mailbox.configured():
        status_ = "not_configured"
    else:
        subject, body = mailbox.split_subject(draft, fallback=mailbox.reply_subject(record["subject"]))
        try:
            await asyncio.to_thread(mailbox.send_reply, recipient, subject, body, record.get("message_id"))
            status_ = "sent"
        except Exception:  # noqa: BLE001
            logger.exception("Sending reply failed. email_id=%s", email_id)
            status_ = "send_failed"

    update_email_record(email_id, delivery_status=status_)
    audit("reply_" + status_, email_id=email_id, recipient_domain=(recipient or "").split("@")[-1])
    return status_
