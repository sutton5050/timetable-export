"""Read weekly course timetable PDFs and add them to Google Calendar.

Usage:
    .venv/bin/python timetable.py            add/update the current and future weeks
    .venv/bin/python timetable.py --preview  only print what the PDFs contain

Settings (calendar ID and timetable folder) live in config.json. Before
changing anything the script shows what it will add and remove and asks
"Go ahead? (y/n)".

How the PDF is laid out (one page per timetable):
  - Days run across the page as columns. Each column heading holds the
    date, e.g. "Mon 09 Nov 26".
  - Periods run down the page as rows (P1 - P2, P3 - P4, P5, ...). The
    left-hand column holds each row's label and its times, plus any
    day-specific exception such as "(15:10 - 16:40 Fri)".
  - Each row is a box split into a taller top part (the subject) and a
    shorter bottom part (the room).
"""

import json
import re
import sys
from dataclasses import dataclass
from datetime import datetime, date, time, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import pdfplumber

PROJECT_DIR = Path(__file__).resolve().parent
CONFIG_FILE = PROJECT_DIR / "config.json"
TIMEZONE = ZoneInfo("Europe/London")

# Grid lines in these PDFs are drawn as very thin filled rectangles.
THIN = 1.5      # anything thinner than this (in points) is a line
SAME_LINE = 2.0  # lines closer together than this are the same line

TIME_RANGE = re.compile(
    r"(\d{1,2}:\d{2})\s*-\s*(\d{1,2}:\d{2})(?:\s+(Mon|Tue|Wed|Thu|Fri|Sat|Sun))?"
)

# Times written inside a cell's own text, e.g. "12.30 - 13.15",
# "12:30-13:15" or "1300 to 1500 hrs". A time is either H:MM / H.MM, or
# exactly four digits; that keeps lesson numbers like "(121-122)" from
# being mistaken for times.
_CLOCK = r"(?<![\d:.])(\d{1,2}[:.]\d{2}|\d{4})(?![\d:.]\d)"
TEXT_TIME_RANGE = re.compile(_CLOCK + r"\s*(?:-|–|to)\s*" + _CLOCK)


@dataclass
class Event:
    start: datetime
    end: datetime
    title: str
    location: str
    period: str  # e.g. "P6 - P7", handy for checking against the PDF
    times_from_text: bool = False  # True when the cell's own text gave the times


@dataclass
class Row:
    label: str            # e.g. "P1 - P2"
    top: float
    split: float          # where the subject part ends and the room part begins
    bottom: float
    times: dict           # weekday ("Mon".."Fri" or "default") -> (start, end)


def _cluster(values):
    """Merge positions that are within SAME_LINE of each other."""
    merged = []
    for v in sorted(values):
        if merged and v - merged[-1][-1] <= SAME_LINE:
            merged[-1].append(v)
        else:
            merged.append([v])
    return [sum(group) / len(group) for group in merged]


def _text(page, x0, top, x1, bottom):
    """All text fully inside a box, joined onto one line."""
    found = page.within_bbox((x0, top, x1, bottom)).extract_text() or ""
    return " ".join(found.split())


def _horizontal_lines(page):
    return [r for r in page.rects if r["height"] < THIN and r["width"] >= THIN]


def _has_line_at(lines, y, x0, x1):
    """Is there a horizontal grid line at height y across the column x0-x1?"""
    middle = (x0 + x1) / 2
    return any(
        abs((r["top"] + r["bottom"]) / 2 - y) <= SAME_LINE and r["x0"] <= middle <= r["x1"]
        for r in lines
    )


def _parse_times(text):
    times = {}
    for start, end, day in TIME_RANGE.findall(text):
        key = day or "default"
        times.setdefault(key, (time.fromisoformat(start.zfill(5)), time.fromisoformat(end.zfill(5))))
    return times


def _clock(text):
    """'12.30', '12:30' or '1230' -> time, or None if it isn't a real time."""
    digits = text.replace(":", "").replace(".", "").zfill(4)
    hour, minute = int(digits[:2]), int(digits[2:])
    if hour > 23 or minute > 59:
        return None
    return time(hour, minute)


