
import sqlite3
from pathlib import Path

# Store the database file in the project root.
DATABASE_PATH = Path(__file__).resolve().parent.parent / "banking.db"


def get_connection():
    """Create and return a database connection."""
    connection = sqlite3.connect(DATABASE_PATH)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA foreign_keys = ON")
    return connection


def initialize_database():
    """Create tables and insert demo data."""

    with get_connection() as connection:

        # 1. Customers table
        connection.execute("""
            CREATE TABLE IF NOT EXISTS customers (
                customer_id TEXT PRIMARY KEY,
                name TEXT NOT NULL,
                email TEXT UNIQUE NOT NULL
            )
        """)

        # 2. Accounts table
        connection.execute("""
            CREATE TABLE IF NOT EXISTS accounts (
                account_id INTEGER PRIMARY KEY AUTOINCREMENT,
                customer_id TEXT NOT NULL,
                account_number TEXT UNIQUE NOT NULL,
                balance REAL NOT NULL CHECK (balance >= 0),
                currency TEXT NOT NULL DEFAULT 'INR',
                status TEXT NOT NULL DEFAULT 'ACTIVE',
                FOREIGN KEY (customer_id)
                    REFERENCES customers(customer_id)
            )
        """)

        # 3. Credit cards table
        connection.execute("""
            CREATE TABLE IF NOT EXISTS cards (
                card_id INTEGER PRIMARY KEY AUTOINCREMENT,
                customer_id TEXT NOT NULL,
                masked_card_number TEXT UNIQUE NOT NULL,
                status TEXT NOT NULL DEFAULT 'ACTIVE',
                FOREIGN KEY (customer_id)
                    REFERENCES customers(customer_id)
            )
        """)

        # 4. Transactions table
        connection.execute("""
            CREATE TABLE IF NOT EXISTS transactions (
                transaction_id TEXT PRIMARY KEY,
                card_id INTEGER NOT NULL,
                transaction_date TEXT NOT NULL,
                description TEXT NOT NULL,
                amount REAL NOT NULL,
                transaction_type TEXT NOT NULL
                    CHECK (transaction_type IN ('debit', 'credit')),
                FOREIGN KEY (card_id)
                    REFERENCES cards(card_id)
            )
        """)

        # 5. Statement requests table
        connection.execute("""
            CREATE TABLE IF NOT EXISTS statement_requests (
                statement_reference TEXT PRIMARY KEY,
                account_id INTEGER NOT NULL,
                start_date TEXT NOT NULL,
                end_date TEXT NOT NULL,
                status TEXT NOT NULL DEFAULT 'REQUESTED',
                FOREIGN KEY (account_id)
                    REFERENCES accounts(account_id),
                CHECK (start_date <= end_date)
            )
        """)

        # Insert demo customer. OR IGNORE prevents duplicate seed data.
        connection.execute("""
            INSERT OR IGNORE INTO customers
                (customer_id, name, email)
            VALUES (?, ?, ?)
        """, ("CUST001", "Demo Customer", "demo@example.com"))

        # Insert demo account
        connection.execute("""
            INSERT OR IGNORE INTO accounts
                (customer_id, account_number, balance, currency)
            VALUES (?, ?, ?, ?)
        """, ("CUST001", "1234567890", 25000.00, "INR"))

        # Insert demo card
        connection.execute("""
            INSERT OR IGNORE INTO cards
                (customer_id, masked_card_number)
            VALUES (?, ?)
        """, ("CUST001", "4567XXXX8901"))

        # Find the card's database ID.
        card = connection.execute("""
            SELECT card_id FROM cards
            WHERE masked_card_number = ?
        """, ("4567XXXX8901",)).fetchone()

        # Insert demo transactions.
        transactions = [
            ("TXN001", card["card_id"], "2026-10-12",
             "Grocery Store", 1250.00, "debit"),
            ("TXN002", card["card_id"], "2026-10-11",
             "Online Shopping", 2499.00, "debit"),
            ("TXN003", card["card_id"], "2026-10-10",
             "Refund", 500.00, "credit"),
        ]

        connection.executemany("""
            INSERT OR IGNORE INTO transactions
                (transaction_id, card_id, transaction_date,
                 description, amount, transaction_type)
            VALUES (?, ?, ?, ?, ?, ?)
        """, transactions)

    print("Database initialized successfully!")
