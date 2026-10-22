# Interview Prep: Concepts Used in This Project

A study guide built from this codebase. For each concept: what it is, where we used it, why, and the questions interviewers tend to ask with short answers. Read it alongside [DESIGN_AND_DECISIONS.md](DESIGN_AND_DECISIONS.md) (the reasoning) and [ARCHITECTURE.md](ARCHITECTURE.md) (the specs).

**Before you start, a word of advice.** Know every part well enough to explain it without the docs, and be able to open the code and point to it (section 12 is a code map). If you are asked whether AI tools helped build it, say so plainly. Interviewers care far more that you understand the design, can defend the trade-offs and know the weak spots (section 14) than who typed the code.

---

## 1. The 2-minute story

> "I built an email assistant for a bank. Customers email questions like *what's my balance, show my last card transactions, send my statement*. The system reads the email, works out what is being asked, how the customer sounds and which account or card they mean, validates those details, fetches the data from a banking system through a small set of tools, and drafts a reply. **A staff member approves every reply before it is sent**, using a web console. It reads and replies through a real Gmail account.
>
> The main design principle is: **use the language model only to understand text, and use ordinary code for anything where a mistake costs money or trust.** The model classifies the email. Regex extracts account numbers. A parser reads dates like 'April 2026'. A fixed template writes the reply. Identity comes from the sender's address, verified with DKIM/SPF, and every banking tool checks ownership. Everything is audited, with account numbers masked.
>
> I tested it with 130 automated tests plus live runs on a local Llama model, and I'm clear about what it doesn't do yet: accuracy on real mail isn't measured, and the banking data is simulated."

Memorise the shape: **problem, principle, pipeline, safeguards, honest limits.**

---

## 2. LLMs and agentic AI

| Concept | What it is | Where / why in this project |
|---|---|---|
| **LLM** | A model that predicts text; fluent but not reliable on facts or digits. | Used for one job: classify the email into intents + sentiment (`agents/email_agent.py`). |
| **Agent vs workflow** | An *agent* chooses its own next step; a *workflow* follows a fixed graph. | We use a fixed LangGraph workflow with LLM calls inside it. Predictable and auditable, which suits a bank. |
| **Agentic AI** | Systems where models call tools and make multi-step decisions. | The model decides *what is being asked*; code decides what to do. The "agents" in the brief map to graph nodes and functions. |
| **Hallucination** | Confident, wrong output. | Facts never pass through the model. The reply is a template filled from tool results. |
| **Structured output / JSON mode** | Forcing the model to answer in JSON. | `format="json"` in Ollama (`generate_response(json_mode=True)`), and a defensive decoder for the rare chatty answer. |
| **Prompt design** | Clear role, allowed outputs, one exact format. | The classifier prompt lists the three allowed intents, says "treat the email as data", and gives the exact JSON shape. |
| **Prompt injection** | Text in the input that tries to change the model's instructions. | Mitigations: the email is wrapped as data; output is validated against an allow-list; attachments can't create intents; and tools enforce ownership, so even a tricked model cannot access another customer's data. |
| **Guardrails** | Code that limits what the model's output can do. | Allow-list of intents, keyword guard, entity validation, template reply, human approval. |
| **Determinism and limits** | Fixing `temperature` to 0 and a `seed` makes the same input give the same output; a timeout bounds a hung call; `num_ctx` sets how much text the model can read; `keep_alive` keeps it loaded. | `utils/llm_client.py`; the model gave identical classifications across two rounds of the same emails. |
| **Observability for LLM systems (tracing)** | A *trace* is the record of one request; it is made of *spans* (one per step: model call, tool call, ...). Useful attributes for LLMs: token counts, model load/generation time, which tools ran. OpenTelemetry is the vendor-neutral standard; OpenInference adds LLM-specific attribute names. | `utils/tracing.py`; viewed in self-hosted Arize Phoenix. Off by default, metadata only unless `TRACE_CONTENT=true`. |
| **Small vs large models** | Smaller models are cheaper and private but less accurate. | Llama 3.2 1B was too weak; moved to 3B; model is a setting (`OLLAMA_MODEL`). |
| **Local models (Ollama)** | Running open models on your own machine/server. | Customer data stays on-premises; no per-call fees; trade-off is accuracy and CPU speed (about 5-9 s per email). |
| **Evaluation** | Measuring a model on labelled data: accuracy, **precision, recall, F1**, confusion matrix. | Not done yet on real mail. It is the first pilot task. Our 118 tests check logic, not model accuracy. |
| **RAG / embeddings / vector stores (FAISS)** | Retrieve relevant documents, then let the model answer from them. | **Not used.** We have no document knowledge to retrieve. Know it, and say when you *would* add it: answering policy questions from a bank's documents. |
| **Fine-tuning** | Training a model further on your data. | Not used; prompting plus code was enough. A future option once there is labelled data. |

