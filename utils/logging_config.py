import json
import logging
import re
from logging.handlers import RotatingFileHandler
from pathlib import Path

LOG_DIR = Path(__file__).resolve().parent.parent / "logs"

# 4567XXXX8901 -> XXXX8901
_MASKED_CARD = re.compile(r"\b\d{4}[Xx*]{4}(\d{4})\b")
# Standalone 6-19 digit numbers (accounts, cards), not money such as 25000.00 or 1,25,000,
# and not digits inside larger tokens such as UUID email_ids.
# ponytail: a whole-number amount of 6+ digits (e.g. 125000) is also masked; ask the LLM for 2 decimals.
_LONG_NUMBER = re.compile(r"(?<![\w.,-])\d{6,19}(?![\w-]|[.,]\d)")


def mask_text(text: str) -> str:
    """Keep only the last 4 digits of account/card numbers."""
    text = _MASKED_CARD.sub(r"XXXX\1", text)
    return _LONG_NUMBER.sub(lambda m: "XXXX" + m.group()[-4:], text)


def mask_value(value):
    """mask_text applied to every string inside nested dicts/lists."""
    if isinstance(value, str):
        return mask_text(value)
    if isinstance(value, dict):
        return {k: mask_value(v) for k, v in value.items()}
    if isinstance(value, list):
        return [mask_value(v) for v in value]
    return value


class _MaskingFilter(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        record.msg = mask_text(record.getMessage())
        record.args = ()
        return True


def audit(event: str, **fields) -> None:
    """Append one masked JSON line to logs/audit.log (with the trace id when tracing is on)."""
    from utils.tracing import current_trace_id  # imported here: tracing itself imports this module

    trace_id = current_trace_id()
    logging.getLogger("audit").info(
        json.dumps({"event": event, **mask_value(fields), **({"trace_id": trace_id} if trace_id else {})}, default=str) # type: ignore
    )


def setup_logging() -> None:
    """Console + logs/app.log for the app, logs/audit.log for the audit trail. Safe to call twice."""
    root = logging.getLogger()
    if getattr(root, "_email_agent_configured", False):
        return
    root._email_agent_configured = True  # type: ignore[attr-defined]

    pii_filter = _MaskingFilter()

    def handler(h: logging.Handler, fmt: str) -> logging.Handler:
        h.setFormatter(logging.Formatter(fmt))
        h.addFilter(pii_filter)
        return h

    def file_handler(name: str, backups: int, fmt: str) -> logging.Handler | None:
        """A rotating log file, or None if the folder is not writable (the service must still start)."""
        try:
            LOG_DIR.mkdir(exist_ok=True)
            return handler(
                RotatingFileHandler(LOG_DIR / name, maxBytes=5_000_000, backupCount=backups, encoding="utf-8"), fmt
            )
        except OSError as exc:
            logging.getLogger(__name__).warning("Cannot write %s (%s); logging to the console only", name, exc)
            return None

    app_fmt = "%(asctime)s %(levelname)s %(name)s: %(message)s"
    audit_fmt = "%(asctime)s %(message)s"
    root.setLevel(logging.INFO)
    root.addHandler(handler(logging.StreamHandler(), app_fmt))
    if app_file := file_handler("app.log", 3, app_fmt):
        root.addHandler(app_file)

    audit_logger = logging.getLogger("audit")
    audit_logger.propagate = False  # the audit trail goes only to its own file ...
    audit_logger.setLevel(logging.INFO)
    # ... or to the console if that file cannot be written, so audit lines are never silently dropped
    audit_logger.addHandler(file_handler("audit.log", 10, audit_fmt) or handler(logging.StreamHandler(), audit_fmt))
