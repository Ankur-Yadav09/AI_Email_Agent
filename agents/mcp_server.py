import sys
from datetime import date
from pathlib import Path
from uuid import uuid4

# Find the project root directory (AI_Email_Agent)
PROJECT_ROOT = Path(__file__).resolve().parent.parent

# Add the project root to Python's module search path
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from mcp.server import MCPServer

from utils.database import get_connection

# Create the MCP server
mcp = MCPServer("Banking Tools")

# --------------------------------------------------
# TOOL 1: Get account balance
# --------------------------------------------------
@mcp.tool() # Register the account balance function as an MCP tool
def get_account_balance(
    account_reference: str,
    customer_id: str,
) -> dict:
    """Get the balance of an account owned by the specified customer."""

    # Connect to the SQLite database
    with get_connection() as connection:
        account = connection.execute(
            """
            SELECT account_number, customer_id, balance, currency
            FROM accounts
            WHERE account_number = ?
            """,
            (account_reference,),
        ).fetchone()

    # Check whether the account exists
    if account is None:
        return {"error": "Account not found"}

    # Verify customer ownership
    if account["customer_id"] != customer_id:
        return {"error": "You are not authorized to access this account"}

    # Return account details
    return {
        "account_reference": account["account_number"],
        "balance": account["balance"],
        "currency": account["currency"],
    }

# --------------------------------------------------
# TOOL 2: Get card transactions
# --------------------------------------------------
@mcp.tool() # Register the card transactions function as an MCP tool
def get_card_transactions(
    card_reference: str,
    customer_id: str,
    limit: int = 10,
) -> dict:
    """Get recent transactions for a card owned by the specified customer."""

    # Only allow a reasonable number of transactions per request.
    if limit < 1 or limit > 100:
        return {"error": "Limit must be between 1 and 100"}

    with get_connection() as connection:
        # First verify that the card belongs to this customer.
        card = connection.execute(
            """
            SELECT card_id, masked_card_number
            FROM cards
            WHERE masked_card_number = ?
              AND customer_id = ?
            """,
            (card_reference, customer_id),
        ).fetchone()

        if card is None:
            return {
                "error": "Card not found or you are not authorized to access it"
            }

        # Fetch transactions only after checking card ownership.
        transactions = connection.execute(
            """
            SELECT
                transaction_id,
                transaction_date,
                description,
                amount,
                transaction_type
            FROM transactions
            WHERE card_id = ?
            ORDER BY transaction_date DESC, transaction_id DESC
            LIMIT ?
            """,
            (card["card_id"], limit),
        ).fetchall()

    return {
        "card_reference": card["masked_card_number"],
        "transactions": [
            {
                "transaction_id": transaction["transaction_id"],
                "date": transaction["transaction_date"],
                "description": transaction["description"],
                "amount": transaction["amount"],
                "transaction_type": transaction["transaction_type"],
            }
            for transaction in transactions
        ],
    }


# --------------------------------------------------
# TOOL 3: Request an account statement
# --------------------------------------------------
@mcp.tool() # Register the account statement request function as an MCP tool
def request_account_statement(
    account_reference: str,
    customer_id: str,
    start_date: str,
    end_date: str,
) -> dict:
    """Submit a statement request for an account owned by the customer.

    Dates must use YYYY-MM-DD format.
    """

    # Validate date format and make sure the range is valid.
    try:
        parsed_start = date.fromisoformat(start_date)
        parsed_end = date.fromisoformat(end_date)
    except ValueError:
        return {
            "error": "Invalid date. Use YYYY-MM-DD format."
        }

    if parsed_start > parsed_end:
        return {
            "error": "Start date cannot be after end date."
        }

    with get_connection() as connection:
        # Check that the account exists and belongs to this customer.
        account = connection.execute(
            """
            SELECT account_id, account_number, customer_id
            FROM accounts
            WHERE account_number = ?
            """,
            (account_reference,),
        ).fetchone()

        if account is None:
            return {"error": "Account not found"}

        if account["customer_id"] != customer_id:
            return {
                "error": "You are not authorized to access this account"
            }

        # Generate a unique reference for this statement request.
        statement_reference = f"STMT-{uuid4().hex[:12].upper()}"

        # Store the request in the database.
        connection.execute(
            """
            INSERT INTO statement_requests (
                statement_reference,
                account_id,
                start_date,
                end_date,
                status
            )
            VALUES (?, ?, ?, ?, ?)
            """,
            (
                statement_reference,
                account["account_id"],
                parsed_start.isoformat(),
                parsed_end.isoformat(),
                "submitted",
            ),
        )

    return {
        "message": "Statement request submitted successfully",
        "statement_reference": statement_reference,
        "account_reference": account_reference,
        "start_date": parsed_start.isoformat(),
        "end_date": parsed_end.isoformat(),
        "status": "submitted",
    }


# Start the MCP server using the default stdio transport when this file is run directly.
if __name__ == "__main__":
    mcp.run()

