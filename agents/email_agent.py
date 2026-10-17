

import asyncio
import json
import re
import sys
from pathlib import Path

# Find the root directory of the project.
# email_agent.py is inside agents/, so parent.parent is the project root.
PROJECT_ROOT = Path(__file__).resolve().parent.parent

# Add the project root to Python's import path.
# This allows Python to find folders such as utils and agents.
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

# Import MCP client components.
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

# Import our existing LLM function.
from utils.llm_client import generate_response

# Configure how the MCP client starts the banking MCP server.
server_params = StdioServerParameters(
    command=sys.executable,
    args=[str(PROJECT_ROOT / "agents" / "mcp_server.py")],
)


def identify_intent(email_text: str) -> dict:
    """
    Classify a customer email into one or more supported intents.
    """

    prompt = f"""
Classify the customer email into one or more intents.

Allowed intents:
- check_balance
- get_transactions
- request_statement
- unsupported

Return exactly one JSON object, with no examples or explanations.

Required format:
{{"intents": ["check_balance"]}}

Customer email:
<email>
{email_text}
</email>

Return the JSON object now.
"""

    response = generate_response(prompt)

    print("\nDEBUG - Raw intent response:")
    print(repr(response))

    # Find the first JSON object in the response.
    start = response.find("{")
    end = response.find("}", start)

    if start == -1 or end == -1:
        return {"intents": ["unsupported"]}

    json_text = response[start:end + 1]

    try:
        classification = json.loads(json_text)
    except json.JSONDecodeError:
        print("DEBUG - Could not parse JSON:", repr(json_text))
        return {"intents": ["unsupported"]}

    allowed_intents = {
        "check_balance",
        "get_transactions",
        "request_statement",
        "unsupported",
    }

    intents = classification.get("intents")

    if not isinstance(intents, list) or not intents:
        return {"intents": ["unsupported"]}

    # Reject malformed or unexpected intent values.
    if any(
        not isinstance(intent, str) or intent not in allowed_intents
        for intent in intents
    ):
        return {"intents": ["unsupported"]}

    # If unsupported appears alongside valid intents, reject the result.
    if "unsupported" in intents and len(intents) > 1:
        return {"intents": ["unsupported"]}

    # Remove duplicate intents.
    intents = list(dict.fromkeys(intents))

    print("\nDEBUG - Parsed intents:")
    print(repr(intents))

    return {"intents": intents}


async def process_email(email_text: str) -> str:
    """Process supported banking requests and generate an email draft."""

    # Step 1: Classify the email.
    classification = identify_intent(email_text)
    intents = classification.get("intents", ["unsupported"])

    print("\nDEBUG - Parsed intents:")
    print(repr(intents))

    if not intents or intents == ["unsupported"]:
        return "This request is not supported by the current version of the email agent."

    # DEMO ONLY: In production, get this from verified authentication.
    customer_id = "CUST001"

    # These are the demo customer's seeded references.
    account_reference = "1234567890"
    card_reference = "4567XXXX8901"

    results = []

    # Step 2: Connect to the MCP server.
    async with stdio_client(server_params) as (read, write):  # noqa: SIM117
        async with ClientSession(read, write) as session:
            await session.initialize()

            # Discover tools available from the MCP server.
            tools_response = await session.list_tools()
            available_tools = {tool.name for tool in tools_response.tools}

            # Step 3: Process every requested intent.
            for intent in intents:

                if intent == "check_balance":
                    if "get_account_balance" not in available_tools:
                        results.append({
                            "intent": intent,
                            "error": "Balance tool is unavailable."
                        })
                        continue

                    tool_result = await session.call_tool(
                        "get_account_balance",
                        arguments={
                            "account_reference": account_reference,
                            "customer_id": customer_id,
                        },
                    )

                elif intent == "get_transactions":
                    if "get_card_transactions" not in available_tools:
                        results.append({
                            "intent": intent,
                            "error": "Transaction tool is unavailable."
                        })
                        continue

                    tool_result = await session.call_tool(
                        "get_card_transactions",
                        arguments={
                            "card_reference": card_reference,
                            "customer_id": customer_id,
                            "limit": 10,
                        },
                    )

                elif intent == "request_statement":
                    if "request_account_statement" not in available_tools:
                        results.append({
                            "intent": intent,
                            "error": "Statement tool is unavailable."
                        })
                        continue

                    # Temporary demo dates. Next, we will extract
                    # the actual dates requested in the email.
                    tool_result = await session.call_tool(
                        "request_account_statement",
                        arguments={
                            "account_reference": account_reference,
                            "customer_id": customer_id,
                            "start_date": "2026-10-01",
                            "end_date": "2026-10-17",
                        },
                    )

                else:
                    results.append({
                        "intent": intent,
                        "error": "Unsupported intent."
                    })
                    continue

                # Convert the MCP result into a Python dictionary.
                result_text = "\n".join(
                    item.text # type: ignore
                    for item in tool_result.content
                    if hasattr(item, "text")
                )

                if tool_result.is_error:
                    results.append({
                        "intent": intent,
                        "error": "Banking tool execution failed."
                    })
                    continue

                try:
                    result_data = json.loads(result_text)
                except json.JSONDecodeError:
                    results.append({
                        "intent": intent,
                        "error": "Banking tool returned invalid data."
                    })
                    continue

                if "error" in result_data:
                    results.append({
                        "intent": intent,
                        "error": "The requested banking information could not be retrieved."
                    })
                else:
                    results.append({
                        "intent": intent,
                        "data": result_data
                    })

    # Step 4: Ask the LLM to draft an email using verified results.
    draft_prompt = f"""
    You are a professional customer support representative at an Indian bank.

    Write a concise, natural, professional email answering the customer's request.

    Rules:
    1. Include a clear subject line and use "Dear Customer," as the greeting.
    2. Convert verified banking data into readable sentences and bullet points.
    3. Format all monetary amounts in INR, using two decimal places.
    4. Format transaction dates in a readable form.
    5. For transactions, show description, amount, transaction type, and date.
    6. Do not print raw JSON or Python dictionary representations.
    7. Do not include placeholders such as [Customer Name] or [Your Name].
    8. Do not invent details or omit relevant retrieved information.
    9. If a tool failed, explain that the relevant information could not be retrieved.
    10. Never claim that the email has been sent.
    11. End with "Best regards,\nCustomer Support Team".

    Customer email:
    {email_text}

    Verified banking tool results:
    {json.dumps(results, ensure_ascii=False)}

    Return only the final email draft.
    """

    return generate_response(draft_prompt)






# Run a sample email when this file is executed directly.
if __name__ == "__main__":

    # Example customer email for testing.
    sample_email = ("Please tell me my balance and show my recent card transactions.")

    # Process the email and print the generated draft.
    result = asyncio.run(process_email(sample_email))

    print("\nGenerated Email Draft:")
    print(result)
