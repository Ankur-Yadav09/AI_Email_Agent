import json
import logging

import pytest
from fastapi.testclient import TestClient
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter

import agents.email_workflow as wf
import utils.llm_client as llm
import utils.logging_config as logging_config
from main import app
from utils import tracing

EMAIL = "Subject: Balance\n\nPlease share my balance for account 1234567890."


@pytest.fixture
def spans():
    """Tracing on, recorded in memory (nothing leaves the test)."""
    exporter = InMemorySpanExporter()
    tracing.configure(exporter, batch=False)
    yield exporter
    tracing.shutdown()


def by_name(exporter):
    out = {}
    for s in exporter.get_finished_spans():
        out.setdefault(s.name, []).append(s)
    return out


@pytest.fixture
def fake_classifier(monkeypatch):
    monkeypatch.setattr(wf, "identify_intent", lambda text, attachment_text="": {
        "intents": ["check_balance"], "dropped_intents": ["request_statement"], "sentiment": "neutral",
        "entities": {"account_number": "1234567890"}})


# ---------- off by default, safe by default ----------

def test_tracing_is_off_unless_an_endpoint_is_configured(monkeypatch):
    for name in ("OTEL_EXPORTER_OTLP_ENDPOINT", "OTEL_EXPORTER_OTLP_TRACES_ENDPOINT"):
        monkeypatch.delenv(name, raising=False)
    assert tracing.setup_tracing() is False and tracing.enabled() is False
    with tracing.span("anything", "CHAIN", x__y=1) as s:  # a no-op: must not fail
        assert tracing.current_trace_id() is None
        s.set_attribute("a", 1)


def test_a_broken_tracing_setup_never_breaks_the_service(monkeypatch):
    def explode(*args, **kwargs):
        raise RuntimeError("backend exploded")

    monkeypatch.setenv("OTEL_EXPORTER_OTLP_ENDPOINT", "http://localhost:1")
    monkeypatch.setattr(tracing, "configure", explode)
    assert tracing.setup_tracing() is False


def test_langsmith_cloud_tracing_triggers_a_privacy_warning(monkeypatch, caplog):
    monkeypatch.setenv("LANGSMITH_TRACING", "true")
    with caplog.at_level(logging.WARNING):
        tracing.warn_if_cloud_tracing()
    assert "LangSmith tracing is ON" in caplog.text and "unmasked" in caplog.text

    caplog.clear()
    monkeypatch.setenv("LANGSMITH_TRACING", "false")
    tracing.warn_if_cloud_tracing()
    assert caplog.text == ""


def test_numbers_are_masked_and_content_is_not_recorded_by_default(spans):
    with tracing.span("s", "CHAIN", tool__parameters={"account_reference": "1234567890", "customer_id": "CUST001"},
                      note="card 4567XXXX8901 and account 1234567890", count=3, flag=True, missing=None):
        pass
    attrs = dict(spans.get_finished_spans()[0].attributes)
    assert "1234567890" not in json.dumps(attrs) and "XXXX7890" in attrs["tool.parameters"]
    assert "XXXX8901" in attrs["note"] and attrs["count"] == 3 and attrs["flag"] is True
    assert "missing" not in attrs and attrs["openinference.span.kind"] == "CHAIN"


# ---------- the model call ----------

def _fake_ollama(monkeypatch, response):
    class FakeClient:
        def __init__(self, **kwargs):
            pass

        def chat(self, **kwargs):
            return response

    monkeypatch.setattr(llm.ollama, "Client", FakeClient)


class Reply(dict):
    """Behaves like Ollama's reply: item access for the message, attributes for the counters."""

    prompt_eval_count, eval_count = 420, 38
    load_duration, prompt_eval_duration = 12_000_000_000, 900_000_000
    eval_duration, total_duration = 3_100_000_000, 16_200_000_000


def test_llm_span_records_model_tokens_and_where_the_time_went(spans, monkeypatch):
    _fake_ollama(monkeypatch, Reply(message={"content": '{"intents": []}'}))
    llm.generate_response("classify: account 1234567890", json_mode=True)

    a = dict(spans.get_finished_spans()[0].attributes)
    assert a["openinference.span.kind"] == "LLM" and a["llm.model_name"] == llm.MODEL_NAME and a["llm.json_mode"] is True
    assert (a["llm.token_count.prompt"], a["llm.token_count.completion"], a["llm.token_count.total"]) == (420, 38, 458)
    assert (a["llm.load_ms"], a["llm.prompt_eval_ms"], a["llm.generate_ms"], a["llm.total_ms"]) == (12000, 900, 3100, 16200)
    assert "temperature" in a["llm.invocation_parameters"]
    assert "input.value" not in a and "output.value" not in a  # customer text is not recorded by default


def test_prompt_and_answer_are_recorded_only_when_asked_and_then_masked(spans, monkeypatch):
    monkeypatch.setenv("TRACE_CONTENT", "true")
    _fake_ollama(monkeypatch, Reply(message={"content": "balance for 1234567890"}))
    llm.generate_response("account 1234567890 please")

    a = dict(spans.get_finished_spans()[0].attributes)
    assert a["input.value"] == "account XXXX7890 please" and a["output.value"] == "balance for XXXX7890"


