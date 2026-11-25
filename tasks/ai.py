"""Natural-language helpers (rule-based, version 1).

SWAPPABLE CONTRACT -- keep these signatures when you replace the bodies with an LLM call:

    parse_task_text(text: str, today: datetime.date) -> dict
        returns {"title": str, "description": str, "due_date": "YYYY-MM-DD" | None,
                 "recurrence": "daily" | "weekly" | "monthly" | "yearly" | None}

    break_down_task(task: Task) -> list[dict]
        returns a list of {"title", "description", "priority", "due_date"} dicts

The views never trust what comes back: every dict goes through TaskSerializer before saving.
"""
import calendar
import re
from datetime import date, timedelta

WEEKDAYS = {
    name: index
    for index, name in enumerate(
        ["monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday"]
    )
}
MONTHS = {}
for _i, _name in enumerate(calendar.month_name):
    if _name:
        MONTHS[_name.lower()] = _i
        MONTHS[_name[:3].lower()] = _i
MONTHS["sept"] = 9

_WEEKDAY_RE = "|".join(WEEKDAYS)
_MONTH_RE = "|".join(sorted(MONTHS, key=len, reverse=True))

LEAD_INS = re.compile(
    r"^\s*(please\s+)?(remind me to|remind me|remember to|don'?t forget to|i need to|i have to|i must|i should|need to)\s+",
    re.IGNORECASE,
)
DANGLING = re.compile(r"\b(on|by|at|the|of|in|from|before|until|due|for|every|each|next|this)\s*$", re.IGNORECASE)


def _cut(pattern, text):
    """Find `pattern` (case-insensitive). Return (match, text_without_the_match)."""
    match = re.search(pattern, text, re.IGNORECASE)
    if not match:
        return None, text
    return match, text[: match.start()] + " " + text[match.end():]


def _add_months(start, months):
    month_index = start.month - 1 + months
    year = start.year + month_index // 12
    month = month_index % 12 + 1
    day = min(start.day, calendar.monthrange(year, month)[1])
    return date(year, month, day)


def _next_day_of_month(today, day):
    """Next date (today included) whose day-of-month is `day`. Skips months that are too short."""
    year, month = today.year, today.month
    for _ in range(24):
        if day <= calendar.monthrange(year, month)[1]:
            candidate = date(year, month, day)
            if candidate >= today:
                return candidate
        month += 1
        if month > 12:
            month, year = 1, year + 1
    return None


def _next_weekday(today, weekday):
    ahead = (weekday - today.weekday()) % 7
    return today + timedelta(days=ahead or 7)


def _next_month_day(today, month, day):
    """Next occurrence of month/day. If the date is impossible (feb 30) hand back the bad
    string on purpose, so the serializer rejects it and the API answers 502."""
    for year in (today.year, today.year + 1):
        try:
            candidate = date(year, month, day)
        except ValueError:
            return f"{today.year}-{month:02d}-{day:02d}"
        if candidate >= today:
            return candidate
    return None


