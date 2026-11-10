"""Talking to Google Calendar: signing in, reading and replacing a week's events.

Every event this script creates carries hidden labels ("private extended
properties"):
    timetable_export = "1"
    timetable_week   = "2026-11-09"   (the Monday of that week)
Only events with these labels are ever removed, so events you add yourself
are never touched.
"""

import os
from datetime import datetime

from google.auth.exceptions import RefreshError
from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError

# Permission to manage events only: it cannot change calendar settings or sharing.
SCOPES = ["https://www.googleapis.com/auth/calendar.events"]
TIMEZONE_NAME = "Europe/London"
TAG = "timetable_export"
WEEK = "timetable_week"


def connect(project_dir):
    """Sign in (using the saved token.json when possible) and return the API service."""
    credentials_file = project_dir / "credentials.json"
    token_file = project_dir / "token.json"
    if not credentials_file.exists():
        raise SystemExit(f"credentials.json not found in {project_dir}")

    creds = None
    if token_file.exists():
        creds = Credentials.from_authorized_user_file(str(token_file), SCOPES)

    if creds and not creds.valid and creds.refresh_token:
        try:
            creds.refresh(Request())
        except RefreshError:
            # In Testing mode Google expires the sign-in after 7 days.
            print("Your Google sign-in has expired; opening the browser to sign in again.")
            creds = None

    if not creds or not creds.valid:
        flow = InstalledAppFlow.from_client_secrets_file(str(credentials_file), SCOPES)
        creds = flow.run_local_server(port=0)
        token_file.write_text(creds.to_json())
        os.chmod(token_file, 0o600)  # readable by you only

    return build("calendar", "v3", credentials=creds, cache_discovery=False)


def events_for_week(service, calendar_id, monday):
    """Events this script previously added for the week starting on `monday`."""
    found, page_token = [], None
    try:
        while True:
            result = service.events().list(
                calendarId=calendar_id,
                privateExtendedProperty=[f"{TAG}=1", f"{WEEK}={monday.isoformat()}"],
                singleEvents=True,
                maxResults=250,
                pageToken=page_token,
            ).execute()
            found.extend(result.get("items", []))
            page_token = result.get("nextPageToken")
            if not page_token:
                return found
    except HttpError as error:
        if error.resp.status == 404:
            raise SystemExit("Google can't find that calendar: check calendar_id in config.json")
        raise


def _key(start, end, title, location):
    return (start, end, title, location or "")


def same_events(existing, events):
    """True when the calendar already holds exactly these events."""
    have = {
        _key(
            datetime.fromisoformat(item["start"]["dateTime"]),
            datetime.fromisoformat(item["end"]["dateTime"]),
            item.get("summary", ""),
            item.get("location", ""),
        )
        for item in existing
        if "dateTime" in item.get("start", {})
    }
    want = {_key(e.start, e.end, e.title, e.location) for e in events}
    return len(existing) == len(events) and have == want


def _body(event, monday, source_name):
    body = {
        "summary": event.title,
        "start": {"dateTime": event.start.isoformat(), "timeZone": TIMEZONE_NAME},
        "end": {"dateTime": event.end.isoformat(), "timeZone": TIMEZONE_NAME},
        "description": f"From timetable: {source_name} ({event.period})",
        "extendedProperties": {"private": {TAG: "1", WEEK: monday.isoformat()}},
    }
    if event.location:
        body["location"] = event.location
    return body


def replace_week(service, calendar_id, monday, source_name, events, existing):
    """Remove this script's old events for the week, then add the new ones."""
    for item in existing:
        try:
            service.events().delete(calendarId=calendar_id, eventId=item["id"]).execute()
        except HttpError as error:
            if error.resp.status not in (404, 410):  # already gone is fine
                raise
    for event in events:
        service.events().insert(
            calendarId=calendar_id, body=_body(event, monday, source_name)
        ).execute()