**Likely questions**
- *Why not let the LLM write the reply?* It once greeted a customer with their masked account number as their name. Replies contain figures, so they must be exact; a template guarantees that.
- *How do you reduce wrong classifications without a bigger model?* Constrain the output (JSON mode, allow-list), cross-check it with code (keyword guard), and keep a human approving.
- *Which error is worse, a false positive or a false negative?* Here a false positive (acting on a request nobody made, such as fetching transactions) is worse than a miss, because it touches data. That is why the guard drops unsupported intents rather than adding missing ones.
- *How would you measure it?* Label a few hundred real emails; compute per-intent precision, recall and F1 and a confusion matrix; track the share of drafts approved unchanged, edited, rejected.

---

## 3. Orchestration: LangGraph

| Concept | Meaning in our code |
|---|---|
| **StateGraph** | The workflow: nodes (`receive_email`, `classify_email`, `supported_requests`, `generate_draft`, `human_review`, `handle_unsupported`) and edges (`agents/email_workflow.py`). |
| **State** | A Pydantic model (`models/email_state.py`). Each node returns a dict of fields to update. There are no custom reducers, so a returned field **replaces** the old value (this matters for lists like `errors`). |
| **Conditional edge** | `route_request` sends the flow to the tool step or to the hand-off step. |
| **Checkpointer** | `AsyncSqliteSaver` saves state after each step to SQLite, keyed by `thread_id` (we use the `email_id`). |
| **`interrupt()` and `Command(resume=...)`** | The graph pauses in `human_review`; a later API call resumes it with the reviewer's decision. The pause survives a server restart because state is in SQLite. |

**A subtle point worth knowing:** when a graph resumes, the interrupted node **runs again from its top**. Any side effect placed before `interrupt()` runs twice (we hit this with audit logging, so the "draft generated" event lives in the node *before* the pause).

**Another:** resuming a thread that has no saved state does not fail. It starts a fresh run, and in testing it overwrote a record. We now check `aget_state(...).next` first and answer 409.

**Likely questions**
- *Why LangGraph over CrewAI or plain LangChain?* A deterministic, inspectable graph with a built-in pause and checkpointing. Free-roaming agents are harder to audit.
- *How is human-in-the-loop implemented?* `interrupt` plus a persistent checkpointer; the review endpoint resumes with `Command`.
- *What happens if the server restarts while a draft awaits review?* Nothing is lost: state and the email record are both in SQLite.

---

## 4. Tool layer: MCP (Model Context Protocol)

- **What:** an open protocol for exposing tools to AI applications. A **server** declares tools (name, typed arguments, description); a **client** lists and calls them. Ours uses the **stdio transport**: the client launches the server as a child process and talks over its stdin/stdout.
- **Where:** `agents/mcp_server.py` (7 tools: balance, card transactions, statement request, list accounts, list cards, validate account, validate card) and the client code in `supported_requests`.
- **Why:** a narrow, typed, allow-listed boundary between the AI side and banking data. **Ownership is enforced inside each tool**, so nothing the model produces can bypass it. It is also the natural place to swap in the real core-banking APIs.
- **Detail to remember:** the server process gets only a minimal environment, so we pass `DATA_DIR` explicitly (and deliberately nothing secret).
- **Trade-off:** one server process is started per email. Fine at pilot scale; a pool or long-lived server would be next.

