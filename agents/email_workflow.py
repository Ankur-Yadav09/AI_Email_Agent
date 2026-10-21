import asyncio
import json
import logging
import os
import pathlib
import time
import sys

# Get the root directory of the AI_Email_Agent project.
PROJECT_ROOT = pathlib.Path(__file__).resolve().parent.parent

# Add the project root to Python's import path.
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))


from langchain_core.runnables import RunnableConfig
from langgraph.graph import END, START, StateGraph
from langgraph.types import interrupt
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

# Reuse the classifier you have already tested.
from agents.email_agent import URGENT_WORDS, identify_intent
from models.email_state import EmailState, priority_for

# Import our existing LLM function.
from utils.logging_config import audit
from utils.reply_builder import build_handoff_reply, build_reply

logger = logging.getLogger(__name__)

# A hung banking service must not hang the whole request.
TOOL_TIMEOUT_SECONDS = 15
# Read-only tools are retried once on timeout. A statement request writes a row,
# so retrying it could create a duplicate.
RETRY_ON_TIMEOUT = 1
NEVER_RETRY = {"request_account_statement"}


class ToolTimeout(ValueError):
    """Raised when a banking tool does not answer in time. The message is customer-safe."""


def _thread_id(config) -> str | None:
    """The email_id, used to correlate every audit line of one email."""
    return ((config or {}).get("configurable") or {}).get("thread_id")

# Launch the existing MCP server as a separate process.
server_params = StdioServerParameters(
    command=sys.executable,
    args=[str(PROJECT_ROOT / "agents" / "mcp_server.py")],
    # The server process only gets a minimal environment, so pass the data location (and nothing secret).
    env={"DATA_DIR": os.environ["DATA_DIR"]} if os.environ.get("DATA_DIR") else None,
)



# --------------------------------------------------
# 1. Node: Receive and validate the email
# --------------------------------------------------

def receive_email(state: EmailState) -> dict:
    """Check that an email was provided."""

    email_text = state.email_text.strip()

    if not email_text:
        return {
            "errors": ["Email text cannot be empty."],
            "status": "invalid_input",
        }

    return {
        "email_text": email_text,
        "tool_results": [],
        "errors": [],
        "status": "received",
    }


# --------------------------------------------------
# 3. Node: Classify the customer's request
# --------------------------------------------------

def classify_email(state: EmailState, config: RunnableConfig = None):  # type: ignore[assignment]
    result = identify_intent(state.email_text, state.attachment_text) # type: ignore

    # Fraud, a lost card or a dispute is urgent however politely it is written, and even if the model
    # failed. It must never be answered with account data: a person takes it over (hand-off draft).
    if URGENT_WORDS.search(state.email_text):
        result["sentiment"] = "urgent"
        if result.get("intents"):
            audit("handed_to_person", email_id=_thread_id(config), reason="fraud/lost card/dispute wording",
                  dropped_intents=result["intents"])
            result["intents"] = []

    audit(
        "classified",
        email_id=_thread_id(config),
        intents=result.get("intents", []),
        sentiment=result.get("sentiment"),
        entities=result.get("entities"),
        error=result.get("error"),
    )

    return {
        "intents": result.get("intents", []),
        "sentiment": result.get("sentiment", "neutral"),
        "entities": result.get("entities", {}),
        "errors": (
            [result["error"]] if result.get("error") else []
        ),
        "status": "classified",
    }


# --------------------------------------------------
# 4. Route based on the detected intents
# --------------------------------------------------

def route_request(state: EmailState) -> str:
    intents = state.intents

    allowed_intents = {
        "check_balance",
        "get_transactions",
        "request_statement",
    }

    # No recognized banking request: do not call banking tools.
    if not intents:
        return "unsupported"

    # Reject unexpected intents.
    if any(intent not in allowed_intents for intent in intents):
        return "unsupported"

    return "supported"


# --------------------------------------------------
# 5. Node: Handle unsupported requests
# --------------------------------------------------
def handle_unsupported(state: EmailState):
    """A request the system cannot complete. The hand-off draft still goes to a person for review."""
    return {
        "draft": build_handoff_reply(state.email_text, state.customer_name, state.sentiment),
        "status": "unsupported",
    }



# --------------------------------------------------
# 6. Temporary node for supported requests
# --------------------------------------------------


