"""Find the statement period a customer asked for ("April 2026", "last month", "01/04/2026 to 30/04/2026").

Deterministic on purpose: a small LLM often fails to turn "April 2026" into dates. When the wording is
ambiguous this returns None, so the customer is asked instead of receiving the wrong period.
Numeric dates are day-first (DD/MM/YYYY), as is usual in India.
"""
import calendar
import re
from datetime import date

MONTHS = {
    "january": 1, "jan": 1, "february": 2, "feb": 2, "march": 3, "mar": 3, "april": 4, "apr": 4,
    "may": 5, "june": 6, "jun": 6, "july": 7, "jul": 7, "august": 8, "aug": 8,
    "september": 9, "sept": 9, "sep": 9, "october": 10, "oct": 10, "november": 11, "nov": 11,
    "december": 12, "dec": 12,
}
_M = "|".join(sorted(MONTHS, key=len, reverse=True))  # longest first, so "sept" is not read as "sep"
_ORD = r"(?:st|nd|rd|th)?"

_ISO = re.compile(r"\b(\d{4})-(\d{2})-(\d{2})\b")
_DMY = re.compile(r"\b(\d{1,2})[/.-](\d{1,2})[/.-](\d{4})\b")
_D_MON_Y = re.compile(rf"\b(\d{{1,2}}){_ORD}\s+({_M})\.?,?\s+(\d{{4}})\b", re.I)
_MON_D_Y = re.compile(rf"\b({_M})\.?\s+(\d{{1,2}}){_ORD},?\s+(\d{{4}})\b", re.I)
_MON_Y = re.compile(rf"\b({_M})\.?,?\s+(\d{{4}})\b", re.I)
_NUM_M_Y = re.compile(r"\b(\d{1,2})/(\d{4})\b")
_RELATIVE = re.compile(r"\b(last|previous|prior|this|current)\s+month\b", re.I)
# A bare month needs a word in front ("for April"), so the verb "may" or "march" is not mistaken for a month.
_BARE_MONTH = re.compile(rf"\b(?:for|of|in|during)\s+(?:the\s+month\s+of\s+)?({_M})\b(?!\s*\.?,?\s*\d)", re.I)


def _day(year, month, day) -> date | None:
    try:
        return date(int(year), int(month), int(day))
    except ValueError:
        return None


def _month(year: int, month: int) -> tuple[date, date]:
    return date(year, month, 1), date(year, month, calendar.monthrange(year, month)[1])


def _explicit_dates(text: str) -> list[date]:
    found: list[tuple[int, date | None]] = []
    for m in _ISO.finditer(text):
        found.append((m.start(), _day(m[1], m[2], m[3])))
    for m in _DMY.finditer(text):
        found.append((m.start(), _day(m[3], m[2], m[1])))
    for m in _D_MON_Y.finditer(text):
        found.append((m.start(), _day(m[3], MONTHS[m[2].lower()], m[1])))
    for m in _MON_D_Y.finditer(text):
        found.append((m.start(), _day(m[3], MONTHS[m[1].lower()], m[2])))
    return [d for _, d in sorted(found, key=lambda item: item[0]) if d]


def _usable(start: date, end: date, today: date) -> tuple[date, date] | None:
    if start > end:
        start, end = end, start
    if start.year < 2000 or start > today:
        return None  # nothing exists yet for a future period
    return start, min(end, today)  # the current month's statement runs up to today


def statement_period(text: str, today: date | None = None) -> tuple[date, date] | None:
    """(start, end) for the period asked for in `text`, or None if it is missing or ambiguous."""
    today = today or date.today()

    dates = _explicit_dates(text)
    if len(dates) >= 2:
        return _usable(min(dates), max(dates), today)
    if len(dates) == 1:
        return None  # "statement for 15 April 2026": one day is not a period, so ask

    if m := _MON_Y.search(text):
        return _usable(*_month(int(m[2]), MONTHS[m[1].lower()]), today)

    if m := _NUM_M_Y.search(text):
        if 1 <= int(m[1]) <= 12:
            return _usable(*_month(int(m[2]), int(m[1])), today)

    if m := _RELATIVE.search(text):
        if m[1].lower() in ("this", "current"):
            return _usable(today.replace(day=1), today, today)
        year, month = (today.year, today.month - 1) if today.month > 1 else (today.year - 1, 12)
        return _usable(*_month(year, month), today)

    if m := _BARE_MONTH.search(text):
        month = MONTHS[m[1].lower()]
        return _usable(*_month(today.year if month <= today.month else today.year - 1, month), today)

    return None
