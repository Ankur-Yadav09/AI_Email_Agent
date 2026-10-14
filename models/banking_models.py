
from pydantic import BaseModel, Field


class BalanceResponse(BaseModel):
    """Response containing a customer's account balance."""

    account_reference: str = Field(description="Unique reference identifying the bank account.")
    balance: float = Field(ge=0,description="Available account balance.")
    currency: str = Field(default="INR",description="Currency of the account balance.")


class Transaction(BaseModel):
    """Details of a credit card transaction."""

    transaction_id: str = Field(description="Unique identifier for the transaction.")
    date: str = Field(description="Transaction date in YYYY-MM-DD format.")
    description: str = Field(description="Description of the transaction.")
    amount: float = Field(description="Transaction amount.")
    transaction_type: str = Field(description="Transaction type: debit or credit.")


class TransactionsResponse(BaseModel):
    """Response containing credit card transactions."""

    card_reference: str = Field(description="Reference identifying the credit card.")
    transactions: list[Transaction] = Field(description="List of credit card transactions.")


class StatementRequest(BaseModel):
    """Request for an account statement."""

    start_date: str = Field(description="Statement start date in YYYY-MM-DD format.")
    end_date: str = Field(description="Statement end date in YYYY-MM-DD format.")


class StatementResponse(BaseModel):
    """Response containing the account statement reference."""

    account_reference: str = Field(description="Reference identifying the bank account.")
    start_date: str = Field(description="Start date of the statement period.")
    end_date: str = Field(description="End date of the statement period.")
    statement_reference: str = Field(description="Unique reference for the requested statement.")
    status: str = Field(description="Status of the statement request.")
