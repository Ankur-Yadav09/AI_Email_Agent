# How to Explain This Project to a Client

A speaking guide: what to say, what to show, and how to answer the hard questions. It is written for a business audience (operations, customer service, risk), not for engineers. Technical depth is in [DESIGN_AND_DECISIONS.md](DESIGN_AND_DECISIONS.md) and [ARCHITECTURE.md](ARCHITECTURE.md).

---

## 1. The one-minute pitch

> "Your support team spends a large part of the day reading emails that ask the same three things: *what is my balance, what are my recent card transactions, can I have a statement.* Today a person reads each email, works out what the customer wants, checks the customer's identity, looks the answer up in the banking system and types a reply.
>
> We built an assistant that does the reading, the lookup and the first draft in seconds. **A member of your staff then reviews the draft, and only when they approve it is the reply sent.** The assistant never replies on its own.
>
> It understands several requests in one email, notices when a customer is upset or in a hurry, reads attachments such as a PDF or a photo of an account number, and tells the customer politely when something they gave us is wrong, for example a card number that does not exist, while still answering the rest of their question."

If you only have 30 seconds: **"It reads the email, finds the answer, writes the reply. Your staff approve it with one click."**

---

## 2. The problem we are solving

| Today (manual) | With the assistant |
|---|---|
| A person reads every email and works out the request | The email is understood automatically, including several requests in one email |
| A person judges whether the customer is angry or urgent | Sentiment is detected and urgent or upset customers are flagged and answered more carefully |
| A person checks the account or card number is real and belongs to the sender | Every number is validated, and ownership is checked against the sender automatically |
| A person logs into the core banking system and copies the figures | The assistant fetches them from the banking system itself |
| A person types the reply, and wording varies by agent | A consistent, professional reply is drafted for review |
| Slow, error-prone, hard to scale, little record of what was done | Seconds per email, every step recorded in an audit trail |

---

## 3. What the system does, in plain words

Follow one email through the system:

1. **An email arrives** in the support mailbox.
2. **Safety checks first.** Is it really from who it says? Is it an automatic message or a bounce? Have we already handled it? Unsafe or irrelevant mail is ignored.
3. **We recognise the customer** from the sender's address. We never trust a name or account number written inside the email to decide who the customer is.
4. **We read the email**: what is being asked (balance, card transactions, statement), the customer's mood, and the details given (account number, card number, a period like "April 2026"). If there is an attachment, we read that too.
5. **We check the details** against the bank's records. A wrong or someone else's number is rejected.
6. **We fetch the answer** from the banking system, and when the customer asked for several things we fetch them at the same time.
7. **We draft the reply** from the verified figures using fixed, professional wording. Account and card numbers are shown only as their last four digits.
8. **A member of staff reviews it** on a simple screen: the customer's email on one side, the proposed reply on the other. They can edit it, approve it or reject it.
9. **Only on approval is the reply emailed**, in the same conversation as the customer's message.
10. **Everything is logged**: what was asked, what was looked up, who approved what.

---

## 4. What we can show them (the demo)

Allow 15 minutes. Use the Reviewer Console (`/ui`). Have the customer's mailbox open next to it so the client sees real email going in and out.

| # | You say | You send (from the registered customer address) | What the client sees |
|---|---|---|---|
| 1 | "The simplest case." | `Please share my balance for account 1234567890.` | Draft with the balance, correctly formatted (INR 25,000.00), account shown as XXXX7890. Approve. The reply arrives in the customer's inbox, in the same thread. |
| 2 | "Customers often ask for two things at once." | `Please share my balance for account 1234567890 and the last 3 transactions of my card 4567XXXX8901.` | One reply with both the balance and the transactions list. |
| 3 | "Now a mistake. What if the customer gives a wrong card number?" | `Please provide balance for account 1234567890 and card transactions for card 999999999.` | The balance is still given. The reply politely asks them to recheck the card. The console's yellow box tells the reviewer what could not be done. |
| 4 | "What about an unhappy customer?" | `This is unacceptable. I need my balance for account 1234567890 immediately, otherwise I will escalate this.` | The email is flagged **Urgent** and moved to the top of the queue. The reply opens with an apology line. |
| 5 | "Customers write in natural language." | `Kindly send my bank statement for April 2026.` | A statement request is created for 1 to 30 April 2026, with a reference number in the reply. Try "last month" too. |
| 6 | "And when they leave something out?" | `Please send my account statement.` | The reply asks which period they need. It does not guess. |
| 7 | "Attachments." (optional) | An email saying "balance please, my details are attached" with a PDF or screenshot containing the account number | The number is found in the attachment and used. |
| 7b | "What if they ask for something we don't automate?" | `Please update my home address.` | It lands in the queue marked **Needs a person**, with a hand-off draft pointing to your support channel. Nothing is changed in any system. |
| 8 | "Staying in control." | Edit a draft in the console, then approve | The edited text is what is sent. Show **Reject** too: nothing is sent. |