**Likely questions**
- *MCP vs plain function calling?* Function calling is a model feature; MCP standardises *how tools are exposed and discovered* across applications, and keeps the tools in their own process.
- *Why does the tool take `customer_id`?* So ownership is checked at the data boundary (this prevents IDOR; see section 7).
- *Why is the model not the one calling the tools?* Our code decides which tools to call from the validated classification. A tool call chosen by a model would be one more place for it to be wrong or manipulated.

---

## 5. Backend and API design (FastAPI)

| Concept | Where |
|---|---|
| **Routers and prefixes** | `apis/email_api.py` (`/emails`), `apis/banking_api.py` (`/api`). |
| **Pydantic models** | Request/response validation (`models/*.py`); `EmailStr`, length limits. |
| **Dependency injection** | `require_token` is attached to the whole `/emails` router with `dependencies=[Depends(...)]`. |
| **Security scheme** | `APIKeyHeader("X-Reviewer-Token")` makes Swagger show an *Authorize* button. |
| **Lifespan** | `main.py` opens the checkpoint DB, builds the workflow, optionally starts the mailbox poller, and closes everything on shutdown. |
| **Async endpoints** | The workflow is async; blocking work (IMAP, SMTP, OCR) goes through `asyncio.to_thread`. |
| **Status codes** | 401 missing/invalid token, 403 unknown sender, 404, 409 conflict (not awaiting review; saved state missing), 422 validation, 502 mailbox unreadable, 503 mailbox not configured. |
| **TestClient** | Whole-app tests, including the lifespan (`with TestClient(app)`). |

**Likely questions**
- *401 vs 403?* 401: we don't know who you are (no/invalid token). 403: we know who you are and you aren't allowed (unregistered sender).
- *Why 409 for reviewing twice?* The request conflicts with the resource's current state.
- *Where do you validate input?* At the boundary (Pydantic), then again where it matters (tools validate numbers and ownership; the date parser rejects impossible dates).

---

## 6. Concurrency and reliability (asyncio)

- **`asyncio.gather`**: run independent intents at the same time; each returns its own `(intent, data, error)` so one failure never hides another.
- **`asyncio.wait_for`**: a 15-second timeout on every tool call.
- **Retries with idempotency in mind**: read-only tools retry once on timeout; the **statement request writes a row, so it is never retried** (a blind retry could create a duplicate).
- **`asyncio.to_thread`**: keeps the event loop responsive while blocking IMAP/SMTP/OCR run.
- **`asyncio.Lock`**: a manual poll and the background poller cannot overlap.
- **Idempotency by `Message-ID`**: re-polling never processes a message twice.
- **Graceful degradation**: model down, attachment corrupt, tool slow, send failed: each has a defined, safe outcome (table in the design document).

**Likely questions**
- *Concurrency vs parallelism?* `asyncio` is concurrent I/O on one thread; it overlaps waiting, not CPU work.
- *What is idempotency and where did you need it?* Repeating an operation has the same effect as doing it once: `Message-ID` de-duplication, and no retry on the statement write.
- *What race could occur?* Two polls fetching the same unread message. The lock prevents it, and the stored `Message-ID` is a second safeguard.

---

## 7. Security and privacy

