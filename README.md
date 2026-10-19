# AI Email Agent

Intelligent email processing and automated customer response for a bank. It reads customer emails (from a real Gmail inbox, a folder of `.eml` files, or the API), detects intents (including several in one email) and sentiment, extracts account/card details from the email and its PDF/image attachments, validates them, fetches data through MCP tools backed by mock banking APIs, and prepares a reply. **A human reviews every reply; only an approved one is emailed back.**

Design, workflow, API specs and limitations: [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md).

## Features

- **Intents:** balance enquiry, card transactions, statement request, or several in one email
- **Sentiment** (neutral / positive / negative / urgent) and **entity extraction** (account, card, dates, transaction count, name). Angry or urgent emails get an apologetic or acknowledging line and a `high` review priority
- **Validation** of account and card numbers; partial failures are reported without losing the successful parts (e.g. balance returned, "please recheck your card" for a bad card)
- **Replies built from a fixed template.** The LLM (Llama via Ollama) only classifies, so names, amounts and numbers are never invented
- **Orchestration:** LangGraph workflow with a human approve / edit / reject step (persisted in SQLite), independent intents run in parallel, tool calls have timeouts and safe retries
- **Real Gmail:** IMAP in, SMTP out (after approval), with sender authentication (DKIM/SPF), loop protection and de-duplication
- **Attachments:** PDF text and image OCR are searched for account/card numbers
- **Security and audit:** customer identity comes from the sender (never from the email text), every tool checks ownership, account/card numbers are masked in logs and replies, and a JSON audit trail records every decision and tool call

## Quick start (Docker, recommended)

Requirements: Docker Desktop.

```bash
docker compose up --build
```

The first run downloads the Llama model (about 1.3 GB, via the `ollama-pull` service), so the API starts after that finishes. Then open **http://localhost:8000/docs**. Databases live in the `app_data` Docker volume and survive rebuilds.

## Quick start (local)

