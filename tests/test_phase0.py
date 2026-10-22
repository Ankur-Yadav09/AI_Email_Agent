import json
import logging
import re
import uuid
from logging.handlers import RotatingFileHandler

import pytest
from fastapi.testclient import TestClient

import agents.email_agent as agent
import agents.email_workflow as wf
import utils.llm_client as llm
import utils.logging_config as logging_config
from main import app
from scripts.latency_report import percentile, render, summarise


# ---------- the model call: deterministic, bounded, kept warm ----------

def test_model_call_is_pinned_bounded_and_kept_warm(monkeypatch):
    seen = {}

    class FakeClient:
        def __init__(self, **kwargs):
            seen["client"] = kwargs

        def chat(self, **kwargs):
            seen["chat"] = kwargs
            return {"message": {"content": "{}"}}

    monkeypatch.setattr(llm.ollama, "Client", FakeClient)
    llm.generate_response("hi", json_mode=True)

    assert seen["client"] == {"timeout": llm.TIMEOUT_SECONDS} and llm.TIMEOUT_SECONDS > 0
    assert seen["chat"]["options"]["temperature"] == 0 and "seed" in seen["chat"]["options"]
    assert seen["chat"]["options"]["num_ctx"] > 0
    assert seen["chat"]["keep_alive"] and seen["chat"]["format"] == "json"

    llm.generate_response("hi")
    assert seen["chat"]["format"] is None


def test_a_model_timeout_degrades_to_a_person_instead_of_failing(monkeypatch):
    def hang(prompt, **kw):
        raise TimeoutError("model did not answer in time")

    monkeypatch.setattr(agent, "generate_response", hang)
    out = wf.classify_email(wf.EmailState(email_text="Subject: x\n\nbalance for account 1234567890"), None)
    assert out["intents"] == [] and out["errors"]  # no banking call; goes to review as "Needs a person"


# ---------- long emails: the model sees a capped prefix, code still sees everything ----------

def test_long_email_is_cut_for_the_model_but_exact_fields_come_from_the_full_text(monkeypatch):
    prompts = []
    monkeypatch.setattr(
        agent, "generate_response",
        lambda prompt, **kw: prompts.append(prompt) or json.dumps({"intents": ["check_balance"], "sentiment": "neutral"}),
    )
    text = "Please share my balance. " + "filler " * 3000 + " my account is 1234567890"
    result = agent.identify_intent(text)

    email_in_prompt = prompts[0].split("<email>")[1].split("</email>")[0]
    assert len(email_in_prompt) <= agent.MAX_EMAIL_CHARS + 5          # the model saw only the start
    assert "1234567890" not in prompts[0]                              # the number was beyond the cut ...
    assert result["entities"]["account_number"] == "1234567890"        # ... but code still found it
    assert result["intents"] == ["check_balance"]


# ---------- logging must never stop the service ----------

def test_unwritable_log_folder_falls_back_to_the_console(tmp_path, monkeypatch):
    root, audit_logger = logging.getLogger(), logging.getLogger("audit")
    saved = (root.handlers[:], audit_logger.handlers[:], getattr(root, "_email_agent_configured", False))
    blocker = tmp_path / "a_file"
    blocker.write_text("x")
    monkeypatch.setattr(logging_config, "LOG_DIR", blocker / "logs")  # a folder under a file can never be created
    root._email_agent_configured = False  # type: ignore[attr-defined]
    try:
        logging_config.setup_logging()  # must not raise
        added = [h for h in audit_logger.handlers if h not in saved[1]]
        assert added and not any(isinstance(h, RotatingFileHandler) for h in added)  # audit still goes somewhere
    finally:
        root.handlers[:] = saved[0]
        audit_logger.handlers[:] = saved[1]
        root._email_agent_configured = saved[2]  # type: ignore[attr-defined]


# ---------- the console's Content-Security-Policy ----------