async def supported_requests(state: EmailState, config: RunnableConfig = None) -> dict:  # type: ignore[assignment]
    """Execute supported requests using customer-specific MCP tools."""

    customer_id = state.verified_customer_id

    if not customer_id:
        return {
            "tool_results": [],
            "errors": ["Customer identity could not be verified."],
            "status": "identity_verification_failed",
        }

    intents = state.intents
    entities = state.entities
    tool_results = []
    errors = []
    customer_name = ""

    async with stdio_client(server_params) as (read, write):  # noqa: SIM117
        async with ClientSession(read, write) as session:
            await session.initialize()

            response = await session.list_tools()
            available_tools = {tool.name for tool in response.tools}

            async def call_banking_tool(tool_name: str, arguments: dict):
                """Call an MCP tool and return its JSON result."""
                if tool_name not in available_tools:
                    raise ValueError(
                        f"Required MCP tool '{tool_name}' is unavailable."
                    )

                attempts = 1 if tool_name in NEVER_RETRY else 1 + RETRY_ON_TIMEOUT
                for attempt in range(1, attempts + 1):
                    started = time.perf_counter()
                    try:
                        result = await asyncio.wait_for(
                            session.call_tool(tool_name, arguments=arguments),
                            timeout=TOOL_TIMEOUT_SECONDS,
                        )
                        break
                    except asyncio.TimeoutError:
                        audit(
                            "tool_timeout",
                            email_id=_thread_id(config),
                            tool=tool_name,
                            attempt=attempt,
                        )
                        if attempt == attempts:
                            raise ToolTimeout(
                                "The banking service did not respond in time. "
                                "Please try again later."
                            ) from None
                audit(
                    "tool_call",
                    email_id=_thread_id(config),
                    tool=tool_name,
                    arguments=arguments,
                    is_error=result.is_error,
                    ms=round((time.perf_counter() - started) * 1000),
                )

                if result.is_error:
                    raise ValueError("MCP tool execution failed.")

                result_text = "\n".join(
                    item.text # type: ignore
                    for item in result.content
                    if hasattr(item, "text")
                )

                data = json.loads(result_text)

                if not isinstance(data, dict) or "error" in data:
                    raise ValueError("Banking tool returned an error.")

                return data

            async def require_valid(tool_name: str, arguments: dict, label: str):
                """Stop this intent with a customer-facing message if the number is invalid."""
                nonlocal customer_name
                check = await call_banking_tool(tool_name, arguments)
                customer_name = check.get("customer_name") or customer_name
                if check.get("status") != "VALID":
                    raise ValueError(
                        f"The {label} provided could not be validated. "
                        f"Please recheck the {label.split()[0]} details and resend your request."
                    )

            # Discover resources belonging to this customer.
            try:
                accounts_data = await call_banking_tool(
                    "get_customer_accounts",
                    {"customer_id": customer_id},
                )
                cards_data = await call_banking_tool(
                    "get_customer_cards",
                    {"customer_id": customer_id},
                )
            except Exception:  # noqa: BLE001
                return {
                    "tool_results": [],
                    "errors": [
                        "Unable to retrieve the customer's account resources."
                    ],
                    "status": "failed",
                }

            accounts = accounts_data.get("accounts", [])
            cards = cards_data.get("cards", [])

            if not isinstance(accounts, list):
                accounts = []

            if not isinstance(cards, list):
                cards = []

            async def run_intent(intent: str):
                """Validate, then fetch, for one intent. Returns (intent, data, error)."""
                try:
                    if intent == "check_balance":
                        account_ref = entities.get("account_number") or (
                            accounts[0]["account_reference"]
                            if len(accounts) == 1 else None
                        )
                        if not account_ref:
                            raise ValueError("Account not specified.")

                        await require_valid(
                            "validate_account",
                            {"account_number": account_ref, "customer_id": customer_id},
                            "account number",
                        )

                        result_data = await call_banking_tool(
                            "get_account_balance",
                            {
                                "account_reference": account_ref,
                                "customer_id": customer_id,
                            },
                        )

                    elif intent == "get_transactions":
                        card_ref = entities.get("card_number") or (
                            cards[0]["card_reference"]
                            if len(cards) == 1 else None
                        )
                        if not card_ref:
                            raise ValueError("Card not specified.")

                        await require_valid(
                            "validate_card",
                            {"card_number": card_ref, "customer_id": customer_id},
                            "card number",
                        )

                        result_data = await call_banking_tool(
                            "get_card_transactions",
                            {
                                "card_reference": card_ref,
                                "customer_id": customer_id,
                                "limit": entities.get("transaction_count") or 5,
                            },
                        )

                    elif intent == "request_statement":
                        account_ref = entities.get("account_number") or (
                            accounts[0]["account_reference"]
                            if len(accounts) == 1 else None
                        )
                        if not account_ref:
                            raise ValueError("Account not specified.")

                        await require_valid(
                            "validate_account",
                            {"account_number": account_ref, "customer_id": customer_id},
                            "account number",
                        )

                        if not (entities.get("start_date") and entities.get("end_date")):
                            raise ValueError("Statement period not specified.")

                        result_data = await call_banking_tool(
                            "request_account_statement",
                            {
                                "account_reference": account_ref,
                                "customer_id": customer_id,
                                "start_date": entities["start_date"],
                                "end_date": entities["end_date"],
                            },
                        )

                    else:
                        return intent, None, f"Unsupported intent: {intent}"

                    return intent, result_data, None

                except Exception as exc:  # noqa: BLE001
                    # ValueError messages raised above are ours and safe to show.
                    reason = (
                        str(exc) if isinstance(exc, ValueError)
                        else "requested banking data could not be retrieved."
                    )
                    return intent, None, f"{intent}: {reason}"

            # Intents are independent, so run them concurrently (order is preserved).
            for intent, data, error in await asyncio.gather(
                *(run_intent(i) for i in intents)
            ):
                if error:
                    errors.append(error)
                else:
                    tool_results.append({"intent": intent, "data": data})

    audit(
        "requests_finished",
        email_id=_thread_id(config),
        succeeded=[r["intent"] for r in tool_results],
        errors=errors,
    )

    if errors and tool_results:
        status = "partial_success"
    elif errors:
        status = "failed"
    else:
        status = "banking_data_retrieved"

    return {
        "tool_results": tool_results,
        "errors": errors,
        "customer_name": customer_name,
        "status": status,
    }