| Concept | How we applied it |
|---|---|
| **Authentication vs authorisation** | Who is it (sender verified by DKIM/SPF; reviewer token) vs what may they do (tools check ownership). |
| **IDOR (insecure direct object reference)** | A customer typing someone else's account number gets "INVALID"; the tools check `customer_id` owns the object. We even return `INVALID` instead of "not yours", so existence isn't leaked. |
| **Defence in depth** | Sender lookup + DKIM/SPF + ownership in tools + human approval + token on the API. Each assumes the previous can fail. |
| **Spoofing** | The `From` header is forgeable. We trust only Gmail's own `Authentication-Results` (`mx.google.com`) and require DKIM/SPF to pass **for the same domain as `From`** (alignment). A header added by the sender is ignored. |
| **Least privilege** | Tools are an allow-list; the MCP server gets only `DATA_DIR`; the mailbox search is limited to recent mail, and optionally to named senders. |
| **PII masking** | Last four digits in logs, audit and replies (`utils/logging_config.py`); the regex avoids money amounts and UUID-style IDs. |
| **Audit logging** | One JSON line per event keyed by `email_id`; answers "what did the system see and who approved it?". |
| **XSS** | Customer text is untrusted. The console renders it only with `textContent`; a test fails the build if `innerHTML` appears; a browser test uses a hostile email. |
| **CSRF** | The token is sent in a custom header, not a cookie, so a malicious page cannot make the browser attach it automatically. |
| **Constant-time comparison** | `hmac.compare_digest` for the token, to avoid timing leaks. |
| **Header injection** | Subjects are collapsed to one line before sending, so `\r\n` cannot add headers like `Bcc`. |
| **Path traversal and uploads** | Attachment names are reduced to a safe basename; size cap 10 MB; only PDF/image types are read. |
| **Secrets** | `.env` is git-ignored and Docker-ignored; the App Password is never in code; `.env.example` is the template. |
| **Reply loops** | Automated mail is skipped; our replies carry `Auto-Submitted: auto-replied` (RFC 3834). |

**Likely questions**
- *How do you stop one customer reading another's balance?* Identity from the verified sender, never from the email body; ownership checked in each tool.
- *What if the model is manipulated?* Its output is only an intent from a 3-item allow-list plus fields we validate. It cannot reach other customers' data or send mail.
- *What's the weakest point?* The shared reviewer token (no per-user identity) and the App Password (full mailbox access). Next steps: SSO and OAuth.

---

## 8. Email protocols and formats

- **IMAP**: reading mail. We use `UID SEARCH UNSEEN SINCE <date> [FROM ...]` and `FETCH ... BODY.PEEK[]`. **`PEEK`** reads without setting the `\Seen` flag, so we mark read only after handling. **UIDs** are stable per mailbox and grow over time, so the highest UIDs are the newest.
- **SMTP**: sending. Port 587 with **STARTTLS**, then login, then `send_message`.
- **MIME / `.eml`**: parsed with the standard `email` library: multipart bodies, HTML-only mail, attachments with content types.
- **Threading**: `Message-ID`, `In-Reply-To` and `References` make the reply appear in the customer's conversation.
- **SPF**: which servers may send for a domain. **DKIM**: a cryptographic signature the domain adds. **DMARC**: policy tying them to the visible `From` domain (alignment). We rely on the receiving provider's verdict.
- **App Password vs OAuth**: an App Password is a revocable per-app credential (needs 2-Step Verification). OAuth is more granular and robust, but needs a registered app and consent flow.
- **Polling vs push**: we poll on an interval. IMAP IDLE or webhooks would be near-instant.

**Likely questions**
- *Why mark mail read after processing?* If we crash mid-way, the message is still unread and can be retried (though we also mark failures read so one bad message is not retried forever).
- *How would you receive mail without polling?* IMAP IDLE, or provider push (e.g. Gmail push notifications), or an inbound-mail webhook.

---

## 9. Text processing and NLP

