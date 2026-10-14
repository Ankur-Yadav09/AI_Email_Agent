
from fastapi.testclient import TestClient

from main import app

client = TestClient(app)

# What is TestClient?
# FastAPI's TestClient lets us send simulated HTTP requests directly to our application. We don't need to start Uvicorn separately to run these tests.

def test_health_check():
    response = client.get("/health")

    assert response.status_code == 200
    assert response.json() == {"status": "ok"}

# What does assert do?
# assert response.status_code == 200
# It verifies that the actual result matches our expectation. If the assertion fails, pytest reports a failed test.

def test_valid_account_balance():
    response = client.get(
        "/api/accounts/1234567890/balance",
        headers={"X-Demo-Customer-ID": "CUST001"},
    )

    assert response.status_code == 200

    data = response.json()
    assert data["account_reference"] == "1234567890"
    assert data["balance"] == 25000.00
    assert data["currency"] == "INR"


def test_invalid_account():
    response = client.get(
        "/api/accounts/0000000000/balance",
        headers={"X-Demo-Customer-ID": "CUST001"},
    )

    assert response.status_code == 404


def test_account_access_denied():
    response = client.get(
        "/api/accounts/1234567890/balance",
        headers={"X-Demo-Customer-ID": "CUST002"},
    )

    assert response.status_code == 403


def test_card_transactions_with_limit():
    response = client.get(
        "/api/cards/4567XXXX8901/transactions",
        params={"limit": 2},
        headers={"X-Demo-Customer-ID": "CUST001"},
    )

    assert response.status_code == 200

    data = response.json()
    assert len(data["transactions"]) == 2
    assert data["card_reference"] == "4567XXXX8901"


def test_invalid_card():
    response = client.get(
        "/api/cards/9999999999/transactions",
        headers={"X-Demo-Customer-ID": "CUST001"},
    )

    assert response.status_code == 404


def test_valid_statement_request():
    response = client.post(
        "/api/accounts/1234567890/statements",
        params={
            "start_date": "2026-04-01",
            "end_date": "2026-04-30",
        },
        headers={"X-Demo-Customer-ID": "CUST001"},
    )

    assert response.status_code == 200

    data = response.json()
    assert data["status"] == "REQUESTED"
    assert data["start_date"] == "2026-04-01"
    assert data["end_date"] == "2026-04-30"
    assert data["statement_reference"].startswith("STMT-")


def test_invalid_statement_date_range():
    response = client.post(
        "/api/accounts/1234567890/statements",
        params={
            "start_date": "2026-05-01",
            "end_date": "2026-04-01",
        },
        headers={"X-Demo-Customer-ID": "CUST001"},
    )

    assert response.status_code == 422