def test_console_has_a_nonce_based_csp_and_nothing_inline_that_it_would_block():
    with TestClient(app) as client:
        first, second = client.get("/ui"), client.get("/ui")

    csp = first.headers["content-security-policy"]
    nonce = re.search(r"script-src 'nonce-([^']+)'", csp).group(1)
    assert f"style-src 'nonce-{nonce}'" in csp and "default-src 'none'" in csp
    assert "frame-ancestors 'none'" in csp and "unsafe-inline" not in csp and "unsafe-eval" not in csp
    assert first.text.count(f'nonce="{nonce}"') == 2 and "__NONCE__" not in first.text
    assert first.headers["x-content-type-options"] == "nosniff" and first.headers["cache-control"] == "no-store"

    other = re.search(r"script-src 'nonce-([^']+)'", second.headers["content-security-policy"]).group(1)
    assert other != nonce  # a fresh nonce for every request

    html = first.text
    assert not re.search(r'\sstyle="', html)           # inline style attributes would be blocked
    assert not re.search(r"\son[a-z]+\s*=", html)      # inline event handlers would be blocked
    assert "javascript:" not in html


# ---------- the system reports its own latency ----------

def test_each_stage_writes_its_duration_to_the_audit_log(monkeypatch):
    monkeypatch.setattr(wf, "identify_intent", lambda text, attachment_text="": {
        "intents": ["check_balance"], "sentiment": "neutral", "entities": {"account_number": "1234567890"}})

    with TestClient(app) as client:
        email_id = client.post("/emails/process", json={
            "email_text": "Subject: x\n\nbalance for account 1234567890", "sender_email": "demo@example.com"}).json()["email_id"]

    lines = [l for l in (logging_config.LOG_DIR / "audit.log").read_text(encoding="utf-8").splitlines() if email_id in l]
    events = {json.loads(l.split(" ", 2)[2])["event"]: json.loads(l.split(" ", 2)[2]) for l in lines}
    assert {"email_received", "classified", "tool_call", "requests_finished", "draft_generated", "email_pipeline"} <= set(events)
    for name, field in (("classified", "ms"), ("requests_finished", "ms"), ("draft_generated", "ms"),
                        ("email_pipeline", "total_ms"), ("email_pipeline", "attachments_ms")):
        assert isinstance(events[name][field], (int, float)) and events[name][field] >= 0, (name, field)
    assert events["email_pipeline"]["total_ms"] >= events["classified"]["ms"]


def test_latency_report_summarises_the_audit_log():
    lines = [
        '2026-10-21 10:00:00,000 {"event": "classified", "email_id": "a", "ms": 6000}',
        '2026-10-21 10:00:10,000 {"event": "classified", "email_id": "b", "ms": 8000}',
        '2026-10-21 10:00:20,000 {"event": "classified", "email_id": "c", "ms": 5000}',
        '2026-10-21 10:00:01,000 {"event": "tool_call", "tool": "get_account_balance", "ms": 4}',
        '2026-10-21 10:00:02,000 {"event": "email_pipeline", "attachments_ms": 2, "total_ms": 6100}',
        "not a log line", '2026-10-21 10:00:03,000 {broken json',
    ]
    samples = summarise(lines)
    assert samples["Classify (language model + parsing)"] == [6000, 8000, 5000]
    assert samples["Tool: get_account_balance"] == [4] and samples["Pipeline total (before human review)"] == [6100]
    assert percentile([1, 2, 3, 4, 5, 6, 7, 8, 9, 10], 50) == 5 and percentile([7], 95) == 7
    report = render(samples)
    assert "Classify" in report and "6000" in report and "Tool: get_account_balance" in report
    assert "No timings" in render({})


@pytest.mark.parametrize("llm_name, expected", [("Ankur Yadav", "Ankur Yadav"), ("null", None), ("None", None), ("  ", None), (None, None), (42, None)])
def test_placeholder_names_from_the_model_are_ignored(llm_name, expected):
    assert agent.extract_entities("hello", {"customer_name": llm_name})["customer_name"] == expected


def test_empty_settings_mean_use_the_default_not_crash(monkeypatch):
    """A blank line in .env (or an empty ${VAR:-} from Compose) must not stop the service from starting."""
    from datetime import date

    import utils.mailbox as mailbox

    for name in ("MAIL_POLL_SECONDS", "MAIL_MAX_AGE_DAYS", "SMTP_PORT", "SMTP_HOST", "IMAP_HOST", "MAIL_ONLY_FROM",
                 "REVIEWER_TOKEN", "TRACE_CONTENT", "OTEL_EXPORTER_OTLP_ENDPOINT"):
        monkeypatch.setenv(name, "")

    assert mailbox.unseen_criteria(date(2026, 10, 22)) == ["UNSEEN", "SINCE", "20-Oct-2026"]  # default of 2 days
    with TestClient(app) as client:  # start-up reads MAIL_POLL_SECONDS and the tracing settings
        assert client.get("/health").status_code == 200
