# AI Email Agent

Intelligent email processing and automated customer response for a bank. It reads customer emails (from a real Gmail inbox, a folder of `.eml` files, or the API), detects intents (including several in one email) and sentiment, extracts account/card details from the email and its PDF/image attachments, validates them, fetches data through MCP tools backed by mock banking APIs, and prepares a reply. **A human reviews every reply; only an approved one is emailed back.**

Further reading in `docs/`:
- [ARCHITECTURE.md](docs/ARCHITECTURE.md): design, workflow, API specs and limitations
- [DESIGN_AND_DECISIONS.md](docs/DESIGN_AND_DECISIONS.md): the complete flow, the techniques used, and why each decision was taken
- [CLIENT_GUIDE.md](docs/CLIENT_GUIDE.md): how to explain and demo the project to a client
- [INTERVIEW_PREP.md](docs/INTERVIEW_PREP.md): the concepts used in the project, with likely interview questions and answers
- [CASE_STUDY.md](docs/CASE_STUDY.md): the project as an agentic-AI case study (model, agent pattern, tools, memory, state, RAG, context, security, reliability, deployment), with what was not built and how to defend it

## Features

- **Intents:** balance enquiry, card transactions, statement request, or several in one email
- **Sentiment** (neutral / positive / negative / urgent) and **entity extraction** (account, card, dates, transaction count, name). Angry or urgent emails get an apologetic or acknowledging line and a `high` review priority
- **Statement periods in plain words** ("April 2026", "last month", "01/04/2026 to 30/04/2026"), read by a deterministic parser rather than the model; if the period is unclear the customer is asked for it
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

The first run downloads the Llama model (about 2 GB, via the `ollama-pull` service), so the API starts after that finishes. Then open **http://localhost:8000/docs**. Databases live in the `app_data` Docker volume and survive rebuilds.

## Quick start (local)

