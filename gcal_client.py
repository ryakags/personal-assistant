import os
import json
import logging
from datetime import datetime, timezone, timedelta
from google.oauth2 import service_account
from googleapiclient.discovery import build
from googleapiclient.discovery_cache.base import Cache

logger = logging.getLogger(__name__)

SCOPES = ["https://www.googleapis.com/auth/calendar.readonly"]


class _NoCache(Cache):
    """Disable the file-based discovery cache (avoids write errors on read-only filesystems)."""
    def get(self, url):
        return None
    def set(self, url, content):
        pass


def _get_service():
    creds_json = os.environ.get("GOOGLE_CREDENTIALS_JSON")
    if not creds_json:
        raise ValueError("GOOGLE_CREDENTIALS_JSON environment variable is not set")
    creds = service_account.Credentials.from_service_account_info(
        json.loads(creds_json), scopes=SCOPES
    )
    return build("calendar", "v3", credentials=creds, cache=_NoCache())


def fetch_events(calendar_id: str = None, days_back: int = 7, days_ahead: int = 30) -> list:
    """Fetch events from a Google Calendar within a window of days_back..days_ahead from now."""
    if not calendar_id:
        calendar_id = os.environ.get("GOOGLE_CALENDAR_ID", "primary")

    service = _get_service()
    now = datetime.now(timezone.utc)
    time_min = (now - timedelta(days=days_back)).isoformat()
    time_max = (now + timedelta(days=days_ahead)).isoformat()

    result = service.events().list(
        calendarId=calendar_id,
        timeMin=time_min,
        timeMax=time_max,
        singleEvents=True,
        orderBy="startTime",
        maxResults=250,
    ).execute()

    events = []
    for item in result.get("items", []):
        if item.get("status") == "cancelled":
            continue
        start = item.get("start", {})
        is_all_day = "date" in start and "dateTime" not in start
        events.append({
            "id": item["id"],
            "title": item.get("summary", "Untitled"),
            "start": start.get("dateTime") or start.get("date", ""),
            "end": (item.get("end", {}).get("dateTime") or item.get("end", {}).get("date", "")),
            "is_all_day": is_all_day,
            "description": item.get("description", ""),
            "location": item.get("location", ""),
        })

    logger.info(f"Fetched {len(events)} events from Google Calendar ({calendar_id})")
    return events
