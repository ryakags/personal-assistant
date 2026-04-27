import os
import json
import logging
import httpx
from datetime import datetime, timedelta

logger = logging.getLogger(__name__)

# Database IDs — read from environment
CALENDAR_DB_ID = os.environ.get("NOTION_CALENDAR_DB", "")
CONTACTS_DB_ID = os.environ.get("NOTION_CONTACTS_DB", "")


def _headers():
    token = os.environ.get("NOTION_TOKEN")
    if not token:
        raise ValueError("NOTION_TOKEN environment variable is not set")
    return {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json",
        "Notion-Version": "2022-06-28"
    }


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
            headers=_headers(),
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
            headers=_headers(),
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


def append_page_blocks(page_id: str, content: str) -> bool:
    """Append a paragraph block to the body of a Notion page."""
    try:
        response = httpx.patch(
            f"https://api.notion.com/v1/blocks/{page_id}/children",
            headers=_headers(),
            json={
                "children": [{
                    "object": "block",
                    "type": "paragraph",
                    "paragraph": {
                        "rich_text": [{"type": "text", "text": {"content": content}}]
                    }
                }]
            },
            timeout=10
        )
        response.raise_for_status()
        logger.info(f"Appended block to page {page_id}")
        return True
    except Exception as e:
        logger.error(f"Error appending block to Notion page: {e}", exc_info=True)
        return False


def search_contacts(name_query: str) -> list:
    """Search contacts database by name, sorted by most recent interaction."""
    try:
        response = httpx.post(
            f"https://api.notion.com/v1/databases/{CONTACTS_DB_ID}/query",
            headers=_headers(),
            json={
                "filter": {
                    "property": "Name",
                    "title": {"contains": name_query}
                },
                "page_size": 10
            },
            timeout=10
        )
        response.raise_for_status()
        results = response.json().get("results", [])

        contacts = []
        for page in results:
            props = page.get("properties", {})

            name_prop = props.get("Name", {})
            name = "".join([t.get("plain_text", "") for t in name_prop.get("title", [])])

            # Extract Last Saw rollup date (rollup of relation to calendar events)
            last_saw_prop = props.get("Last Saw", {})
            last_saw = None
            if last_saw_prop.get("type") == "rollup":
                rollup = last_saw_prop.get("rollup", {})
                if rollup.get("type") == "date" and rollup.get("date"):
                    last_saw = rollup["date"].get("start")

            contacts.append({"id": page["id"], "name": name, "last_saw": last_saw})

        # Sort by last_saw descending (None goes to end)
        contacts.sort(key=lambda c: c["last_saw"] or "", reverse=True)
        return contacts

    except Exception as e:
        logger.error(f"Error searching contacts: {e}", exc_info=True)
        return []


def create_contact(name: str) -> dict | None:
    try:
        response = httpx.post(
            "https://api.notion.com/v1/pages",
            headers=_headers(),
            json={
                "parent": {"database_id": CONTACTS_DB_ID},
                "properties": {
                    "Name": {
                        "title": [{"type": "text", "text": {"content": name}}]
                    }
                }
            },
            timeout=10
        )
        response.raise_for_status()
        page = response.json()
        logger.info(f"Created contact: {name}")
        return {"id": page["id"], "name": name, "last_saw": None}
    except Exception as e:
        logger.error(f"Error creating contact: {e}", exc_info=True)
        return None


def update_people_involved(event_page_id: str, contact_ids: list) -> bool:
    """Update the People Involved relation on a calendar event."""
    try:
        response = httpx.patch(
            f"https://api.notion.com/v1/pages/{event_page_id}",
            headers=_headers(),
            json={
                "properties": {
                    "People Involved": {
                        "relation": [{"id": cid} for cid in contact_ids]
                    }
                }
            },
            timeout=10
        )
        response.raise_for_status()
        logger.info(f"Updated People Involved on page {event_page_id}")
        return True
    except Exception as e:
        logger.error(f"Error updating People Involved: {e}", exc_info=True)
        return False


