import shutil

import pytest
from fastapi.testclient import TestClient

import agents.email_workflow as wf
import apis.email_api as email_api
import utils.email_reader as reader
from agents.email_agent import extract_entities
from main import app
from scripts.make_sample_inbox import SAMPLES, make_text_pdf
from utils.email_reader import Attachment, clean_text, extract_attachment_text, parse_eml, process_attachments


def test_clean_text_drops_quotes_signature_and_thread():
    cleaned = clean_text(
        "Balance please.\n\n> quoted\nThanks\n--\nSignature\n\nOn Mon, X wrote:\nold"
    )
    assert cleaned == "Balance please.\n\nThanks"


def test_parse_eml_reads_sender_subject_body_attachment():
    parsed = parse_eml(SAMPLES["02_balance_number_only_in_pdf.eml"])
    assert parsed.sender == "demo@example.com"
    assert parsed.email_text.startswith("Subject: Please share my balance")
    assert [a.filename for a in parsed.attachments] == ["account_proof.pdf"]

    quoted = parse_eml(SAMPLES["01_balance_with_reply_and_signature.eml"])
    assert "999999999" not in quoted.body and "Sent from my phone" not in quoted.body


def test_pdf_text_is_extracted():
    text = extract_attachment_text(Attachment("a.pdf", "application/pdf", make_text_pdf("Account Number: 1234567890")))
    assert "1234567890" in text


def test_unsupported_and_image_without_ocr():
    with pytest.raises(ValueError, match="unsupported"):
        extract_attachment_text(Attachment("a.zip", "application/zip", b"x"))

    png = _tiny_png()
    if shutil.which("tesseract") is None:
        with pytest.raises(ValueError, match="not installed"):
            extract_attachment_text(Attachment("a.png", "image/png", png))
    else:
        extract_attachment_text(Attachment("a.png", "image/png", png))  # must not raise


def _tiny_png() -> bytes:
    import io

    from PIL import Image

    buffer = io.BytesIO()
    Image.new("RGB", (20, 20), "white").save(buffer, "PNG")
    return buffer.getvalue()


def test_process_attachments_saves_safely_and_skips_bad_ones(tmp_path, monkeypatch):
    monkeypatch.setattr(reader, "ATTACHMENT_DIR", tmp_path)
    good = Attachment("../../evil name.pdf", "application/pdf", make_text_pdf("Account Number: 1234567890"))
    corrupt = Attachment("broken.pdf", "application/pdf", b"not a pdf")

    text = process_attachments("email-1", [good, corrupt])

    assert "1234567890" in text
    saved = list((tmp_path / "email-1").iterdir())
    assert len(saved) == 1 and saved[0].parent == tmp_path / "email-1"  # no path escape, bad file skipped


def test_attachment_number_is_only_a_fallback():
    assert extract_entities("send balance", {}, "Account Number: 1234567890")["account_number"] == "1234567890"
    both = extract_entities("balance for account 1111111111", {}, "Account Number: 1234567890")
    assert both["account_number"] == "1111111111"  # the email body wins


def test_poll_inbox_end_to_end(tmp_path, monkeypatch):
    inbox = tmp_path / "inbox"
    inbox.mkdir()
    for name in ("02_balance_number_only_in_pdf.eml", "03_unknown_sender.eml"):
        (inbox / name).write_bytes(SAMPLES[name])
    monkeypatch.setattr(email_api, "INBOX_DIR", inbox)
    monkeypatch.setattr(reader, "ATTACHMENT_DIR", tmp_path / "attachments")

    seen = []

    def fake_intent(text, attachment_text=""):
        entities = extract_entities(text, {}, attachment_text)
        seen.append(entities)
        return {"intents": ["check_balance"], "sentiment": "neutral", "entities": entities}

    monkeypatch.setattr(wf, "identify_intent", fake_intent)

    with TestClient(app) as client:
        body = client.post("/emails/inbox/poll").json()

    by_file = {r["file"]: r for r in body["results"]}
    assert by_file["02_balance_number_only_in_pdf.eml"]["status"] == "pending_review"
    assert by_file["03_unknown_sender.eml"]["error"] == "Sender identity could not be verified."
    assert seen[0]["account_number"] == "1234567890"  # came from the PDF; the body has no number
    assert (inbox / "processed" / "02_balance_number_only_in_pdf.eml").exists()
    assert (inbox / "failed" / "03_unknown_sender.eml").exists()
    assert not list(inbox.glob("*.eml"))