def test_llm_span_works_when_ollama_reports_no_counters(spans, monkeypatch):
    _fake_ollama(monkeypatch, {"message": {"content": "{}"}})  # e.g. a stub or an older server
    llm.generate_response("hi")
    a = dict(spans.get_finished_spans()[0].attributes)
    assert "llm.token_count.total" not in a and a["output.size_chars"] == 2


# ---------- one connected trace per email ----------

def test_one_email_is_one_connected_trace(spans, fake_classifier):
    with TestClient(app) as client:
        email_id = client.post("/emails/process", json={"email_text": EMAIL, "sender_email": "demo@example.com"}).json()["email_id"]
    found = by_name(spans)

    for name in ("email.process", "agent.classify", "agent.tools", "agent.draft", "tool.validate_account", "tool.get_account_balance"):
        assert name in found, (name, sorted(found))

    root = found["email.process"][0]
    trace_ids = {s.context.trace_id for group in found.values() for s in group}
    assert trace_ids == {root.context.trace_id}, "every step must belong to the one trace of this email"

    # structure: process > classify | tools > tool.* | draft
    parent = {name: group[0].parent.span_id if group[0].parent else None for name, group in found.items()}
    assert parent["agent.classify"] == root.context.span_id and parent["agent.tools"] == root.context.span_id
    tools_span = found["agent.tools"][0]
    assert all(s.parent.span_id == tools_span.context.span_id for n, group in found.items() if n.startswith("tool.") for s in group)

    r = dict(root.attributes)
    assert r["email.id"] == email_id and r["session.id"] == email_id and r["email.status"] == "pending_review"
    c = dict(found["agent.classify"][0].attributes)
    assert json.loads(c["classify.intents"]) == ["check_balance"] and json.loads(c["classify.dropped_intents"]) == ["request_statement"]
    t = dict(found["tool.get_account_balance"][0].attributes)
    assert t["openinference.span.kind"] == "TOOL" and "1234567890" not in t["tool.parameters"]
    assert "1234567890" not in json.dumps([dict(s.attributes) for group in found.values() for s in group])

    # the trace list shows a short summary per step (metadata only: no email text unless TRACE_CONTENT is on)
    assert r["input.value"].startswith("email: ") and r["output.value"] == "status: pending_review"
    assert c["output.value"] == "intents=['check_balance'] sentiment=neutral"
    assert "Please share" not in json.dumps([r, c])


def test_audit_lines_carry_the_trace_id_so_logs_and_traces_match(spans, fake_classifier):
    with TestClient(app) as client:
        email_id = client.post("/emails/process", json={"email_text": EMAIL, "sender_email": "demo@example.com"}).json()["email_id"]
    trace_id = format(by_name(spans)["email.process"][0].context.trace_id, "032x")

    lines = [l for l in (logging_config.LOG_DIR / "audit.log").read_text(encoding="utf-8").splitlines() if email_id in l]
    events = {json.loads(l.split(" ", 2)[2])["event"]: json.loads(l.split(" ", 2)[2]) for l in lines}
    assert events["classified"]["trace_id"] == trace_id and events["email_pipeline"]["trace_id"] == trace_id


def test_audit_lines_have_no_trace_id_when_tracing_is_off(fake_classifier):
    with TestClient(app) as client:
        email_id = client.post("/emails/process", json={"email_text": EMAIL, "sender_email": "demo@example.com"}).json()["email_id"]
    lines = [l for l in (logging_config.LOG_DIR / "audit.log").read_text(encoding="utf-8").splitlines() if email_id in l]
    assert lines and not any('"trace_id"' in l for l in lines)


# ---------- the reviewer's decision becomes a quality signal ----------

def _review(client, body):
    email_id = client.post("/emails/process", json={"email_text": EMAIL, "sender_email": "demo@example.com"}).json()["email_id"]
    client.post(f"/emails/{email_id}/review", json=body)
    return email_id


def test_review_span_records_whether_and_how_much_the_draft_was_edited(spans, fake_classifier):
    with TestClient(app) as client:
        unchanged = _review(client, {"approved": True})
        edited = _review(client, {"approved": True, "edited_draft": "Subject: Re: Balance\n\nDear customer, totally rewritten text."})
        rejected = _review(client, {"approved": False, "feedback": "no"})

    reviews = {dict(s.attributes)["email.id"]: dict(s.attributes) for s in by_name(spans)["email.review"]}
    assert reviews[unchanged]["review.edited"] is False and reviews[unchanged]["review.edit_similarity"] == 1.0
    assert reviews[edited]["review.edited"] is True and reviews[edited]["review.edit_similarity"] < 0.8
    assert reviews[rejected]["review.approved"] is False and "review.edit_similarity" not in reviews[rejected]
    assert reviews[unchanged]["session.id"] == unchanged  # the review shares the email's session with its processing trace
    assert reviews[unchanged]["email.status"] == "approved" and reviews[unchanged]["review.has_feedback"] is False
