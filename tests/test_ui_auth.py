from pathlib import Path

from fastapi.testclient import TestClient

from main import app

UI = Path(__file__).resolve().parent.parent / "ui" / "index.html"


def test_open_when_no_token_is_configured():
    with TestClient(app) as client:
        assert client.get("/emails").status_code == 200


def test_token_is_required_when_configured(monkeypatch):
    monkeypatch.setenv("REVIEWER_TOKEN", "s3cret-token")
    with TestClient(app) as client:
        assert client.get("/emails").status_code == 401
        assert client.get("/emails", headers={"X-Reviewer-Token": "wrong"}).status_code == 401
        assert client.get("/emails", headers={"X-Reviewer-Token": "s3cret-token"}).status_code == 200

        # every action endpoint is protected, not just the list
        assert client.post("/emails/process", json={"email_text": "x", "sender_email": "demo@example.com"}).status_code == 401
        assert client.post("/emails/mailbox/poll").status_code == 401
        assert client.post("/emails/anything/review", json={"approved": True}).status_code == 401

        # the page and the health check stay reachable (the page holds no data)
        assert client.get("/health").status_code == 200
        assert client.get("/ui").status_code == 200


def test_non_ascii_token_does_not_crash(monkeypatch):
    monkeypatch.setenv("REVIEWER_TOKEN", "s3cret-token")
    with TestClient(app) as client:
        assert client.get("/emails", headers={"X-Reviewer-Token": "tékén".encode("latin-1")}).status_code == 401


def test_ui_page_is_served():
    with TestClient(app) as client:
        response = client.get("/ui")
    assert response.status_code == 200 and "text/html" in response.headers["content-type"]
    assert "Reviewer Console" in response.text


def test_ui_never_writes_untrusted_text_as_html():
    """Emails and drafts come from customers: the page must only use textContent."""
    source = UI.read_text(encoding="utf-8")
    for dangerous in ("innerHTML", "outerHTML", "insertAdjacentHTML", "document.write", "eval("):
        assert dangerous not in source, dangerous