def _times_in_text(text):
    """Find time ranges written in a cell's text.

    Returns (start, end) or None. If several ranges are written (e.g.
    "1300 to 1500 hrs and 1500 to 1700 hrs") the event runs from the
    earliest start to the latest end.
    """
    ranges = []
    for match in TEXT_TIME_RANGE.finditer(text):
        start_text, end_text = match.groups()
        # "(1001-1002)" is a lesson number in brackets, not a time.
        bracketed = text[match.start() - 1:match.start()] == "(" and text[match.end():match.end() + 1] == ")"
        if bracketed and start_text.isdigit() and end_text.isdigit():
            continue
        start, end = _clock(start_text), _clock(end_text)
        if start and end and start < end:
            ranges.append((start, end))
    if not ranges:
        return None
    return min(s for s, _ in ranges), max(e for _, e in ranges)


def parse_timetable(path):
    """Return (events, warnings) for one timetable PDF."""
    events, warnings = [], []

    with pdfplumber.open(path) as pdf:
        if len(pdf.pages) != 1:
            warnings.append(f"expected 1 page, found {len(pdf.pages)}; only reading the first")
        page = pdf.pages[0]

        # Column edges come from the vertical grid lines.
        verticals = [r for r in page.rects if r["width"] < THIN and r["height"] >= THIN]
        col_edges = _cluster([(r["x0"] + r["x1"]) / 2 for r in verticals])
        if len(col_edges) < 3:
            raise ValueError("could not find the timetable grid")
        label_x0, label_x1 = col_edges[0], col_edges[1]

        # Row edges come from the horizontal lines crossing the label column.
        horizontals = _horizontal_lines(page)
        row_edges = _cluster(
            (r["top"] + r["bottom"]) / 2
            for r in horizontals
            if r["x0"] < label_x1 and r["x1"] > label_x0
        )
        header_top, header_bottom = row_edges[0], row_edges[1]

        # Day columns: read the date from each heading.
        days = []  # (x0, x1, date, "Mon")
        for x0, x1 in zip(col_edges[1:], col_edges[2:]):
            heading = _text(page, x0, header_top, x1, header_bottom)
            try:
                day_date = datetime.strptime(heading, "%a %d %b %y").date()
            except ValueError:
                warnings.append(f"could not read a date from column heading {heading!r}")
                continue
            days.append((x0, x1, day_date, heading.split()[0]))

        # Period rows: label, times and where the subject/room split is.
        rows = []
        for top, bottom in zip(row_edges[1:], row_edges[2:]):
            # The label column's shaded boxes show where the room part starts.
            boxes = [
                r for r in page.rects
                if r["width"] >= THIN and r["height"] >= THIN
                and r["x0"] <= label_x0 + SAME_LINE and r["x1"] >= label_x1 - SAME_LINE
                and r["top"] >= top - SAME_LINE and r["bottom"] <= bottom + SAME_LINE
            ]
            inner_tops = [r["top"] for r in boxes if r["top"] > top + SAME_LINE]
            split = max(inner_tops) if inner_tops else bottom

            label = _text(page, label_x0, top, label_x1, split)
            times = _parse_times(_text(page, label_x0, top, label_x1, bottom))
            if "default" not in times:
                warnings.append(f"row {label!r}: no times found, skipping it")
                continue
            rows.append(Row(label, top, split, bottom, times))

        # Each day x period cell becomes one event.
        for x0, x1, day_date, weekday in days:
            for i, row in enumerate(rows):
                if i > 0 and not _has_line_at(horizontals, row.top, x0, x1):
                    warnings.append(
                        f"{weekday} {day_date}: the box for {row.label} seems joined to the "
                        f"one above it; check this day by hand"
                    )
                title = _text(page, x0, row.top, x1, row.split)
                location = _text(page, x0, row.split, x1, row.bottom)
                if not title and not location:
                    continue
                if not title:
                    warnings.append(f"{weekday} {day_date} {row.label}: room {location!r} but no subject")
                    title = location
                start, end = row.times.get(weekday, row.times["default"])
                written = _times_in_text(f"{title} {location}")
                if written:
                    start, end = written
                events.append(Event(
                    start=datetime.combine(day_date, start, TIMEZONE),
                    end=datetime.combine(day_date, end, TIMEZONE),
                    title=title,
                    location=location,
                    period=row.label,
                    times_from_text=bool(written),
                ))

    events.sort(key=lambda e: e.start)
    return events, warnings


