"""
Notion tool definitions and executor for Claude's tool-use interface.
Tools are defined with JSON Schema so they can be passed directly to the Claude API.
Each tool maps to functions in notion_client_wrapper.py.
"""

import json
import logging
from notion_client_wrapper import (
    query_calendar,
    create_calendar_event,
    search_contacts,
    create_contact,
    update_people_involved,
    get_contacts_by_ids,
    append_page_blocks,
    replace_section,
    write_contact_recap,
    write_contact_summary,
)

logger = logging.getLogger(__name__)

NOTION_TOOLS = [
    {
        "name": "notion_query_calendar",
        "description": (
            "Query the user's Notion calendar. Returns events with id, name, date, type, location, "
            "and the names/ids of People Involved. Use date_from + date_to for ranges. "
            "Use event_type to filter (e.g. 'Dinner', 'Exercise'). Use name_query to search by event name. "
            "Omit filters to get the next 7 days."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "date_from": {"type": "string", "description": "Start date ISO string, e.g. '2026-04-01'"},
                "date_to":   {"type": "string", "description": "End date ISO string, e.g. '2026-04-30'"},
                "event_type": {"type": "string", "description": "Event type filter, e.g. 'Dinner'"},
                "name_query": {"type": "string", "description": "Partial event name to search for"},
            },
        },
    },
    {
        "name": "notion_create_calendar_event",
        "description": "Create a new event in the user's Notion calendar.",
        "input_schema": {
            "type": "object",
            "properties": {
                "name":       {"type": "string"},
                "date":       {"type": "string", "description": "ISO date, e.g. '2026-04-13'"},
                "event_type": {"type": "string", "description": "Must match a valid Notion event type"},
                "location":   {"type": "string"},
                "notes":      {"type": "string"},
            },
            "required": ["name", "date", "event_type"],
        },
    },
    {
        "name": "notion_search_contacts",
        "description": "Search the user's Notion contacts database by name. Returns up to 3 matches sorted by most recent interaction.",
        "input_schema": {
            "type": "object",
            "properties": {
                "name_query": {"type": "string", "description": "First name or partial name"},
            },
            "required": ["name_query"],
        },
    },
    {
        "name": "notion_create_contact",
        "description": "Create a new contact in the user's Notion contacts database.",
        "input_schema": {
            "type": "object",
            "properties": {
                "name": {"type": "string", "description": "Contact's full name"},
            },
            "required": ["name"],
        },
    },
    {
        "name": "notion_update_event_people",
        "description": (
            "Set the People Involved on a calendar event. Pass the complete desired list of contact IDs "
            "(the current list is returned by notion_query_calendar). To add someone: include existing ids + new id. "
            "To remove: exclude their id."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "event_id":    {"type": "string", "description": "Notion page ID of the calendar event"},
                "contact_ids": {"type": "array", "items": {"type": "string"}, "description": "Complete list of contact page IDs to set"},
            },
            "required": ["event_id", "contact_ids"],
        },
    },
    {
        "name": "notion_append_to_page",
        "description": "Append a paragraph of text to a Notion page body (for adding notes to an event page).",
        "input_schema": {
            "type": "object",
            "properties": {
                "page_id": {"type": "string"},
                "content": {"type": "string", "description": "Text to append as a paragraph"},
            },
            "required": ["page_id", "content"],
        },
    },
    {
        "name": "notion_write_recap_to_event",
        "description": "Write (or replace) a Recap section on a calendar event page.",
        "input_schema": {
            "type": "object",
            "properties": {
                "event_id":   {"type": "string"},
                "event_date": {"type": "string", "description": "Date string, e.g. '2026-04-13'"},
                "summary":    {"type": "string", "description": "3-5 sentence summary of what happened"},
            },
            "required": ["event_id", "event_date", "summary"],
        },
    },
    {
        "name": "notion_write_contact_recap",
        "description": "Write (or replace) an event recap section on a contact's Notion page.",
        "input_schema": {
            "type": "object",
            "properties": {
                "contact_id": {"type": "string"},
                "event_name": {"type": "string"},
                "event_date": {"type": "string"},
                "bullets": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "Key observations about this person at the event",
                },
                "facts": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "Personal facts learned (job, life updates, opinions, plans, etc.)",
                },
            },
            "required": ["contact_id", "event_name", "event_date", "bullets"],
        },
    },
    {
        "name": "notion_write_contact_summary",
        "description": "Write (or replace) the Summary section on a contact's Notion page. Use for profile updates.",
        "input_schema": {
            "type": "object",
            "properties": {
                "contact_id": {"type": "string"},
                "bullets": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "5-15 bullet facts about this person",
                },
            },
            "required": ["contact_id", "bullets"],
        },
    },
]


def execute_notion_tool(name: str, input_data: dict) -> str:
    """Execute a Notion tool call and return a JSON string result."""
    try:
        if name == "notion_query_calendar":
            events = query_calendar(
                date_from=input_data.get("date_from"),
                date_to=input_data.get("date_to"),
                event_type=input_data.get("event_type"),
                name_query=input_data.get("name_query"),
            )
            # Retry without type filter if no results
            if not events and input_data.get("event_type"):
                events = query_calendar(
                    date_from=input_data.get("date_from"),
                    date_to=input_data.get("date_to"),
                    name_query=input_data.get("name_query"),
                )
            return json.dumps(events)

        elif name == "notion_create_calendar_event":
            ok = create_calendar_event(
                name=input_data["name"],
                date=input_data["date"],
                event_type=input_data["event_type"],
                location=input_data.get("location", ""),
                notes=input_data.get("notes", ""),
            )
            return "success" if ok else "error: failed to create event"

        elif name == "notion_search_contacts":
            contacts = search_contacts(input_data["name_query"])
            return json.dumps(contacts[:3])

        elif name == "notion_create_contact":
            contact = create_contact(input_data["name"])
            return json.dumps(contact) if contact else "error: failed to create contact"

        elif name == "notion_update_event_people":
            ok = update_people_involved(input_data["event_id"], input_data["contact_ids"])
            return "success" if ok else "error: failed to update"

        elif name == "notion_append_to_page":
            ok = append_page_blocks(input_data["page_id"], input_data["content"])
            return "success" if ok else "error: failed to append"

        elif name == "notion_write_recap_to_event":
            blocks = [
                {
                    "object": "block",
                    "type": "heading_2",
                    "heading_2": {
                        "rich_text": [{"type": "text", "text": {"content": f"Recap — {input_data['event_date']}"}}]
                    },
                },
                {
                    "object": "block",
                    "type": "paragraph",
                    "paragraph": {
                        "rich_text": [{"type": "text", "text": {"content": input_data["summary"]}}]
                    },
                },
            ]
            ok = replace_section(input_data["event_id"], f"Recap — {input_data['event_date']}", blocks)
            return "success" if ok else "error"

        elif name == "notion_write_contact_recap":
            ok = write_contact_recap(
                contact_id=input_data["contact_id"],
                event_name=input_data["event_name"],
                event_date=input_data["event_date"],
                bullets=input_data["bullets"],
                facts=input_data.get("facts", []),
            )
            return "success" if ok else "error"

        elif name == "notion_write_contact_summary":
            ok = write_contact_summary(
                contact_id=input_data["contact_id"],
                bullets=input_data["bullets"],
            )
            return "success" if ok else "error"

        else:
            return f"Unknown tool: {name}"

    except Exception as e:
        logger.error(f"Notion tool error [{name}]: {e}", exc_info=True)
        return f"error: {e}"