def get_upcoming_events(days_ahead: int = 7, date_from: str = None, date_to: str = None) -> list:
    today = datetime.now().strftime("%Y-%m-%d")
    start = date_from or today
    end = date_to or (datetime.now() + timedelta(days=days_ahead)).strftime("%Y-%m-%d")

    body = {
        "filter": {
            "and": [
                {"property": "Scheduled", "date": {"on_or_after": start}},
                {"property": "Scheduled", "date": {"on_or_before": end}}
            ]
        },
        "sorts": [{"property": "Scheduled", "direction": "ascending"}],
        "page_size": 20
    }

    try:
        response = httpx.post(
            f"https://api.notion.com/v1/databases/{CALENDAR_DB_ID}/query",
            headers=_headers(),
            json=body,
            timeout=10
        )
        response.raise_for_status()
        results = response.json().get("results", [])

        events = []
        for page in results:
            props = page.get("properties", {})

            name_prop = props.get("Name", {})
            name = "".join([t.get("plain_text", "") for t in name_prop.get("title", [])])

            scheduled_prop = props.get("Scheduled", {}).get("date", {})
            scheduled = scheduled_prop.get("start", "") if scheduled_prop else ""

            type_prop = props.get("Type of Event", {}).get("select", {})
            event_type_val = type_prop.get("name", "") if type_prop else ""

            location_prop = props.get("Location", {})
            location = "".join([t.get("plain_text", "") for t in location_prop.get("rich_text", [])])

            events.append({
                "name": name,
                "scheduled": scheduled,
                "type": event_type_val,
                "location": location
            })

        return events

    except Exception as e:
        logger.error(f"Error fetching upcoming events: {e}", exc_info=True)
        return []


def get_contacts_by_ids(contact_ids: list) -> list:
    """Fetch name + id for a list of contact page IDs."""
    contacts = []
    for cid in contact_ids:
        try:
            response = httpx.get(
                f"https://api.notion.com/v1/pages/{cid}",
                headers=_headers(),
                timeout=10
            )
            response.raise_for_status()
            props = response.json().get("properties", {})
            name = "".join([t.get("plain_text", "") for t in props.get("Name", {}).get("title", [])])
            if name:
                contacts.append({"id": cid, "name": name})
        except Exception as e:
            logger.error(f"Error fetching contact {cid}: {e}")
    return contacts


def append_blocks(page_id: str, blocks: list) -> bool:
    """Append a list of pre-built block dicts to a Notion page."""
    try:
        response = httpx.patch(
            f"https://api.notion.com/v1/blocks/{page_id}/children",
            headers=_headers(),
            json={"children": blocks},
            timeout=15
        )
        response.raise_for_status()
        logger.info(f"Appended {len(blocks)} blocks to {page_id}")
        return True
    except Exception as e:
        logger.error(f"Error appending blocks to {page_id}: {e}", exc_info=True)
        return False


def write_contact_recap(contact_id: str, event_name: str, event_date: str, bullets: list, facts: list) -> bool:
    """Append an event recap section to a contact's Notion page."""
    blocks = [{
        "object": "block",
        "type": "heading_2",
        "heading_2": {"rich_text": [{"type": "text", "text": {"content": f"{event_name} — {event_date}"}}]}
    }]
    for bullet in bullets:
        blocks.append({
            "object": "block",
            "type": "bulleted_list_item",
            "bulleted_list_item": {"rich_text": [{"type": "text", "text": {"content": bullet}}]}
        })
    if facts:
        blocks.append({
            "object": "block",
            "type": "heading_3",
            "heading_3": {"rich_text": [{"type": "text", "text": {"content": "Personal Facts"}}]}
        })
        for fact in facts:
            blocks.append({
                "object": "block",
                "type": "bulleted_list_item",
                "bulleted_list_item": {"rich_text": [{"type": "text", "text": {"content": fact}}]}
            })
    return append_blocks(contact_id, blocks)


def write_contact_summary(contact_id: str, bullets: list) -> bool:
    blocks = [{
        "object": "block",
        "type": "heading_2",
        "heading_2": {"rich_text": [{"type": "text", "text": {"content": "Summary"}}]}
    }]
    for bullet in bullets:
        blocks.append({
            "object": "block",
            "type": "bulleted_list_item",
            "bulleted_list_item": {"rich_text": [{"type": "text", "text": {"content": bullet}}]}
        })
    return append_blocks(contact_id, blocks)


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
            headers=_headers(),
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
