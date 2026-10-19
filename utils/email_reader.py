import html
import io
import logging
import re
from dataclasses import dataclass, field
from email import policy
from email.parser import BytesParser
from email.utils import parseaddr
from pathlib import Path

from utils.logging_config import audit

logger = logging.getLogger(__name__)

PROJECT_ROOT = Path(__file__).resolve().parent.parent
# Simulated mailbox: drop .eml files into INBOX_DIR and call POST /emails/inbox/poll.
INBOX_DIR = PROJECT_ROOT / "data" / "inbox"
ATTACHMENT_DIR = PROJECT_ROOT / "data" / "attachments"

MAX_ATTACHMENT_BYTES = 10 * 1024 * 1024
MAX_ATTACHMENT_TEXT_CHARS = 20_000


@dataclass
class Attachment:
    filename: str
    content_type: str
    data: bytes


@dataclass
class ParsedEmail:
    sender: str | None
    subject: str
    body: str
    attachments: list[Attachment] = field(default_factory=list)
    message_id: str | None = None
    automated: bool = False  # bounce, no-reply, auto-responder: never answer these
    authenticated: bool = False  # Gmail verified the sender's domain (DKIM/SPF)

    @property
    def email_text(self) -> str:
        """Subject + cleaned body; the subject often carries the request."""
        return f"Subject: {self.subject}\n\n{self.body}" if self.subject else self.body


def clean_text(text: str) -> str:
    """Drop quoted replies and signatures, normalise whitespace."""
    lines = []
    for line in text.replace("\r\n", "\n").split("\n"):
        stripped = line.strip()
        if stripped.startswith(">"):
            continue
        if re.match(r"^On .+ wrote:$", stripped) or stripped == "--":
            break  # everything below is the previous thread or a signature
        lines.append(line.rstrip())
    return re.sub(r"\n{3,}", "\n\n", "\n".join(lines)).strip()


def _domains_match(a: str, b: str) -> bool:
    return a == b or a.endswith("." + b) or b.endswith("." + a)


def _sender_authenticated(message, from_domain: str) -> bool:
    """True if Gmail's own Authentication-Results show DKIM or SPF passing for the From domain.

    The From header alone is trivially forged. Only headers added by our receiving server
    (mx.google.com) are trusted; any other Authentication-Results header came from the sender.
    """
    for header in message.get_all("Authentication-Results", []):
        text = str(header).lower().strip()
        if not text.startswith("mx.google.com"):
            continue
        for match in re.finditer(
            r"dkim=pass[^;]*?header\.[di]=@?([\w.-]+)|spf=pass[^;]*?smtp\.mailfrom=(?:[^\s;@]*@)?([\w.-]+)",
            text,
        ):
            if _domains_match(match.group(1) or match.group(2), from_domain):
                return True
    return False


def _is_automated(message, sender: str | None) -> bool:
    auto_submitted = (message.get("Auto-Submitted") or "no").strip().lower() != "no"
    bulk = (message.get("Precedence") or "").strip().lower() in {"bulk", "junk", "list"}
    local_part = (sender or "").split("@")[0]
    return auto_submitted or bulk or local_part in {
        "mailer-daemon", "postmaster", "noreply", "no-reply", "donotreply",
    }


def parse_eml(raw: bytes) -> ParsedEmail:
    message = BytesParser(policy=policy.default).parsebytes(raw)

    body_part = message.get_body(preferencelist=("plain", "html"))
    body = body_part.get_content() if body_part else ""
    if body_part is not None and body_part.get_content_type() == "text/html":
        body = html.unescape(re.sub(r"<[^>]+>", " ", body))

    attachments = [
        Attachment(
            filename=part.get_filename() or "attachment",
            content_type=part.get_content_type(),
            data=part.get_payload(decode=True) or b"", # type: ignore
        )
        for part in message.iter_attachments()
    ]

    sender = parseaddr(message.get("From", ""))[1].strip().lower() or None
    return ParsedEmail(
        sender,
        (message.get("Subject") or "").strip(),
        clean_text(body),
        attachments,
        message_id=(message.get("Message-ID") or "").strip() or None,
        automated=_is_automated(message, sender),
        authenticated=bool(sender) and _sender_authenticated(message, sender.split("@")[-1]),  # type: ignore[union-attr]
    )


def _safe_name(filename: str, index: int) -> str:
    name = re.sub(r"[^\w.\-]", "_", Path(filename).name) or "attachment"
    return f"{index}_{name}"  # index avoids clashes; Path(...).name blocks ../ tricks


def extract_attachment_text(attachment: Attachment) -> str:
    """Text from a PDF or an image. Raises ValueError (customer-safe reason) if unsupported."""
    kind = attachment.content_type.lower()

    if kind == "application/pdf":
        from pypdf import PdfReader

        reader = PdfReader(io.BytesIO(attachment.data))
        return "\n".join((page.extract_text() or "") for page in reader.pages)

    if kind.startswith("image/"):
        import pytesseract
        from PIL import Image

        try:
            return pytesseract.image_to_string(Image.open(io.BytesIO(attachment.data)))
        except pytesseract.TesseractNotFoundError as exc:
            raise ValueError("OCR engine (Tesseract) is not installed") from exc

    raise ValueError(f"unsupported attachment type {kind}")


def process_attachments(email_id: str, attachments: list[Attachment]) -> str:
    """Save each attachment to disk and return all extracted text combined.

    A bad attachment is skipped (and audited); it never fails the email.
    """
    texts = []
    folder = ATTACHMENT_DIR / email_id

    for index, attachment in enumerate(attachments, start=1):
        info = {"email_id": email_id, "filename": attachment.filename, "type": attachment.content_type}

        if len(attachment.data) > MAX_ATTACHMENT_BYTES:
            audit("attachment_skipped", reason="too large", size=len(attachment.data), **info)
            continue

        try:
            text = extract_attachment_text(attachment)
        except Exception as exc:  # noqa: BLE001  (corrupt PDF, unreadable image, no OCR...)
            audit("attachment_skipped", reason=str(exc), **info)
            continue

        folder.mkdir(parents=True, exist_ok=True)
        (folder / _safe_name(attachment.filename, index)).write_bytes(attachment.data)
        audit("attachment_saved", size=len(attachment.data), chars_extracted=len(text.strip()), **info)
        texts.append(text.strip())

    return "\n\n".join(t for t in texts if t)[:MAX_ATTACHMENT_TEXT_CHARS]