Requirements: Python 3.12, [uv](https://docs.astral.sh/uv/), [Ollama](https://ollama.com), and optionally [Tesseract](https://github.com/UB-Mannheim/tesseract/wiki) (for image attachments; without it they are skipped).

```bash
uv sync
ollama pull llama3.2:3b
uv run python -m uvicorn main:app --port 8000
```

Run the helper scripts below with `uv run python scripts/...` (or from an activated virtual environment); in Docker use `docker compose exec api python scripts/...`.

The databases are created and seeded on startup: customer `CUST001` (`demo@example.com`), account `1234567890` (balance INR 25,000), card `4567XXXX8901`.

## Reviewer console (web UI)

Open **http://localhost:8000/ui**. It opens straight into the most urgent email waiting for review: the customer's email on the left, the reply on the right with separate **Subject** and **Message** boxes you can edit (an "edited" marker and **Reset** appear when you do), and chips showing what was asked and how the customer sounds. **Approve & send** shows a confirmation with exactly what will be emailed and to whom; **Reject** takes an optional reason. A yellow box lists anything the system could not do (for example an invalid card). The **History** tab shows handled emails read-only, with the outcome ("Reply sent", "Rejected: reason"). **Check mailbox** looks for new mail, and **New test email** runs the agent without Gmail. Each email has a shareable link (`/ui#<email_id>`), and the page works on phones and in dark mode. It is one static page (`ui/index.html`, no build step); everything customers write is displayed as plain text, never as HTML.

Because approving sends a real email, protect the API with a shared secret. Put `REVIEWER_TOKEN=<long random string>` in `.env` (generate one with `python -c "import secrets; print(secrets.token_urlsafe(24))"`), restart, and enter the token when the page asks for it (it is kept for that browser tab only; **Sign out** forgets it). Every `/emails` endpoint, including Swagger (use *Authorize*), then requires it. With no token set the API is open and the server logs a warning at startup, so only do that on a private machine.

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
python scripts/demo.py --approve          # also approve each draft (uses REVIEWER_TOKEN from the environment if set)
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
| `REVIEWER_TOKEN` | unset (API open) | Shared secret required by every `/emails` endpoint and the UI. Set it before exposing the port |
| `SUPPORT_CONTACT_TEXT` | "Please contact customer support for assistance." | Sentence added to the reply when a request cannot be completed by email (e.g. an address change). Set it to the real channel, such as net banking or a phone number |
| `OLLAMA_MODEL` | `llama3.2:3b` | Model used for classification. Docker pulls it automatically; locally run `ollama pull <model>` |
| `OLLAMA_TIMEOUT_SECONDS` | `120` | Give up on the model after this long; the email then goes to the review queue as "Needs a person" |
| `OLLAMA_NUM_CTX` | `4096` | Model context window |
| `OLLAMA_KEEP_ALIVE` | `30m` | How long the model stays loaded between emails (a cold start adds about 12 s) |
| `MAX_EMAIL_CHARS` | `6000` | The model sees at most this much of an email; numbers and dates are still read from the full text |
| `OTEL_EXPORTER_OTLP_ENDPOINT` | unset (tracing off) | Where to send traces, e.g. `http://phoenix:6006` (see Tracing) |
| `TRACE_CONTENT` | `false` | Also record the prompt and the model's answer in traces (customer text; numbers masked). Development only |
| `TRACE_PROJECT` | `ai-email-agent` | Project name shown in Phoenix |
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
| `GET /ui` | Reviewer console (web page) |
| `GET /health` | Liveness |

## Project structure

```text
agents/    LangGraph workflow, intent classifier, MCP server and client
apis/      FastAPI routers: email processing and the mock banking API
models/    Pydantic models and workflow state
utils/     databases, LLM client, mailbox (IMAP/SMTP), email reader, reply template, logging and masking
scripts/   demo, sample inbox, Gmail login check, customer registration, latency report
tests/     pytest suites
data/      sample_emails.json (demo scenarios); inbox/ and attachments/ are created at runtime
ui/        reviewer console (single static page)
docs/      ARCHITECTURE.md
logs/      app.log and audit.log (created at runtime)
main.py    FastAPI entry point
```

## Tests

```bash
uv run python -m pytest tests -v
```

The tests need no LLM, network or mailbox: the LLM, IMAP and SMTP are faked, and they use throwaway databases in a temp folder, so they never touch your real data or credentials and can be repeated.

## Tracing (optional)

Every email can be recorded as one **trace**: how long each step took, what the model was asked and cost in tokens, how long it took to load, which tools ran, and what the reviewer decided. It is **off by default** (nothing is recorded or sent) and built on OpenTelemetry, so any compatible tool can show it.

**Turn it on with the bundled viewer** (Arize Phoenix: open source, runs on your machine, nothing leaves it):

```bash
docker compose --profile tracing up -d       # starts Phoenix; the first time it downloads about 330 MB
# add to .env:  OTEL_EXPORTER_OTLP_ENDPOINT=http://phoenix:6006
docker compose up -d                          # restart the API so it picks the setting up
```

Then open **http://localhost:6006**, project `ai-email-agent`. The Traces tab lists one row per email and per review; click one for the step-by-step tree (`email.process` > `agent.classify` > `llm.chat`, `agent.tools` > one `tool.*` per call, `agent.draft`). **Sessions** groups an email's processing with its later review.

What each trace records, and what you can learn from it:

| Recorded | Why it is useful |
|---|---|
| `llm.token_count.*`, `llm.load_ms`, `llm.generate_ms` | Cost and speed of the model call; a large `load_ms` is a cold start |
| `classify.intents`, `classify.dropped_intents` | How often the model asked for something the email never mentioned |
| One `tool.*` span per call (arguments masked) | Which tools ran, how long each took, which failed |
| `review.edited`, `review.edit_similarity` | Did the reviewer change the draft, and how much (1.0 = approved as written)? A measure of draft quality |
| `trace_id` in every audit-log line | Jump from a log line to its trace and back |

**Privacy.** Only metadata is recorded by default (timings, token counts, intents, tool names, sizes, the email's id). The text of prompts and answers is recorded only if you set `TRACE_CONTENT=true`, and even then account and card numbers are masked; use that on a development machine only. Phoenix has no login, so Compose publishes it on `127.0.0.1` only. Tracing cannot slow or break the service: if the viewer is down, emails are still processed normally (exports run in the background and time out after 3 s, `OTEL_EXPORTER_OTLP_TIMEOUT`).

**Other tools.** Set the standard OpenTelemetry variables instead, for example `OTEL_EXPORTER_OTLP_ENDPOINT` and `OTEL_EXPORTER_OTLP_HEADERS` (or `OTEL_EXPORTER_OTLP_TRACES_ENDPOINT` for a full URL). Langfuse and LangSmith both accept OpenTelemetry traces; see their current documentation for the endpoint and headers. **We have not tested these two** (they need accounts). Both are cloud services, so keep `TRACE_CONTENT` off and check the client's data policy first.

**LangSmith warning.** LangGraph sends its own traces to LangSmith, unmasked, if `LANGSMITH_TRACING=true` is set. That would include the customer's email and account numbers, so the service logs a loud warning at start-up if it is on.

## Speed

About 7 seconds from an email being picked up to a draft ready for review (84% of it the language model), on a laptop CPU; the first email after a restart or a long idle takes about 20 seconds while the model loads. One worker handles roughly 480 emails an hour. Every stage writes its duration to the audit log; summarise it with:

```bash
docker compose exec api python scripts/latency_report.py     # or: python scripts/latency_report.py
```

Measurements and what they mean: [docs/CASE_STUDY.md](docs/CASE_STUDY.md#513-latency-and-throughput).

## Logs and audit

- `logs/app.log`: application log
- `logs/audit.log`: one JSON line per event (received, classified, each tool call with timing, draft, review, reply sent), keyed by `email_id`. With Docker, `./logs` is mounted from the container.

Both are masked (`1234567890` appears as `XXXX7890`).

## Limitations

- The default Llama 3.2 3B model is small; intent accuracy has not been measured, and a keyword check removes intents the email never mentioned. Use a larger model for production.
- Requests the system cannot complete (e.g. "update my address") are not answered automatically. They appear in the review queue marked **Needs a person**, with a polite hand-off draft pointing to `SUPPORT_CONTACT_TEXT`; emails about fraud, a lost or blocked card, or a dispute are always handed to a person (never answered with account data) and marked urgent.
- Scanned PDFs (images inside a PDF) are not read; text PDFs and images are.
- Attachments are only searched for account/card numbers; they are not understood or summarised.
- Mail is polled on an interval (no IMAP push); replies are plain text; Gmail aliases (dots, `+tags`) are not normalised when matching customers.
- Sentiment sets the tone and the review priority only; there is no escalation workflow.

More in [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md#5-assumptions-and-limitations).
