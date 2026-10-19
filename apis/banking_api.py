
from datetime import date  # Parses and compares statement dates
from uuid import uuid4  # Generates unique statement references

from fastapi import APIRouter, Header, HTTPException, Query

# APIRouter -> Groups related banking endpoints
# Header -> Reads customer identity from a request header
# HTTPException -> Returns HTTP errors such as 403 and 404
# Query -> Validates query parameters, such as transaction limits
from models.banking_models import (
    BalanceResponse,
    StatementResponse,
    TransactionsResponse,
    ValidateAccountRequest,
    ValidateCardRequest,
    ValidationResponse,
)
from utils.database import (
    get_connection,  # Provides a database connection.
)

# Pydantic response models -> Validate and document the API response structure

router = APIRouter(prefix="/api", tags=["Banking"])

# prefix="/api" means every route starts with /api.
# tags=["Banking"] groups these endpoints under the Banking section in Swagger UI.


# What does the decorator do?
# @router.get(...) registers a function as an HTTP GET endpoint.
# The URL contains {account_reference}, which FastAPI extracts from the path and passes to the function.
# The response_model=BalanceResponse tells FastAPI to validate the returned data against the Pydantic model and document the expected response.

# What does Header do?
# customer_id: str = Header(alias="X-Demo-Customer-ID")

# It tells FastAPI to read the customer ID from an HTTP request header.
# A request includes:X-Demo-Customer-ID: CUST001

# FastAPI passes CUST001 into the customer_id parameter.
# This header is only a development shortcut. A real application must derive the customer identity from verified authentication rather than trust a header supplied by the caller.


@router.get(
    "/accounts/{account_reference}/balance",
    response_model=BalanceResponse,
)
def get_account_balance(
    account_reference: str,
    customer_id: str = Header(alias="X-Demo-Customer-ID"),
):
    with get_connection() as connection:

        # First, check whether the account exists.
        account = connection.execute(
            """
            SELECT account_number, customer_id, balance, currency
            FROM accounts
            WHERE account_number = ?
            """,
            (account_reference,),
        ).fetchone()

        # Return 404 if the account does not exist.
        if account is None:
            raise HTTPException(
                status_code=404,
                detail="Account not found",
            )

        # Return 403 if the account belongs to another customer.
        if account["customer_id"] != customer_id:
            raise HTTPException(
                status_code=403,
                detail="You are not authorized to access this account",
            )

    # Return the account details only after ownership validation.
    return {
        "account_reference": account["account_number"],
        "balance": account["balance"],
        "currency": account["currency"],
    }

@router.get(
    "/cards/{card_reference}/transactions",
    response_model=TransactionsResponse,
)
def get_card_transactions(
    card_reference: str,
    limit: int = Query(default=5, ge=1, le=10), # The limit parameter controls how many transactions the API returns.
    customer_id: str = Header(alias="X-Demo-Customer-ID"),
):
    # Open a connection to the SQLite database.
    with get_connection() as connection:

        # Find the card only if it belongs to this customer.
        card = connection.execute(
            """
            SELECT card_id, masked_card_number
            FROM cards
            WHERE masked_card_number = ? AND customer_id = ?
            """,
            (card_reference, customer_id),
        ).fetchone()

        # Stop if the card does not exist or belongs to another customer.
        if card is None:
            raise HTTPException(
                status_code=404,
                detail="Card not found",
            )

        # Fetch transactions associated with this card.
        transactions = connection.execute(
            """
            SELECT transaction_id, transaction_date,
                   description, amount, transaction_type
            FROM transactions
            WHERE card_id = ?
            ORDER BY transaction_date DESC
            LIMIT ?
            """,
            (card["card_id"], limit),
        ).fetchall()

    # Convert database rows into the response format expected by FastAPI.
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



@router.post(
    "/accounts/{account_reference}/statements",
    response_model=StatementResponse,
)
def request_account_statement(
    account_reference: str,
    start_date: date,
    end_date: date,
    customer_id: str = Header(alias="X-Demo-Customer-ID"),
):
    # Validate the requested date range.
    if start_date > end_date:
        raise HTTPException(
            status_code=422,
            detail="Start date must not be after end date",
        )

    # Open a connection to the database.
    with get_connection() as connection:

        # Find the account belonging to this customer.
        account = connection.execute(
            """
            SELECT account_id, account_number
            FROM accounts
            WHERE account_number = ? AND customer_id = ?
            """,
            (account_reference, customer_id),
        ).fetchone()

        # Do not allow access to another customer's account.
        if account is None:
            raise HTTPException(
                status_code=404,
                detail="Account not found",
            )

        # Generate a unique reference for this request.
        statement_reference = (
            f"STMT-{uuid4().hex[:12].upper()}"
        )

        # Save the statement request in SQLite.
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
                start_date.isoformat(),
                end_date.isoformat(),
                "REQUESTED",
            ),
        )

    # Return the saved request details.
    return {
        "account_reference": account["account_number"],
        "start_date": start_date.isoformat(),
        "end_date": end_date.isoformat(),
        "statement_reference": statement_reference,
        "status": "REQUESTED",
    }


@router.post("/accounts/validate", response_model=ValidationResponse)
def validate_account(
    body: ValidateAccountRequest,
    customer_id: str = Header(alias="X-Demo-Customer-ID"),
):
    with get_connection() as connection:
        row = connection.execute(
            """
            SELECT c.name
            FROM accounts a JOIN customers c USING (customer_id)
            WHERE a.account_number = ? AND a.customer_id = ?
              AND a.status = 'ACTIVE'
            """,
            (body.account_number, customer_id),
        ).fetchone()

    if row is None:
        return {"status": "INVALID"}
    return {"status": "VALID", "customer_name": row["name"]}


@router.post("/cards/validate", response_model=ValidationResponse)
def validate_card(
    body: ValidateCardRequest,
    customer_id: str = Header(alias="X-Demo-Customer-ID"),
):
    with get_connection() as connection:
        row = connection.execute(
            """
            SELECT c.name
            FROM cards k JOIN customers c USING (customer_id)
            WHERE k.masked_card_number = ? AND k.customer_id = ?
              AND k.status = 'ACTIVE'
            """,
            (body.card_number, customer_id),
        ).fetchone()

    if row is None:
        return {"status": "INVALID"}
    return {"status": "VALID", "customer_name": row["name"]}