- **Regex with lookarounds**: account/card extraction (`account ... \d{6,18}`, masked cards like `4567XXXX8901`); masking uses lookbehind/lookahead (`(?<![\w.,-])\d{6,19}(?![\w-]|[.,]\d)`) so it hits stand-alone numbers but not `25000.00` or digits inside a UUID. Know what `\b`, greedy vs lazy, and lookarounds do.
- **Deterministic date parsing** (`utils/periods.py`): month + year, `04/2026`, "last month", bare month after for/of/in, explicit ranges. **Day-first** numeric dates (India). Ambiguity returns `None`, so we ask instead of guessing. Edge cases handled: leap years, impossible dates, the verb "may", future periods, the current month clipped to today.
- **Entity extraction**: account, card, dates, transaction count, name (hybrid: regex + parser + validated LLM fields).
- **Intent classification and sentiment**: the LLM, with allow-list and keyword guard.
- **Email cleaning**: strip quoted replies, "On ... wrote:" threads and signatures; HTML to text.
- **OCR**: Tesseract (via `pytesseract`) for images. **PDF text** via `pypdf`; a scanned PDF has no text layer, so it needs OCR (not done).
- **Indian digit grouping**: `1,25,000.00` (lakh/crore), implemented in `inr()`.

**Likely questions**
- *Why not ask the LLM for everything?* A small model is unreliable with digits and dates; exact logic is testable and deterministic.
- *Rules vs ML: trade-off?* Rules are predictable and explainable but brittle to unseen phrasing; ML generalises but is probabilistic. We combine them and fall back to asking the customer.

---

## 10. Data and persistence

- **SQLite**, three files in `DATA_DIR`: `banking.db`, `email.db`, `email_checkpoints.db`.
- **Schema**: customers, accounts, cards (masked numbers), transactions, statement_requests; `email_records` for the review queue.
- **Migrations**: `ALTER TABLE ... ADD COLUMN` guarded by `PRAGMA table_info`, so older databases upgrade automatically.
- **Parameterised queries** everywhere (no string-built SQL with user input) to prevent SQL injection.
- **Foreign keys and constraints** (`PRAGMA foreign_keys = ON`, `UNIQUE`, `CHECK`).
- **Config via environment** (`DATA_DIR`) lets tests use temp folders and Docker use a volume.
- **Why SQLite, and the upgrade path:** zero set-up; limited to one writer. Production: PostgreSQL, with the SQL isolated in two small modules.

**Likely questions**
- *How do you migrate a live schema?* Additive, nullable-or-defaulted columns first; never drop in the same release.
- *What would break first at 100,000 emails a day?* The single-process design and SQLite writes, then CPU-bound model inference. See the scaling answer in section 11.

---

## 11. The project deep-dive: questions you should expect