Close the demo by opening the **History** tab: every email, its outcome ("Reply sent", "Rejected: reason").

---

## 5. Why a bank can trust it (say this clearly)

**1. A human approves every reply.** There is no automatic sending. This is the single most important point. A mistake is caught before it reaches a customer.

**2. We identify the customer from the sender, not from the text.** A customer cannot ask for someone else's balance by typing someone else's account number: every lookup is checked against the identified customer's own accounts and cards.

**3. We verify the sender is real.** Mail is only answered when the mail provider has verified the sender's domain. Forged "From" addresses are ignored.

**4. The AI does not write the reply and does not touch the banking data.** The language model is used only to *understand* the email. The figures come straight from the banking system and the reply is built from a fixed template. The AI therefore cannot invent a balance, a transaction or a customer name. (We learned this the hard way during testing; see the design document.)

**5. Customer data stays inside your environment.** The language model runs locally (Llama through Ollama). Customer emails are not sent to an external AI service.

**6. Sensitive numbers are masked.** Replies, logs and audit records show only the last four digits.

**7. Full audit trail.** Every email, decision, banking lookup (with timing) and approval is recorded, so you can answer "who approved this, and what did the system see?"

**8. It fails safely.** If the AI is unavailable or the banking system is slow, the email is not guessed at. It is flagged for a person. Banking calls have time limits, and a request that writes data (a statement request) is never repeated automatically, so it cannot be created twice.

---

## 6. What it handles today, and what it does not

**Handles today**
- Account balance enquiries
- Credit card transaction enquiries (last N transactions)
- Statement requests (a month, "last month", or a date range)
- Several requests in one email
- Wrong, missing or someone else's account or card details, answered politely
- Angry or urgent customers (flagged and answered more carefully)
- PDF and image attachments, read for account or card numbers
- A real Gmail mailbox (receive and reply), and a web console for the reviewing staff

**Not in this version** (be upfront about these)
- It runs against **simulated banking data**. Connecting to your real core banking system is the main integration step.
- **Requests it cannot complete** (for example "update my address") are never actioned by the system. They appear in the review queue marked **Needs a person**, with a polite draft that points the customer to your support channel (set in one line of configuration). Staff can send it, rewrite it, or follow up another way. Emails about fraud, a lost or blocked card, or a dispute are never answered with account data: they are marked urgent and handed straight to a person.
- **Scanned PDFs** (a photo saved as PDF) are not read; normal PDFs and image files are.
- **One language** (English) and **plain-text** replies.
- **Accuracy has not been formally measured** on your real emails; see the plan below.
- Reviewers share one access token; there are no individual user accounts yet.
- It is a **prototype**, sized for a pilot, not yet for bank-wide volume.

---

## 7. Questions the client is likely to ask