Requirements: Python 3.12, [uv](https://docs.astral.sh/uv/), [Ollama](https://ollama.com), and optionally [Tesseract](https://github.com/UB-Mannheim/tesseract/wiki) (for image attachments; without it they are skipped).

```bash
uv sync
ollama pull llama3.2:1b
uv run python -m uvicorn main:app --port 8000
```

Run the helper scripts below with `uv run python scripts/...` (or from an activated virtual environment); in Docker use `docker compose exec api python scripts/...`.

The databases are created and seeded on startup: customer `CUST001` (`demo@example.com`), account `1234567890` (balance INR 25,000), card `4567XXXX8901`.

## Try it

In Swagger (`/docs`), or from a terminal:

```bash
# 1. Submit an email (see data/sample_emails.json for more)
curl -X POST localhost:8000/emails/process -H "Content-Type: application/json" \
  -d '{"sender_email":"demo@example.com","email_text":"Please provide balance for account 1234567890 and card transactions for card 999999999."}'

# 2. See what is waiting for review, then read a draft
curl "localhost:8000/emails?status=pending_review"
curl localhost:8000/emails/<email_id>

# 3. Approve it (or "approved": false to reject; add "edited_draft" to change the text)
curl -X POST localhost:8000/emails/<email_id>/review -H "Content-Type: application/json" \
  -d '{"approved":true,"feedback":"OK"}'
```

That example is the "partial validation failure" scenario: the balance is returned, and the draft asks the customer to recheck the card.

**Run all the sample emails** (`data/sample_emails.json`: the five business scenarios, an urgent email, an unsupported request and an unknown sender):

```bash
python scripts/demo.py                    # all samples, prints each draft
python scripts/demo.py scenario_5 urgent  # only samples whose name matches
python scripts/demo.py --approve          # also approve each draft
docker compose exec api python scripts/demo.py   # inside Docker
```

**Try attachments without Gmail** using the simulated mailbox (a folder of `.eml` files):

```bash
python scripts/make_sample_inbox.py            # writes 3 sample emails to data/inbox
curl -X POST localhost:8000/emails/inbox/poll  # processes them (one has the account number only in a PDF)
# Docker: docker compose exec api python scripts/make_sample_inbox.py
```

Approving a draft only sends an email when Gmail is configured (below). Otherwise `delivery_status` is `not_configured`.

## Real Gmail (receive and send)

Use **two Gmail accounts**: one for the bank's inbox, and one to play the customer. The agent ignores mail from its own address. A dedicated, empty account is best for the bank inbox; a demo on a personal inbox exposes your real mail and addresses.

1. In the bank account: turn on 2-Step Verification, create an **App Password** (https://myaccount.google.com/apppasswords) and enable IMAP (Gmail > Settings > Forwarding and POP/IMAP).
2. `cp .env.example .env` and fill in `MAIL_ADDRESS` and `MAIL_APP_PASSWORD` (paste the App Password without spaces). `.env` is git-ignored; never commit it.
3. Verify the login (sends nothing): `python scripts/check_mailbox.py`
4. Customers are recognised by sender address. Register the customer account you will send *from*:
   `python scripts/set_customer_email.py CUST001 customer@gmail.com "Full Name"`
5. Strongly recommended on a real inbox: set `MAIL_ONLY_FROM=customer@gmail.com` in `.env`, so mail from anyone else is never fetched or marked read.
6. Send an email from the customer account to the bank inbox, e.g. *"Please share my balance for account 1234567890."*
7. Process it: `curl -X POST localhost:8000/emails/mailbox/poll` (or set `MAIL_POLL_SECONDS=60` to poll automatically). An empty poll explains why in its `note`.
8. Review: `GET /emails?status=pending_review`, `GET /emails/{id}`, then `POST /emails/{id}/review` with `{"approved": true}`. The reply is emailed to the sender, threaded under their message. Rejecting sends nothing.

**In Docker**, run the helper scripts inside the container, and recreate it after editing `.env`:

```bash
docker compose up -d
docker compose exec api python scripts/check_mailbox.py
docker compose exec api python scripts/set_customer_email.py CUST001 customer@gmail.com "Full Name"
```

If the poll finds nothing, the most common cause is sending from an address other than the registered one.

**Safety rules built in:** only senders whose domain Gmail verified (DKIM/SPF) are answered; bounces, no-reply and auto-responder mail and the agent's own mail are ignored; each `Message-ID` is processed once; only unread mail from the last 2 days is looked at, newest first; nothing is sent without approval; a failed send never undoes an approval (`delivery_status: send_failed`).

## Configuration

Set in `.env` (see `.env.example`) or the environment.

| Variable | Default | Purpose |
|---|---|---|
| `MAIL_ADDRESS`, `MAIL_APP_PASSWORD` | – | Gmail account and App Password. Mailbox features are off until both are set |
| `MAIL_ONLY_FROM` | all senders | Comma-separated senders to fetch; others are never touched |
| `MAIL_MAX_AGE_DAYS` | `2` | Only look at unread mail from the last N days |
| `MAIL_POLL_SECONDS` | `0` (off) | Poll the mailbox in the background every N seconds |
| `REQUIRE_SENDER_AUTH` | `true` | Only answer senders Gmail verified. Disable for testing only |
| `IMAP_HOST`, `SMTP_HOST`, `SMTP_PORT` | Gmail, 587 | Other mail providers |
| `OLLAMA_HOST` | local Ollama | Set automatically in Docker |
| `DATA_DIR` | project folder | Where the SQLite databases live (a volume in Docker, a temp folder in tests) |

## API summary

| Endpoint | Purpose |
|---|---|
| `POST /emails/process` | Submit an email (text) |
| `POST /emails/mailbox/poll` | Read unread Gmail and process it |
| `POST /emails/inbox/poll` | Process `.eml` files in `data/inbox` (simulated mailbox) |
| `GET /emails?status=pending_review` | Work queue (urgent first, then oldest) |
| `GET /emails/{id}` | Status, intents, sentiment, priority, draft, errors, delivery status |
| `POST /emails/{id}/review` | Approve / edit / reject; an approval sends the reply |
| `/api/...` | Mock core banking: balance, card transactions, statement, validate account / card |
| `GET /health` | Liveness |

## Project structure

```text
agents/    LangGraph workflow, intent classifier, MCP server and client
apis/      FastAPI routers: email processing and the mock banking API
models/    Pydantic models and workflow state
utils/     databases, LLM client, mailbox (IMAP/SMTP), email reader, reply template, logging and masking
scripts/   demo, sample inbox, Gmail login check, customer registration
tests/     pytest suites
data/      sample_emails.json (demo scenarios); inbox/ and attachments/ are created at runtime
docs/      ARCHITECTURE.md
logs/      app.log and audit.log (created at runtime)
main.py    FastAPI entry point
```

## Tests

```bash
uv run python -m pytest tests -v
```

The tests need no LLM, network or mailbox: the LLM, IMAP and SMTP are faked, and they use throwaway databases in a temp folder, so they never touch your real data or credentials and can be repeated.

## Logs and audit

- `logs/app.log`: application log
- `logs/audit.log`: one JSON line per event (received, classified, each tool call with timing, draft, review, reply sent), keyed by `email_id`. With Docker, `./logs` is mounted from the container.

Both are masked (`1234567890` appears as `XXXX7890`).

## Limitations

- The 1B Llama model is small; intent accuracy has not been measured, and a keyword check removes intents the email never mentioned. Use a larger model for production.
- Scanned PDFs (images inside a PDF) are not read; text PDFs and images are.
- Attachments are only searched for account/card numbers; they are not understood or summarised.
- Mail is polled on an interval (no IMAP push); replies are plain text; Gmail aliases (dots, `+tags`) are not normalised when matching customers.
- Sentiment sets the tone and the review priority only; there is no escalation workflow.

More in [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md#5-assumptions-and-limitations).