1. **Walk me through what happens when an email arrives.** Use the 13-step table in the design document. Safety filters, sender lookup, attachments, classify, route, fetch in parallel, template, pause, review, send, audit.
2. **Why a local model?** Bank data stays on-premises, no per-call cost, matches the brief; trade-off is accuracy and speed, which code and review compensate for.
3. **How do you prevent wrong or invented information?** Facts come from tools; replies from a template; the model only classifies; a human approves.
4. **What if the model misclassifies?** Allow-list + keyword guard remove spurious intents; missing details trigger a question to the customer; a person reviews every reply; low-confidence cases go to "Needs a person".
5. **How do you handle partial failure?** Each intent is isolated: a bad card number still lets the balance through, and the reply asks to recheck the card.
6. **How is identity established?** Sender address looked up in the customer table, backed by DKIM/SPF alignment; ownership checked per tool.
7. **What did you do about fraud or lost-card emails?** They are never answered with account data: urgent words clear the intents and the email goes to a person, marked urgent.
8. **How would you scale it?** Stateless API behind a load balancer; PostgreSQL; a queue (e.g. Redis/Celery or a cloud queue) between mailbox polling and workers; several workers; a GPU or a hosted model for inference; long-lived tool servers; metrics and alerts.
9. **How would you add a new request type?** Six steps: tool, intent + keywords + prompt, workflow branch, reply section, console label, tests.
10. **How do you test something nondeterministic?** Fake the model in the unit tests and test the *logic around it* (parsers, guards, routing, templates); measure the model separately with a labelled set; run live checks for smoke tests.
11. **What was the hardest bug?** Pick one from the list in section 13 and tell it as a story.
12. **What would you do differently?** Measure accuracy earlier; label data first; start with a bigger model to see the ceiling; build the console earlier.
13. **How do you know it works?** 130 automated tests, a browser test of the console, and live runs. Be explicit that *accuracy on real mail* is unmeasured.
14. **What are the security risks?** Prompt injection, spoofed senders, IDOR, XSS in the console, credential exposure, PII in logs: each has a stated mitigation; remaining gaps are the shared token and App Password.
15. **Why SQLite?** Prototype speed; isolated so it can be swapped.
16. **How does the human review survive a restart?** LangGraph checkpoints in SQLite plus the `email_records` row.
17. **Why not auto-send?** Unmeasured accuracy and the cost of a wrong email; auto-send for low-risk cases is a measured, later decision.
18. **What's the cost model?** No per-email AI fees; cost is the server; a larger model needs more RAM/GPU.
19. **How does your reply get threaded in Gmail?** `In-Reply-To` and `References` set to the customer's `Message-ID`, and a `Re:` subject.
20. **How do you prevent the system replying to itself or to bounces?** Skip own address, `Auto-Submitted`, `Precedence: bulk`, mailer-daemon/no-reply senders; mark our replies `Auto-Submitted`.
21. **How do you monitor and debug the LLM part? Why not LangSmith?** Every email is a trace with a span per step; the model span carries token counts and Ollama's load/generate times, and the review span records whether the reviewer edited the draft (`edit_similarity`). Audit lines carry the `trace_id`. LangSmith is a cloud service that would receive customer emails and account numbers, so the default viewer is self-hosted Phoenix; because we emit OpenTelemetry, LangSmith or Langfuse can be targets if the client's policy allows. We also warn at start-up if LangGraph's built-in LangSmith export is switched on.
22. **What does the trace tell you that logs do not?** The tree and the timing of each step, the model's token cost and its cold-start (load) time, and, with the session view, an email's processing and its later review together. In our run it explained a 55 s p99 against an 8.6 s median: one cold start, `llm.load_ms` 38.7 s.

---

## 12. Code map (for live walkthroughs)

| To show | Open |
|---|---|
| The whole flow | `agents/email_workflow.py` (graph at the bottom) |
| Classification, guards, entities | `agents/email_agent.py` |
| Date logic | `utils/periods.py` |
| Reply text | `utils/reply_builder.py` |
| Banking tools (MCP) | `agents/mcp_server.py` |
| Mail in/out, safety filters | `utils/mailbox.py`, `utils/email_reader.py` |
| API, review, token, queue | `apis/email_api.py` |
| Masking and audit | `utils/logging_config.py` |
| Console | `ui/index.html` |
| Data | `utils/database.py`, `utils/email_database.py` |
| Packaging | `Dockerfile`, `docker-compose.yml`, `.env.example` |
| Tests | `tests/` (start with `test_periods.py`, `test_mailbox.py`, `test_handoff.py`) |

---

## 13. Bugs and lessons: ready-made stories

Tell these as **situation, what went wrong, what you did, what changed.**

1. **The reply that greeted "Dear XXXX7890".** The small model used a masked account number as a name. Lesson: don't let a probabilistic model write facts. Fix: a template.
2. **The 42,784-message inbox.** The first real mailbox had a huge unread backlog and the poller took the oldest first. Lesson: never touch data you did not mean to; scope by recency and sender, and use `PEEK`. Fix: recent-only, newest-first, allow-list.
3. **The review that silently re-ran.** Resuming a graph without saved state started a new run and overwrote the record. Found by a UI test. Lesson: verify state before resuming. Fix: a 409 guard.
4. **The masking that corrupted IDs.** A digit-run regex altered UUIDs in the audit log, making an email untraceable (it surfaced as a flaky test). Lesson: understand your regex boundaries. Fix: mask stand-alone numbers only.
5. **The fraud email that got a transactions table.** The model tagged it `get_transactions`. Lesson: some categories must never be automated. Fix: urgent words clear intents and hand off to a person.
6. **Tests that broke after real setup.** Tests shared the real database and read the real `.env`. Lesson: isolate test state completely. Fix: temp databases and cleared mail settings.
7. **The model that wrapped its JSON in prose.** A greedy `{.*}` regex broke on stray braces. Lesson: parse defensively. Fix: JSON mode plus first-object decoding (also 3-4x faster).