**"How accurate is it?"**
Be honest: "It handles the standard cases reliably in our tests, and every reply is reviewed, so errors are caught before sending. We have not yet measured accuracy on your real emails. The pilot's first task is to build a test set from your own mail and measure it." (Our automated test suite has 87 checks; that is not the same as accuracy on real customer mail. Don't conflate them.)

**"Can it send replies automatically?"**
"Technically yes. We deliberately did not. Once you have measured accuracy and are comfortable, you could allow auto-send for the simplest, lowest-risk cases, for example a balance reply with no complaints, and keep human review for the rest."

**"What if it gets something wrong?"**
"The reviewer sees the original email next to the reply and can edit or reject it. Figures come from the banking system, not from the AI, so the usual error is *misunderstanding the request*, not *making up a number*."

**"Is our customer data safe? Does it go to ChatGPT or similar?"**
"No. The language model runs on your own server. Nothing is sent to an outside AI service. Numbers are masked in replies and logs, and every action is audited."

**"What does it cost to run?"**
"No per-email AI fees, because the model is local. The cost is the server it runs on. A bigger, more accurate model needs a stronger server, and that is the main tuning decision for the pilot."

**"How fast is it?"**
"In our tests, about 5 to 10 seconds from email to draft on an ordinary laptop, most of it the AI reading the email. The real delay is the human review, and the console puts the most urgent email first."

**"Can it handle our volume?"**
"This version is built for a pilot. The design separates the pieces (mailbox, AI, banking tools, review queue), so scaling is a matter of moving the database to a production one, running several copies and adding a work queue. That is planned, not built."

**"Can it connect to our real systems?"**
"Yes, by design. The banking lookups are a small set of tools. Today they read a demo database. Pointing them at your core banking APIs is a contained change. It does not alter the rest of the system."

**"What about other languages, SMS, WhatsApp, voice?"**
"The same pipeline can serve other channels; each needs an adapter and, for other languages, a suitable model. These are roadmap items."

**"What if the AI is down?"**
"The email is not guessed at. It is recorded and shown for a person to handle. Nothing is lost."

**"Can we see what it did for a particular email?"**
"Yes. The audit log shows each step for that email, in order: received, understood, what was looked up, the draft, who approved it, and whether the reply was sent."

**"How do we add a new type of request?"**
"One new banking lookup, one new intent and one new section in the reply template. The design document lists the exact steps."

---

## 8. Suggested pilot plan

| Phase | What | Outcome |
|---|---|---|
| 1. Measure (1-2 weeks) | Collect a few hundred real, anonymised emails. Label the correct request for each. Run them through the system. | A real accuracy number for intent, sentiment and details; a list of misunderstood phrasings |
| 2. Connect (2-4 weeks) | Point the banking lookups at your test environment. Add your authentication (single sign-on) for reviewers. | A realistic end-to-end trial in a safe environment |
| 3. Pilot (4-8 weeks) | A small group of reviewers works from the console on live mail, with human approval for every reply. | Handling time per email, rework rate, reviewer feedback |
| 4. Decide | Review the numbers. Consider limited auto-send for the lowest-risk cases, a larger model, more languages and channels. | A go / no-go for wider rollout |

Measure from day one: **time to handle an email before and after, share of drafts approved unchanged, share edited, share rejected, and the reasons.** Those four numbers make the business case. We do not quote savings now, because we have not measured them on your data.

---

## 9. Tips for presenting

**Do**
- Call it a **working prototype** and a **pilot**.
- Lead with control and safety (section 5) before features.
- Let the client *watch an email go in and a reply come out*: it is more convincing than slides.
- Show a failure case (the bad card number) on purpose. It builds trust more than a flawless demo.
- Mention the limits (section 6) yourself before they ask.

**Don't**
- Don't say "production-ready", "fully automated" or "100% accurate".
- Don't promise cost savings or percentages we have not measured.
- Don't demo from a personal mailbox, and don't show `.env`, log files or databases on screen. Use a dedicated demo mailbox.
- Don't skip the pre-flight check below. The first request after a restart can take noticeably longer while the model loads.

### Pre-demo checklist
- [ ] Docker stack is running; open the console and confirm it loads
- [ ] Reviewer token is set, and you can unlock the console
- [ ] `scripts/check_mailbox.py` says IMAP ok and SMTP ok
- [ ] The customer sender address is registered, and `MAIL_ONLY_FROM` is set to it
- [ ] Send one throw-away email and process it, so the model is warm
- [ ] Reject or clear any leftover test drafts so the queue starts empty
- [ ] Have screenshots ready in case the network or mailbox misbehaves

---

## 10. Words the client may hear (and plain meanings)

| Term | Meaning |
|---|---|
| **LLM / language model** | The AI that reads text. Here it only *understands* the email; it does not write the reply or touch the banking data. |
| **Llama / Ollama** | The open model we use, and the tool that runs it on your own server. |
| **Intent** | What the customer wants: balance, transactions, statement. |
| **Sentiment** | How the customer sounds: neutral, positive, upset, urgent. |
| **Human in the loop** | A person approves before anything is sent. |
| **Audit trail** | A permanent record of every action, for review and compliance. |
| **Masking** | Showing only the last four digits of a number. |
| **DKIM / SPF** | Email checks that prove a message really came from the sender's domain. |
| **MCP** | A standard way to give the AI a fixed, limited set of tools (here: the banking lookups), so it can only do what we allow. |
| **Mock / simulated banking** | A demo database standing in for the real core banking system. |
