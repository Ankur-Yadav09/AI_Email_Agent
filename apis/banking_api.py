
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
)

# Pydantic response models -> Validate and document the API response structure

router = APIRouter(prefix="/api", tags=["Banking"])

# prefix="/api" means every route starts with /api.
# tags=["Banking"] groups these endpoints under the Banking section in Swagger UI.

# Synthetic data for development and testing only.
ACCOUNTS = {
    "1234567890": {
        "customer_id": "CUST001",
        "balance": 25000.00,
        "currency": "INR",
    }
}
# This is a Python dictionary that acts as our simulated database.
# The account number is the key. Its value is another dictionary containing the owner, balance, and currency.

CARDS = {
    "4567XXXX8901": {
        "customer_id": "CUST001",
        "transactions": [
            {
                "transaction_id": "TXN001",
                "date": "2026-10-12",
                "description": "Grocery Store",
                "amount": 1250.00,
                "transaction_type": "debit",
            },
            {
                "transaction_id": "TXN002",
                "date": "2026-10-11",
                "description": "Online Shopping",
                "amount": 2499.00,
                "transaction_type": "debit",
            },
            {
                "transaction_id": "TXN003",
                "date": "2026-10-10",
                "description": "Refund",
                "amount": 500.00,
                "transaction_type": "credit",
            },
        ],
    }
}

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
    account = ACCOUNTS.get(account_reference)

    if not account:
        raise HTTPException(
            status_code=404,
            detail="Account not found",
        )
    # First, we check whether the account exists.
    # If it doesn't, we return HTTP 404 Not Found.

    if account["customer_id"] != customer_id:
        raise HTTPException(
            status_code=403,
            detail="You are not authorized to access this account",
        )
    # This checks whether the requested account belongs to the supplied customer.
    # - Correct customer: continue.
    # - Different customer: return 403 Forbidden.

    return {
        "account_reference": account_reference,
        "balance": account["balance"],
        "currency": account["currency"],
    }
    # FastAPI validates this dictionary against BalanceResponse and serializes it into JSON.


@router.get(
    "/cards/{card_reference}/transactions",
    response_model=TransactionsResponse,
)
def get_card_transactions(
    card_reference: str,
    limit: int = Query(default=5, ge=1, le=10), # The limit parameter controls how many transactions the API returns.
    customer_id: str = Header(alias="X-Demo-Customer-ID"),
):
    # You can request the last two transactions using:
    # GET /api/cards/4567XXXX8901/transactions?limit=2
    
    card = CARDS.get(card_reference)

    if not card:
        raise HTTPException(
            status_code=404,
            detail="Card not found",
        )

    if card["customer_id"] != customer_id:
        raise HTTPException(
            status_code=403,
            detail="You are not authorized to access this card",
        )

    return {
        "card_reference": card_reference,
        "transactions": card["transactions"][:limit], # Python list slicing returns the first limit entries from the list.
    }


@router.post(
    "/accounts/{account_reference}/statements",
    response_model=StatementResponse,
)
def request_account_statement(
    account_reference: str,
    start_date: date, # FastAPI parses date inputs in YYYY-MM-DD format.
    end_date: date,
    customer_id: str = Header(alias="X-Demo-Customer-ID"),
):
    account = ACCOUNTS.get(account_reference)

    if not account:
        raise HTTPException(
            status_code=404,
            detail="Account not found",
        )
    # First, we check whether the account exists.
    # If it doesn't, we return HTTP 404 Not Found.
    
    if account["customer_id"] != customer_id:
        raise HTTPException(
            status_code=403,
            detail="You are not authorized to access this account",
        )

    if start_date > end_date:
        raise HTTPException(
            status_code=422,
            detail="Start date must not be after end date",
        )

    return {
        "account_reference": account_reference,
        "start_date": start_date.isoformat(),
        "end_date": end_date.isoformat(),
        "statement_reference": f"STMT-{uuid4().hex[:12].upper()}",
        "status": "REQUESTED",
    }