def parse_task_text(text, today):
    if not isinstance(text, str) or not text.strip():
        raise ValueError("text must be a non-empty string")

    original = " ".join(text.split())
    work = original
    due = None
    recurrence = None
    recurring_weekday = None

    # ---- recurrence -------------------------------------------------------------
    match, work = _cut(rf"\bevery\s+({_WEEKDAY_RE})\b", work)
    if match:
        recurrence = "weekly"
        recurring_weekday = WEEKDAYS[match.group(1).lower()]
    if not recurrence:
        for pattern, value in [
            (r"\b(?:of\s+)?(?:every|each)\s+month\b|\bmonthly\b", "monthly"),
            (r"\b(?:every|each)\s+week\b|\bweekly\b", "weekly"),
            (r"\b(?:every|each)\s+(?:day|weekday)\b|\bdaily\b|\beveryday\b", "daily"),
            (r"\b(?:every|each)\s+year\b|\byearly\b|\bannually\b", "yearly"),
        ]:
            match, work = _cut(pattern, work)
            if match:
                recurrence = value
                break

    # ---- due date ---------------------------------------------------------------
    match, work = _cut(r"\b\d{4}-\d{2}-\d{2}\b", work)
    if match:
        due = match.group(0)  # left as text: the serializer decides if it is a real date

    if due is None:
        match, work = _cut(r"\bday after tomorrow\b", work)
        if match:
            due = today + timedelta(days=2)
    if due is None:
        match, work = _cut(r"\b(?:tomorrow|tmrw)\b", work)
        if match:
            due = today + timedelta(days=1)
    if due is None:
        match, work = _cut(r"\b(?:today|tonight)\b", work)
        if match:
            due = today
    if due is None:
        match, work = _cut(r"\bin\s+(\d+)\s+(day|week|month)s?\b", work)
        if match:
            amount, unit = int(match.group(1)), match.group(2).lower()
            if unit == "day":
                due = today + timedelta(days=amount)
            elif unit == "week":
                due = today + timedelta(weeks=amount)
            else:
                due = _add_months(today, amount)
    if due is None:
        match, work = _cut(r"\bnext\s+week\b", work)
        if match:
            due = today + timedelta(days=7)
    if due is None:
        match, work = _cut(r"\bnext\s+month\b", work)
        if match:
            due = _add_months(today, 1)
    if due is None:
        match, work = _cut(rf"\b(?:on\s+)?(\d{{1,2}})(?:st|nd|rd|th)?\s+(?:of\s+)?({_MONTH_RE})\b", work)
        if match:
            due = _next_month_day(today, MONTHS[match.group(2).lower()], int(match.group(1)))
    if due is None:
        match, work = _cut(rf"\b(?:on\s+)?({_MONTH_RE})\s+(\d{{1,2}})(?:st|nd|rd|th)?\b", work)
        if match:
            due = _next_month_day(today, MONTHS[match.group(1).lower()], int(match.group(2)))
    if due is None:
        match, work = _cut(rf"\b(?:on\s+|next\s+|this\s+)?({_WEEKDAY_RE})\b", work)
        if match:
            due = _next_weekday(today, WEEKDAYS[match.group(1).lower()])
    if due is None:
        match, work = _cut(r"\b(?:on\s+)?(?:the\s+)?(\d{1,2})(?:st|nd|rd|th)\b", work)
        if match:
            due = _next_day_of_month(today, int(match.group(1)))
    if due is None and recurring_weekday is not None:
        due = _next_weekday(today, recurring_weekday)

    if isinstance(due, date):
        due = due.isoformat()

    # ---- title ------------------------------------------------------------------
    title = " ".join(work.split())
    title = LEAD_INS.sub("", title)
    previous = None
    while previous != title:  # peel dangling words like "on", "by" left behind
        previous = title
        title = DANGLING.sub("", title).strip(" ,.;:-")
    title = title[:1].upper() + title[1:] if title else ""

    result = {"title": title, "description": original, "due_date": due}
    if recurrence:
        result["recurrence"] = recurrence
    return result


# ----------------------------------------------------------------------------------
# Breakdown
# ----------------------------------------------------------------------------------
TEMPLATES = [
    (("pay", "bill", "rent", "invoice"), ["Check the amount due", "Make the payment", "Save the receipt"]),
    (("write", "report", "essay", "article", "blog"), ["Outline the main points", "Write the first draft", "Review and edit", "Share the final version"]),
    (("buy", "shop", "order", "purchase"), ["Make a list of what is needed", "Compare options and prices", "Place the order or buy it"]),
    (("trip", "travel", "flight", "vacation"), ["Pick dates and budget", "Book tickets and stay", "Pack and prepare documents"]),
    (("clean", "tidy", "organize", "organise"), ["Gather supplies", "Do the main cleaning or sorting", "Put everything back in place"]),
    (("study", "learn", "revise", "exam"), ["Collect the study material", "Study one topic at a time", "Practice and self-test"]),
]


def break_down_task(task):
    title = (task.title or "").strip()
    pieces = [p.strip(" .") for p in re.split(r",|;|\band\b|\bthen\b", title, flags=re.IGNORECASE) if p.strip(" .")]

    if len(pieces) < 2:
        lowered = title.lower()
        steps = None
        for keywords, template in TEMPLATES:
            if any(re.search(rf"\b{k}", lowered) for k in keywords):
                steps = template
                break
        if steps is None:
            steps = [f"Plan: {title}", f"Do: {title}", f"Review: {title}"]
        pieces = steps

    due = task.due_date.isoformat() if task.due_date else None
    return [
        {
            "title": piece[:1].upper() + piece[1:],
            "description": f"Step {index} of '{title}'",
            "priority": task.priority,
            "due_date": due,
        }
        for index, piece in enumerate(pieces, start=1)
    ]
