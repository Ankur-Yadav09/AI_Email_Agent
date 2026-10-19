import asyncio
import json
import pathlib
import sys

# Get the root directory of the AI_Email_Agent project.
PROJECT_ROOT = pathlib.Path(__file__).resolve().parent.parent

# Add the project root to Python's import path.
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))


from langgraph.graph import END, START, StateGraph
from langgraph.types import interrupt
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

# Reuse the classifier you have already tested.
from agents.email_agent import identify_intent
from models.email_state import EmailState

# Import our existing LLM function.
from utils.llm_client import generate_response

# Launch the existing MCP server as a separate process.
server_params = StdioServerParameters(
    command=sys.executable,
    args=[str(PROJECT_ROOT / "agents" / "mcp_server.py")],
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

def classify_email(state: EmailState):
    print("\nDEBUG - Original email received:")
    print(repr(state.email_text)) # type: ignore

    result = identify_intent(state.email_text) # type: ignore

    print("\nDEBUG - Classifier result:")
    print(result)

    return {
        "intents": result.get("intents", []),
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

def handle_unsupported(state: EmailState):
    return {
        "draft": (
            "Dear Customer,\n\n"
            "Thank you for contacting us. Your request is not "
            "supported by our current automated service. "
            "Please contact customer support for assistance.\n\n"
            "Best regards,\nCustomer Support Team"
        ),
        "status": "unsupported",
    }
# --------------------------------------------------
# 5. Node: Handle unsupported requests
# --------------------------------------------------




# --------------------------------------------------
# 6. Temporary node for supported requests
# --------------------------------------------------


async def supported_requests(state: EmailState) -> dict:
    """Execute supported requests using customer-specific MCP tools."""

    customer_id = state.verified_customer_id

    if not customer_id:
        return {
            "tool_results": [],
            "errors": ["Customer identity could not be verified."],
            "status": "identity_verification_failed",
        }

    intents = state.intents
    tool_results = []
    errors = []

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

                result = await session.call_tool(
                    tool_name,
                    arguments=arguments,
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

            for intent in intents:
                try:
                    if intent == "check_balance":
                        if len(accounts) == 0:
                            raise ValueError("No active account is available.")

                        if len(accounts) > 1:
                            raise ValueError(
                                "Multiple accounts found. Customer must specify an account."
                            )

                        result_data = await call_banking_tool(
                            "get_account_balance",
                            {
                                "account_reference":
                                    accounts[0]["account_reference"],
                                "customer_id": customer_id,
                            },
                        )

                    elif intent == "get_transactions":
                        if len(cards) == 0:
                            raise ValueError("No active card is available.")

                        if len(cards) > 1:
                            raise ValueError(
                                "Multiple cards found. Customer must specify a card."
                            )

                        result_data = await call_banking_tool(
                            "get_card_transactions",
                            {
                                "card_reference":
                                    cards[0]["card_reference"],
                                "customer_id": customer_id,
                                "limit": 10,
                            },
                        )

                    elif intent == "request_statement":
                        if len(accounts) == 0:
                            raise ValueError("No active account is available.")

                        if len(accounts) > 1:
                            raise ValueError(
                                "Multiple accounts found. Customer must specify an account."
                            )

                        result_data = await call_banking_tool(
                            "request_account_statement",
                            {
                                "account_reference":
                                    accounts[0]["account_reference"],
                                "customer_id": customer_id,
                                "start_date": "2026-10-01",
                                "end_date": "2026-10-17",
                            },
                        )

                    else:
                        errors.append(f"Unsupported intent: {intent}")
                        continue

                    tool_results.append({
                        "intent": intent,
                        "data": result_data,
                    })

                except Exception:  # noqa: BLE001
                    errors.append(
                        f"{intent}: requested banking data could not be retrieved."
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
        "status": status,
    }

# --------------------------------------------------
# 7. generate node for draft email using the LLM
# --------------------------------------------------
def generate_draft(state: EmailState) -> dict:
    """
    Generate a customer email draft using the LLM.

    The LLM uses the original email and the banking data
    returned by our MCP tools. It must not invent facts.
    """

    email_text = state.email_text
    intents = state.intents
    tool_results = state.tool_results
    errors = state.errors

    # Do not generate a banking response if classification failed
    # or the request is unsupported.
    if not intents:
        return {
            "draft": (
                "Dear Customer,\n\n"
                "Thank you for contacting us. Your request is not "
                "supported by our current automated service. "
                "Please contact customer support for assistance.\n\n"
                "Best regards,\nCustomer Support Team"
            ),
            "status": "unsupported",
        }

    # Provide the LLM with the original request and retrieved data.
    prompt = f"""
You are a professional banking customer-support email assistant.

CUSTOMER EMAIL:
{email_text}

CLASSIFIED INTENTS:
{json.dumps(intents)}

VERIFIED BANKING TOOL RESULTS:
{json.dumps(tool_results, indent=2)}

TOOL ERRORS:
{json.dumps(errors, indent=2)}

RULES:
- Answer only the customer's actual request.
- Use only facts present in the verified tool results.
- Never invent customer details, addresses, balances, transactions,
  dates, or processing times.
- Never claim an operation was completed unless an authorized tool
  actually performed it and confirmed success.
- If banking data is missing, explain that it could not be retrieved.
- If a tool failed, do not pretend that it succeeded.
- Do not reveal internal errors, credentials, or sensitive information.
- Write a clear, polite, professional email with a subject.
- Generate a draft only. Do not send the email.

Return only the email draft.
"""

    try:
        # Call the existing Ollama LLM helper.
        draft = generate_response(prompt)

        return {
            "draft": draft.strip(),
            "status": "draft_generated",
        }

    except Exception as error:

        return {
            "draft": (
                "Dear Customer,\n\n"
                "Thank you for contacting us. We are unable to "
                "prepare your response automatically at this time. "
                "Please contact customer support for assistance.\n\n"
                "Best regards,\nCustomer Support Team"
            ),
            "status": "draft_generation_failed",
            "errors": errors + ["Email draft generation failed."],
        }



def human_review(state: EmailState) -> dict:
    """Pause execution until a human reviews the generated draft."""

    # Pause the workflow and expose the draft to the reviewer.
    decision = interrupt({
        "type": "email_draft_review",
        "original_email": state.email_text,
        "draft": state.draft,
        "message": (
            "Review the draft. Approve it or reject it with feedback."
        ),
    })

    # The workflow resumes here after receiving a reviewer decision.
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

# Unsupported requests get a safe response without banking tool calls.
builder.add_edge("handle_unsupported", END)

# --------------------------------------------------
# 9. Create a workflow using the supplied checkpointer
# --------------------------------------------------
def build_email_workflow(checkpointer):
    """
    Compile the existing email graph with the supplied checkpointer.

    FastAPI will supply a persistent SQLite checkpointer at startup.
    """
    return builder.compile(checkpointer=checkpointer) # type: ignore