# --------------------------------------------------
# 7. generate node for the draft email (fixed template, no LLM)
# --------------------------------------------------
def generate_draft(state: EmailState, config: RunnableConfig = None) -> dict:  # type: ignore[assignment]
    """Build the reply from the verified tool results.

    A template, not the LLM, writes the reply, so names, amounts and numbers cannot be
    invented or garbled (the 1B model once greeted a customer with their account number).
    """

    if not state.intents:
        return handle_unsupported(state)

    try:
        draft = build_reply(
            state.email_text,
            state.customer_name,
            state.tool_results,
            state.errors,
            state.sentiment,
        )
    except Exception:  # noqa: BLE001
        logger.exception("Draft generation failed")
        audit("draft_failed", email_id=_thread_id(config))
        return {
            "draft": build_handoff_reply(state.email_text, state.customer_name, state.sentiment),
            "status": "draft_generation_failed",
            "errors": state.errors + ["Email draft generation failed."],
        }

    audit(
        "draft_generated",
        email_id=_thread_id(config),
        sentiment=state.sentiment,
        priority=priority_for(state.sentiment),
    )
    return {"draft": draft, "status": "draft_generated"}



def human_review(state: EmailState, config: RunnableConfig = None) -> dict:  # type: ignore[assignment]
    """Pause execution until a human reviews the generated draft."""

    # Pause the workflow and expose the draft to the reviewer.
    decision = interrupt({
        "type": "email_draft_review",
        "original_email": state.email_text,
        "sentiment": state.sentiment,
        "priority": priority_for(state.sentiment),
        "draft": state.draft,
        "message": (
            "Review the draft. Approve it or reject it with feedback."
        ),
    })

    # The workflow resumes here after receiving a reviewer decision.
    audit(
        "human_review",
        email_id=_thread_id(config),
        approved=decision.get("approved") if isinstance(decision, dict) else None,
        edited=bool(isinstance(decision, dict) and decision.get("edited_draft")),
    )
    if not isinstance(decision, dict):
        return {
            "approval_status": "rejected",
            "reviewer_feedback": "Invalid review decision.",
            "status": "rejected",
        }

    if decision.get("approved") is True:
        edited_draft = decision.get("edited_draft")

        # Allow the reviewer to edit the draft before approving.
        final_draft = (
            edited_draft.strip()
            if isinstance(edited_draft, str) and edited_draft.strip()
            else state.draft
        )

        return {
            "draft": final_draft,
            "approval_status": "approved",
            "reviewer_feedback": decision.get("feedback", ""),
            "status": "approved",
        }

    return {
        "approval_status": "rejected",
        "reviewer_feedback": decision.get(
            "feedback", "The reviewer rejected the draft."
        ),
        "status": "rejected",
    }



# --------------------------------------------------
# 8. Build and compile the LangGraph workflow
# --------------------------------------------------

builder = StateGraph(EmailState)

builder.add_node("receive_email", receive_email)
builder.add_node("classify_email", classify_email)
builder.add_node("handle_unsupported", handle_unsupported)
builder.add_node("supported_requests", supported_requests)
builder.add_node("generate_draft", generate_draft)
builder.add_node("human_review", human_review)

builder.add_edge(START, "receive_email")
builder.add_edge("receive_email", "classify_email")

builder.add_conditional_edges(
    "classify_email",
    route_request,
    {
        "supported": "supported_requests",
        "unsupported": "handle_unsupported",
    },
)

# Retrieve banking data, then generate the response draft.
builder.add_edge("supported_requests", "generate_draft")
builder.add_edge("generate_draft", "human_review")
builder.add_edge("human_review", END)

# Unsupported requests get a safe hand-off draft without banking tool calls, and a person reviews it
# like any other reply (it would otherwise never be answered).
builder.add_edge("handle_unsupported", "human_review")

# --------------------------------------------------
# 9. Create a workflow using the supplied checkpointer
# --------------------------------------------------
def build_email_workflow(checkpointer):
    """
    Compile the existing email graph with the supplied checkpointer.

    FastAPI will supply a persistent SQLite checkpointer at startup.
    """
    return builder.compile(checkpointer=checkpointer) # type: ignore

