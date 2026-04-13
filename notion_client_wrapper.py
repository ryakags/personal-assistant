import os
import json
import logging
import httpx
from datetime import datetime, timedelta

logger = logging.getLogger(__name__)

NOTION_TOKEN = os.environ.get("NOTION_TOKEN")
NOTION_CALENDAR_DB = "collection://1f889657-f277-459b-a531-f039a9965f95"

HEADERS = {
    "Authorization": f"Bearer {NOTION_TOKEN}",
    "Content-Type": "application/json",
    "Notion-Version": "2022-06-28"
}

# The actual database ID (without collection:// prefix) for API calls
CALENDAR_DB_ID = "1f889657-f277-459b-a531-f039a9965f95"


def search_events(query_date: str = None, event_type: str = None, name_query: str = None, days_back: int = 1) -> list:
    """
    Search Notion calendar for events matching criteria.
    query_date: ISO date string like "2026-04-13"
    event_type: type of event like "Dinner", "Exercise", etc.
    name_query: partial name to search for
    days_back: how many days back to search if no specific date
    """
    filters = []

    # Date filter
    if query_date:
        filters.append({
            "property": "Scheduled",
            "date": {
                "equals": query_date
            }
        })
    else:
        # Default to recent events (last N days)
        since = (datetime.now() - timedelta(days=days_back)).strftime("%Y-%m-%d")
        filters.append({
            "property": "Scheduled",
            "date": {
                "on_or_after": since
            }
        })

    # Event type filter
    if event_type:
        filters.append({
            "property": "Type of Event",
            "select": {
                "equals": event_type
            }
        })

    filter_body = {"and": filters} if len(filters) > 1 else filters[0] if filters else {}

    body = {
        "filter": filter_body,
        "sorts": [{"property": "Scheduled", "direction": "descending"}],
        "page_size": 10
    }

    try:
        response = httpx.post(
            f"https://api.notion.com/v1/databases/{CALENDAR_DB_ID}/query",
            headers=HEADERS,
            json=body,
            timeout=10
        )
        response.raise_for_status()
        results = response.json().get("results", [])

        events = []
        for page in results:
            props = page.get("properties", {})

            # Extract name
            name_prop = props.get("Name", {})
            name = ""
            if name_prop.get("title"):
                name = "".join([t.get("plain_text", "") for t in name_prop["title"]])

            # Skip if name_query provided and doesn't match
            if name_query and name_query.lower() not in name.lower():
                continue

            # Extract scheduled date
            scheduled_prop = props.get("Scheduled", {}).get("date", {})
            scheduled = scheduled_prop.get("start", "") if scheduled_prop else ""

            # Extract type
            type_prop = props.get("Type of Event", {}).get("select", {})
            event_type_val = type_prop.get("name", "") if type_prop else ""

            # Extract existing notes
            notes_prop = props.get("Notes", {})
            notes = notes_prop.get("rich_text", [{}])
            existing_notes = "".join([t.get("plain_text", "") for t in notes]) if notes else ""

            # Extract people involved (relation - just get page IDs for now)
            people_prop = props.get("People Involved", {}).get("relation", [])
            people_ids = [p.get("id") for p in people_prop]

            # Extract location
            location_prop = props.get("Location", {})
            location = ""
            if location_prop.get("rich_text"):
                location = "".join([t.get("plain_text", "") for t in location_prop["rich_text"]])

            events.append({
                "id": page["id"],
                "name": name,
                "scheduled": scheduled,
                "type": event_type_val,
                "notes": existing_notes,
                "location": location,
                "people_ids": people_ids
            })

        return events

    except Exception as e:
        logger.error(f"Error searching Notion events: {e}", exc_info=True)
        return []


def write_event_notes(page_id: str, notes: str) -> bool:
    """Write notes back to a Notion calendar event."""
    try:
        response = httpx.patch(
            f"https://api.notion.com/v1/pages/{page_id}",
            headers=HEADERS,
            json={
                "properties": {
                    "Notes": {
                        "rich_text": [
                            {
                                "type": "text",
                                "text": {"content": notes}
                            }
                        ]
                    }
                }
            },
            timeout=10
        )
        response.raise_for_status()
        logger.info(f"Notes written to page {page_id}")
        return True
    except Exception as e:
        logger.error(f"Error writing notes to Notion: {e}", exc_info=True)
        return False


def get_todays_events() -> list:
    """Get today's events - kept for backwards compatibility."""
    today = datetime.now().strftime("%Y-%m-%d")
    return search_events(query_date=today)


def update_event_notes(page_id: str, summary: str, followups: list = None) -> bool:
    """Legacy function - kept for backwards compatibility."""
    notes = summary
    if followups:
        notes += "\n\nFollow-ups:\n" + "\n".join(f"- {f}" for f in followups)
    return write_event_notes(page_id, notes)


def update_contact(page_id: str, name: str, summary: str, followups: list, event_title: str):
    """Legacy function - kept for backwards compatibility."""
    pass


def create_calendar_event(name: str, date: str, event_type: str, location: str = "", notes: str = "") -> bool:
    """Create a new event in the Notion calendar database."""
    properties = {
        "Name": {
            "title": [{"type": "text", "text": {"content": name}}]
        },
        "Scheduled": {
            "date": {"start": date}
        }
    }

    if event_type:
        properties["Type of Event"] = {"select": {"name": event_type}}

    if location:
        properties["Location"] = {
            "rich_text": [{"type": "text", "text": {"content": location}}]
        }

    if notes:
        properties["Notes"] = {
            "rich_text": [{"type": "text", "text": {"content": notes}}]
        }

    try:
        response = httpx.post(
            "https://api.notion.com/v1/pages",
            headers=HEADERS,
            json={
                "parent": {"database_id": CALENDAR_DB_ID},
                "properties": properties
            },
            timeout=10
        )
        response.raise_for_status()
        logger.info(f"Created calendar event: {name} on {date}")
        return True
    except Exception as e:
        logger.error(f"Error creating Notion event: {e}", exc_info=True)
        return False