---

## 14. Honest weak spots, and how to say them

| Weakness | A good answer |
|---|---|
| Accuracy not measured | "The suite proves the logic given a model answer. Measuring the model needs a labelled set of real email, which is the first pilot step. I designed everything so a wrong model answer is caught by code or by the reviewer." |
| Mock banking data | "The tools are the integration seam; swapping the data source doesn't touch the workflow, reply or console." |
| SQLite / single process | "Chosen for prototype speed. The SQL is confined to two modules; the scaling plan is PostgreSQL plus a queue and workers." |
| Shared reviewer token | "A deliberate minimum for a prototype. Production needs SSO and recording who approved." |
| Polling, App Password | "Simple to demonstrate. IMAP IDLE/OAuth are the production routes." |
| Rule-based parsing is brittle | "Which is why ambiguity makes it ask the customer, and why the labelled set will tell us which phrasings to add." |
| Scanned PDFs, one language | "Known, listed, and scoped for later." |

---

## 15. Practice exercises

1. **Add a "cheque book request" intent**: list every file you would touch (section 9 of the design document).
2. **Handle 50,000 emails an hour**: draw the architecture; say what becomes a queue, what is stateless, where the model runs, how you keep ordering and idempotency.
3. **Evaluate the classifier**: design the labelled set, the metrics, the acceptance threshold, and what you'd do about a 15% error rate on "statement" requests.
4. **Make the reviewer token per-user**: where does the identity come from, where is it stored, what changes in the audit log?
5. **A customer complains they got someone else's data**: how would you investigate using the audit log, and what would you check in the tools?
6. **Make approvals dual-control** for large statements: where in the graph does it go?
7. **Support Hindi**: what changes in the model, the keyword guard, the date parser and the templates?

---

## 16. Quick glossary

**Agent**: a system where a model chooses actions. **Allow-list**: only listed values are accepted. **Alignment (DMARC)**: the authenticated domain matches the visible From domain. **Checkpoint**: saved workflow state. **Defence in depth**: several independent safeguards. **DKIM / SPF**: email sender authentication. **Guardrail**: code limiting what a model's output can do. **Hallucination**: confident wrong output. **HITL**: human in the loop. **IDOR**: accessing an object by guessing its ID without an ownership check. **Idempotent**: safe to repeat. **IMAP / SMTP**: protocols for reading / sending mail. **Intent**: what the user wants. **JSON mode**: constrained model output. **LangGraph**: library for stateful LLM workflows. **MCP**: protocol for exposing tools to AI applications. **OCR**: reading text from images. **Ollama**: runs open models locally. **PEEK**: IMAP fetch that doesn't mark mail read. **PII**: personally identifiable information. **Precision / recall**: share of predictions that are right / share of true cases found. **Prompt injection**: input text that tries to override instructions. **RAG**: retrieval-augmented generation. **Reducer**: how a graph merges a node's update into state. **XSS**: injecting script into a page. **OpenTelemetry**: vendor-neutral standard for traces, metrics and logs. **Span / trace**: one step of a request / the whole request. **OpenInference**: attribute names for LLM spans (kind LLM, TOOL, CHAIN; token counts). **Phoenix, Langfuse, LangSmith**: tools that show LLM traces; Phoenix and Langfuse are open source and self-hostable, LangSmith is a hosted service.