def format_event(e):
    where = f"  @ {e.location}" if e.location else ""
    note = "  (times taken from the text)" if e.times_from_text else ""
    return (
        f"{e.start:%a %d %b}  {e.start:%H:%M}-{e.end:%H:%M}  "
        f"{e.title}{where}{note}"
    )


def load_config():
    with open(CONFIG_FILE) as f:
        return json.load(f)


def issue_key(path):
    """Files start with the date they were issued, e.g. 20261023-...; newest wins."""
    match = re.match(r"(\d{8})", path.name)
    return (match.group(1) if match else "", path.stat().st_mtime)


def read_weeks(folder):
    """Parse every PDF and keep the newest file for each week.

    Returns {monday: (path, events, warnings)} and a list of problems.
    """
    weeks, problems = {}, []
    for path in sorted(folder.glob("*.pdf"), key=issue_key):
        try:
            events, warnings = parse_timetable(path)
        except Exception as error:  # keep going with the other files
            problems.append(f"{path.name}: could not read it ({error})")
            continue
        if not events:
            problems.append(f"{path.name}: no events found")
            continue
        for w in warnings:
            problems.append(f"{path.name}: {w}")
        first = min(e.start.date() for e in events)
        monday = first - timedelta(days=first.weekday())
        if monday in weeks:
            problems.append(f"{weeks[monday][0].name} is replaced by the newer {path.name}")
        weeks[monday] = (path, events, warnings)
    return dict(sorted(weeks.items())), problems


def preview(folder):
    weeks, problems = read_weeks(folder)
    for monday, (path, events, _) in weeks.items():
        print(f"\n=== Week of {monday:%a %d %b %Y}  ({path.name})")
        for e in events:
            print("  " + format_event(e))
        print(f"  {len(events)} events")
    for p in problems:
        print(f"NOTE: {p}")


def sync(folder, calendar_id):
    import google_calendar  # only needed when talking to Google

    weeks, problems = read_weeks(folder)
    for p in problems:
        print(f"NOTE: {p}")

    now = datetime.now(TIMEZONE)
    current = {m: w for m, w in weeks.items() if max(e.end for e in w[1]) > now}
    skipped = len(weeks) - len(current)
    if skipped:
        print(f"Skipping {skipped} week(s) that are already over.")
    if not current:
        print("Nothing to do: no current or future weeks found.")
        return

    service = google_calendar.connect(PROJECT_DIR)

    # Work out the changes for each week before touching anything.
    plan = []  # (monday, path, events, existing)
    for monday, (path, events, _) in current.items():
        existing = google_calendar.events_for_week(service, calendar_id, monday)
        if google_calendar.same_events(existing, events):
            print(f"\nWeek of {monday:%d %b %Y}: already up to date ({path.name})")
            continue
        plan.append((monday, path, events, existing))

    if not plan:
        print("\nYour calendar is already up to date.")
        return

    for monday, path, events, existing in plan:
        print(f"\n=== Week of {monday:%a %d %b %Y}  ({path.name})")
        if existing:
            print(f"  Remove {len(existing)} event(s) previously added for this week")
        print(f"  Add {len(events)} event(s):")
        for e in events:
            print("    " + format_event(e))

    answer = input("\nGo ahead? (y/n) ").strip().lower()
    if answer not in ("y", "yes"):
        print("Cancelled. Nothing was changed.")
        return

    for monday, path, events, existing in plan:
        google_calendar.replace_week(service, calendar_id, monday, path.name, events, existing)
        print(f"Week of {monday:%d %b %Y}: done")
    print("\nAll done.")


def main():
    config = load_config()
    folder = Path(config["timetable_folder"])
    if not folder.is_dir():
        sys.exit(f"Timetable folder not found: {folder}")
    if "--preview" in sys.argv[1:]:
        preview(folder)
    else:
        sync(folder, config["calendar_id"])


if __name__ == "__main__":
    main()
