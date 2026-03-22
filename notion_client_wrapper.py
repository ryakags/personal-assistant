import os
import httpx
import logging
from datetime import datetime, timezone, timedelta
from typing import Optional

logger = logging.getLogger(__name__)

NOTION_TOKEN = os.environ.get("NOTION_TOKEN")
NOTION_CALENDAR_DB = os.environ.get("NOTION_CALENDAR_DB")
NOTION_CONTACTS_DB = os.environ.get("NOTION_CONTACTS_DB")
TIMEZONE_OFFSET = int(os.environ.get("TIMEZONE_OFFSET", "-5"))  # Default to ET

HEADERS = {
    "Authorization": f"Bearer {NOTION_TOKEN}",
    "Content-Type": "application/json",
    "Notion-Version": "2022-06-28"
}


def get_todays_events() -> list:
    """Query Notion calendar for today's events."""
    try:
        now = datetime.now(timezone(timedelta(hours=TIMEZONE_OFFSET)))
        today = now.strftime("%Y-%m-%d")
        logger.info(f"Querying Notion for events on: {today}")

        response = httpx.post(
            f"https://api.notion.com/v1/databases/{NOTION_CALENDAR_DB}/query",
            headers=HEADERS,
            json={"filter": {"property": "Scheduled", "date": {"equals": today}}},
            timeout=15
        )
        response.raise_for_status()
        results = response.json().get("results", [])

        events = []
        for page in results:
            props = page.get("properties", {})
            title = _get_title(props)
            event_type = _get_select(props, "Type of Event")
            workout = _get_multi_select(props, "Workout")
            people_relation = props.get("People Involved", {}).get("relation", [])

            contacts = []
            for rel in people_relation:
                contact_id = rel.get("id", "")
                contact_name = _get_page_title(contact_id)
                if contact_id:
                    contacts.append({"id": contact_id, "name": contact_name})

            events.append({
                "id": page["id"],
                "title": title,
                "type": event_type,
                "workout": workout,
                "people": [c["name"] for c in contacts],
                "contacts": contacts
            })

        logger.info(f"Found {len(events)} events today")
        return events

    except Exception as e:
        logger.error(f"Failed to get today's events: {e}", exc_info=True)
        return []


def update_event_notes(page_id: str, summary: str, followups: list) -> bool:
    """Update the Notes property on the event page."""
    try:
        full_note = summary
        if followups:
            full_note += "\n\n📌 Follow-ups: " + " | ".join(followups)

        httpx.patch(
            f"https://api.notion.com/v1/pages/{page_id}",
            headers=HEADERS,
            json={
                "properties": {
                    "Notes": {
                        "rich_text": [{"type": "text", "text": {"content": full_note}}]
                    }
                }
            },
            timeout=10
        ).raise_for_status()

        logger.info(f"Updated event notes for {page_id}")
        return True
    except Exception as e:
        logger.error(f"Failed to update event notes: {e}", exc_info=True)
        return False


def update_contact(page_id: str, name: str, summary: str, followups: list, event_title: str) -> bool:
    """Update a contact's Last Seen date and replace the Last Seen recap block."""
    try:
        today = datetime.now().strftime("%Y-%m-%d")
        date_label = datetime.now().strftime("%B %d, %Y")

        # Update Last Seen property
        httpx.patch(
            f"https://api.notion.com/v1/pages/{page_id}",
            headers=HEADERS,
            json={"properties": {"Last Seen": {"date": {"start": today}}}},
            timeout=10
        ).raise_for_status()

        # Delete existing blocks so we replace instead of append
        _clear_page_blocks(page_id)

        # Build the new Last Seen recap block
        full_summary = summary
        if followups:
            full_summary += "\n\n📌 Follow-up: " + " | ".join(followups)

        blocks = [
            {
                "object": "block",
                "type": "heading_3",
                "heading_3": {
                    "rich_text": [{"type": "text", "text": {"content": f"Last Seen — {date_label}"}}]
                }
            },
            {
                "object": "block",
                "type": "paragraph",
                "paragraph": {
                    "rich_text": [{"type": "text", "text": {"content": f"{event_title}"}}]
                }
            },
            {
                "object": "block",
                "type": "paragraph",
                "paragraph": {
                    "rich_text": [{"type": "text", "text": {"content": full_summary}}]
                }
            }
        ]

        httpx.patch(
            f"https://api.notion.com/v1/blocks/{page_id}/children",
            headers=HEADERS,
            json={"children": blocks},
            timeout=10
        ).raise_for_status()

        logger.info(f"Updated contact {name} ({page_id})")
        return True
    except Exception as e:
        logger.error(f"Failed to update contact {name}: {e}", exc_info=True)
        return False


def _clear_page_blocks(page_id: str):
    """Delete all existing blocks on a page."""
    try:
        response = httpx.get(
            f"https://api.notion.com/v1/blocks/{page_id}/children",
            headers=HEADERS,
            timeout=10
        )
        response.raise_for_status()
        blocks = response.json().get("results", [])

        for block in blocks:
            block_id = block.get("id")
            if block_id:
                httpx.delete(
                    f"https://api.notion.com/v1/blocks/{block_id}",
                    headers=HEADERS,
                    timeout=10
                )
    except Exception as e:
        logger.error(f"Failed to clear blocks for {page_id}: {e}")


def _get_title(props: dict) -> str:
    title_prop = props.get("Name") or props.get("title") or {}
    title_list = title_prop.get("title", [])
    return title_list[0]["plain_text"] if title_list else "Untitled"


def _get_select(props: dict, key: str) -> str:
    select = props.get(key, {}).get("select")
    return select.get("name", "") if select else ""


def _get_multi_select(props: dict, key: str) -> list:
    items = props.get(key, {}).get("multi_select", [])
    return [item["name"] for item in items]


def _get_page_title(page_id: str) -> str:
    """Fetch a page's title by ID."""
    try:
        response = httpx.get(
            f"https://api.notion.com/v1/pages/{page_id}",
            headers=HEADERS,
            timeout=10
        )
        response.raise_for_status()
        props = response.json().get("properties", {})
        return _get_title(props)
    except Exception as e:
        logger.error(f"Failed to get page title for {page_id}: {e}")
        return "Unknown"
