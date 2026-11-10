# Timetable Export

Reads weekly course timetable PDFs from a folder and adds the lessons to a
dedicated Google Calendar. It is a plain Python script: no AI, no server,
run by hand whenever a new timetable arrives.

```
Time tables/                         Google Calendar
  ...wc 09 Nov 26.pdf   ──┐          ┌─ "Work Timetable" calendar
  ...wc 16 Nov 26.pdf   ──┼─ script ─┤    Mon 08:30 Communication Principles (17-18) @ F110
  ...wc 23 Nov 26.pdf   ──┘          └─   ...
```

## What it does

- Reads every PDF timetable in a folder (one page, one week each).
- Turns each filled cell into a calendar event: the subject becomes the title
  (e.g. `Communication Principles (17-18)`) and the room the location (e.g. `F110`).
- Puts events only in a separate calendar, so a calendar shared with family
  stays uncluttered.
- Skips weeks that are already over.
- Handles revised timetables: if a newer PDF for the same week appears, that
  week's events are replaced.
- Shows exactly what it will add and remove and asks `Go ahead? (y/n)`
  before changing anything. Re-running with nothing new changes nothing.

## How it works

### 1. Reading the PDF (`timetable.py`)

The timetables are a grid, which is why simply extracting the text gives a
jumbled result. Instead the script uses [pdfplumber](https://github.com/jsvine/pdfplumber)
to get the position of every word and every grid line on the page:

```
            │ Mon 09 Nov 26 │ Tue 10 Nov 26 │ ...      ← column headings give the dates
────────────┼───────────────┼───────────────┤
 P1 - P2    │ Communication │ Management of │          ← top part of a box: the subject
            │ Principles    │ Information   │
 08:30-10:00│ F110          │ S099          │          ← bottom part: the room
────────────┼───────────────┼───────────────┤
 ...        │               │               │
 P8 - P9    │               │               │
 15:30-17:00│               │               │
 (15:10 - 16:40 Fri)                                  ← day-specific exception
```

1. **Columns:** the vertical grid lines mark the day columns. Each heading
   (`Mon 09 Nov 26`) is read as a date.
2. **Rows:** the horizontal lines across the left-hand column mark the period
   rows. The times are read from that column rather than being hard-coded,
   including exceptions such as `(15:10 - 16:40 Fri)`.
3. **Cells:** each row's box is split into a taller top part (subject) and a
   shorter bottom part (room). The split is found from the shaded boxes in the
   left-hand column. Text in the top part becomes the title, text in the
   bottom part the location. Empty cells are skipped.
4. **Times written in the text:** some one-off events state their own times,
   e.g. `Financial Resilience Brief 12.30 - 13.15` or
   `Jobs Fair 1300 to 1500 hrs and 1500 to 1700 hrs`. When a cell contains a
   time range like this, those times are used instead of the slot's times
   (several ranges → earliest start to latest end). Lesson numbers in brackets
   such as `(121-122)` are not mistaken for times.
5. **Warnings:** anything odd (a box that seems to span two periods, a missing
   date, a room with no subject) is printed as a warning, not silently guessed.

All times are UK time (`Europe/London`).

### 2. Choosing which weeks to process

- Each PDF's week is the Monday of its first event.
- If two PDFs cover the same week, the newest wins, based on the date at the
  start of the filename (`20261023-...`); file modification time breaks ties.
- Weeks whose last event has already finished are skipped.

### 3. Updating Google Calendar (`google_calendar.py`)

- **Sign-in:** standard Google OAuth for a desktop app. The first run opens a
  browser to sign in; the result is saved in `token.json` and reused.
- **Permission requested:** `calendar.events` only. It can manage events but
  cannot change calendar settings or sharing.
- **Hidden labels:** every event the script creates carries private extended
  properties `timetable_export=1` and `timetable_week=<Monday's date>`. These
  are invisible in the calendar but let the script find its own events again.
- **Updating a week:** the script fetches the events labelled with that week.
  If they already match the PDF exactly, the week is "already up to date".
  Otherwise it deletes only those labelled events and inserts the new ones.
  Events without the labels (anything you add yourself) are never touched.

## Project files

| File | Purpose | In git? |
|---|---|---|
| `timetable.py` | PDF parsing, choosing weeks, the main program | yes |
| `google_calendar.py` | Sign-in and reading/replacing a week's events | yes |
| `requirements.txt` | Python libraries needed | yes |
| `config.example.json` | Template for `config.json` | yes |
| `README.md` | This file | yes |
| `config.json` | Your calendar ID and timetable folder | **no** (personal) |
| `credentials.json` | Google OAuth client file | **no** (secret) |
| `token.json` | Saved Google sign-in | **no** (secret) |
| `.venv/` | Installed libraries | no |

## Setup

### 1. Python

Uses Python 3.13 from Homebrew and a virtual environment inside the project:

```bash
brew install python@3.13
/opt/homebrew/bin/python3.13 -m venv .venv
.venv/bin/pip install -r requirements.txt
```

### 2. Google Calendar

1. In [Google Calendar](https://calendar.google.com), next to **Other calendars**
   click **+** → **Create new calendar**, e.g. `Work Timetable`. Leave it unshared.
2. In that calendar's **Settings and sharing**, copy the **Calendar ID** from
   **Integrate calendar**.

### 3. Google Cloud (one-time)

1. At [console.cloud.google.com](https://console.cloud.google.com) create a
   new project (e.g. `Timetable Export`).
2. Enable the **Google Calendar API** for it.
3. Open **Google Auth Platform** → **Get started**: give it a name and your
   email, choose **External**, agree to the policy.
4. Under **Audience**, add your own Google account as a **test user**.
5. Under **Clients**, create a client of type **Desktop app** and download the
   JSON. Save it in the project folder as `credentials.json`.

> In **Testing** mode Google expires the sign-in after 7 days; the script then
> simply opens the browser to sign in again. Publishing the app (Audience →
> Publish app) avoids this, at the cost of an "unverified app" warning on
> first sign-in.

### 4. Configuration

Copy the template and fill in your details:

```bash
cp config.example.json config.json
```

```json
{
  "calendar_id": "your-calendar-id@group.calendar.google.com",
  "timetable_folder": "/path/to/your/Time tables"
}
```

## Usage

```bash
.venv/bin/python timetable.py            # update the calendar (asks before changing anything)
.venv/bin/python timetable.py --preview  # only print what the PDFs contain; no Google access
```

The first real run opens a browser: choose your account, click **Continue**
on the "Google hasn't verified this app" screen (expected: it's your own
script), and **tick the calendar events permission box**.

## Security notes

- `credentials.json` and `token.json` together give access to your calendar
  events. They are listed in `.gitignore`; never commit, email or upload them.
  `token.json` is saved readable by your user account only.
- If either file may have leaked: delete the client in Google Cloud
  (Google Auth Platform → Clients), remove `token.json`, and create a new client.
  You can also revoke access at [myaccount.google.com/permissions](https://myaccount.google.com/permissions).
- `config.json` is kept out of git because it holds personal details (your
  calendar ID and a folder path that can include your email address).

## Limitations

- Built for one specific timetable layout: days as columns, period rows each
  split into a subject part and a room part, times in the left-hand column.
  A different layout will produce warnings or no events; use `--preview` to check.
- If a lesson ever spans two period rows, the script warns rather than
  merging them; check that day by hand.
- Only the first page of each PDF is read.
