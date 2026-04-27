import os
import json
import logging
import requests
from flask import Flask, request, jsonify
from datetime import datetime
from notion_client_wrapper import search_events, write_event_notes, create_calendar_event, append_page_blocks, append_blocks, search_contacts, update_people_involved, get_upcoming_events, create_contact, get_contacts_by_ids, write_contact_recap, write_contact_summary
from claude_client import get_claude_response

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

app = Flask(__name__)

MY_NUMBER = "+19168331436"
BLUEBUBBLES_URL = os.environ.get("BLUEBUBBLES_URL", "http://localhost:1234")
BLUEBUBBLES_PASSWORD = os.environ.get("BLUEBUBBLES_PASSWORD", "")

sessions = {}

SYSTEM_PROMPT = """You are Rocky, a personal AI assistant available over iMessage. You are helpful, concise, and conversational — this is iMessage, not email. Keep responses short and punchy. You can help with anything: questions, drafting, thinking through problems, recommendations, math, etc.

You also have access to the user's Notion calendar. When the user wants to review a past activity or add something to their calendar, you will handle that.

You have web search capability. Use it when the user asks about current events, real-time information, recent news, sports scores, weather, or anything that requires up-to-date data. Don't search for things you already know well."""

INTENT_PROMPT = """You are analyzing a message to determine the user's intent.

Return a JSON object with:
- "intent": one of "recap", "add_to_calendar", "edit_page", "update_people", "update_contact", "query_calendar", or "general"
- "date": ISO date string like "2026-04-13" or null
- "event_type": Notion event type or null (e.g. "Exercise", "Dinner", "Lunch", "Coffee", "Meeting")
- "name_query": partial name to search for, or null
- "days_back": how many days back to search (default 1, for review/edit_page/update_people intent only)
- "days_ahead": how many days ahead to look (for query_calendar intent only, default 7)
- "action": "add" or "remove" (for update_people intent only), or null
- "contact_name": first name of the person to add/remove (for update_people intent only), or null
- "needs_web_search": true if the message requires real-time or current information (news, scores, weather, stock prices, recent events), false otherwise

Notion event types: Exercise, Dinner, Concert, Reminder, Comedy, Call, Vacation, Lunch, Party, Coffee, FaceTime, Happy Hour, Sports, Wedding, Festival, Work, Food, Remote Work Trip, Haircut, Movie, Coffee Club, Podcast, Appointment, Art, Date, Comedy Show, Basketball, Therapy, Birthday, Drinks, Hangout, Grocery, Laundry, Beach, Airport, Speaker Event, Open Mic, Errand, Breakfast, Cowork, Cultural Event, Volunteering, Sick, Music, Art Show, Doctors, Pop Up, Bars, Project Work, Travel, Brunch, Self Care, Theater, Trivia, Meeting, Broadway, Bars, Clubbing, Baseball, Bachelor Party, House Warming, Visitors, Short Trip, Holiday Trip

"add_to_calendar" intent examples: "add dinner with Jake to my calendar", "put my workout on the cal", "add to my notion", "create a calendar event for lunch tomorrow", "schedule a meeting for friday"
"recap" intent examples: "let's recap my workout today", "review dinner with Sarah last night", "recap my meeting", "recap dinner", "let's do a recap"
"edit_page" intent examples: "update my workout today", "add notes to my dinner last night", "edit my meeting from this morning", "I want to add something to today's workout page"
"update_people" intent examples: "add Jake to my dinner last night", "remove Sarah from my meeting today", "add Mike to last night's event"
"query_calendar" intent examples: "what's on my calendar this week?", "what do I have coming up?", "what are my plans for tomorrow?", "show me my schedule", "what's on my calendar today?", "anything on my calendar this weekend?". Use days_ahead: 1 for today/tomorrow, 7 for this week, 14 for next two weeks, 30 for this month.
"update_contact" intent examples: "update Jake", "add notes about Sarah", "update my contact for Mike", "add a summary for Jessica", "update [name]'s profile". Use contact_name field.
"general" intent: everything else

Today's date is: {today}

Respond with ONLY the JSON object, no other text."""

ADD_EVENT_PROMPT = """You are Rocky, helping the user add an event to their Notion calendar over iMessage.

You need to collect these details:
- Name (required): what is this event called?
- Date (required): when is it?
- Type of Event (required): pick the best match from the list
- Location (optional)
- Notes (optional)

Notion event types: Exercise, Dinner, Concert, Reminder, Comedy, Call, Vacation, Lunch, Party, Coffee, FaceTime, Happy Hour, Sports, Wedding, Festival, Work, Food, Remote Work Trip, Haircut, Movie, Coffee Club, Podcast, Appointment, Art, Date, Comedy Show, Basketball, Therapy, Birthday, Drinks, Hangout, Grocery, Laundry, Beach, Airport, Errand, Breakfast, Cowork, Cultural Event, Volunteering, Sick, Music, Bars, Project Work, Travel, Brunch, Self Care, Theater, Trivia, Meeting, Broadway, Clubbing, Baseball, Bachelor Party, House Warming, Visitors, Short Trip, Holiday Trip

Today's date is: {today}

Current collected info: {collected}

Rules:
- Ask for missing required fields one round at a time
- Keep it conversational and short
- Infer what you can from context (e.g. "dinner tomorrow" → Type: Dinner, Date: tomorrow's date)
- Once you have name, date, and type, confirm and create it

When you have enough to create the event, respond with ONLY this JSON:
{{"ready": true, "name": "...", "date": "YYYY-MM-DD", "event_type": "...", "location": "...", "notes": "...", "confirm_message": "Short confirmation message to send the user"}}

If location or notes are not provided, use empty strings."""


EDIT_PAGE_PROMPT = """You are Rocky, helping the user add notes to an existing Notion page over iMessage.

Event: {event_name} ({event_type}) on {event_date}

Your job is to collect what the user wants to add to the page body. Keep it short and conversational.

- If the user has already provided content in their message, use it directly.
- If not, ask once: "What do you want to add to this page?"
- Once you have content, respond with ONLY this JSON:
{{"ready": true, "content": "The notes to append verbatim", "closing_message": "Short friendly confirmation"}}"""


RECAP_PROMPT = """You are processing an event recap for a personal Notion database.

Event: {event_name} ({event_type}) on {event_date}
People Involved: {contact_names}

The user's recap notes:
{recap_text}

Return a JSON object with:
{{
  "event_summary": "3-5 sentence structured summary of what happened",
  "contacts": [
    {{
      "name": "Contact name exactly as listed in People Involved",
      "bullets": ["Key thing about this person at the event", "Another notable moment"],
      "facts": ["Personal fact or life update mentioned", "Another fact"]
    }}
  ],
  "closing_message": "Short punchy iMessage confirmation of what was saved"
}}

Rules:
- event_summary: past tense, structured and clear
- For each contact in People Involved, write 1-3 bullets about their involvement and extract personal facts (job changes, life updates, opinions, plans, preferences, health, relationships, etc.)
- Only include a contact in the array if they were actually mentioned in the recap notes
- facts can be [] if nothing personal was mentioned about them
- Respond with ONLY the JSON"""


CONTACT_NOTE_PROMPT = """You are updating a contact profile in a personal Notion database.

Contact: {contact_name}

The user's notes about this person:
{notes_text}

Return a JSON object with:
{{
  "bullets": ["Fact or note about this person", "Another fact"],
  "closing_message": "Short iMessage-style confirmation"
}}

Rules:
- Extract key facts and notes: job/career, location, family, interests, how they know them, recent life updates, personality, anything worth remembering
- Each bullet is a clear standalone fact
- 5-15 bullets depending on how much was shared
- Respond with ONLY the JSON"""


def send_message(chat_guid: str, text: str):
    url = f"{BLUEBUBBLES_URL}/api/v1/message/text"
    payload = {
        "chatGuid": chat_guid,
        "tempGuid": f"temp-{datetime.now().timestamp()}",
        "message": text,
        "method": "private-api"
    }
    params = {"password": BLUEBUBBLES_PASSWORD}
    try:
        response = requests.post(url, json=payload, params=params, timeout=10)
        response.raise_for_status()
        logger.info(f"Message sent to {chat_guid}")
    except Exception as e:
        logger.error(f"Failed to send message via BlueBubbles: {e}", exc_info=True)


def extract_sender_number(data: dict) -> str | None:
    try:
        return data.get("data", {}).get("handle", {}).get("address", "")
    except Exception:
        return None


def extract_message_text(data: dict) -> str | None:
    try:
        return data.get("data", {}).get("text", "").strip()
    except Exception:
        return None


def extract_chat_guid(data: dict) -> str | None:
    try:
        chats = data.get("data", {}).get("chats", [])
        return chats[0].get("guid", "") if chats else None
    except Exception:
        return None


@app.route("/webhook", methods=["POST"])
def webhook():
    data = request.json
    logger.info(f"Incoming webhook: {json.dumps(data)}")

    if data.get("type") != "new-message":
        return jsonify({"ok": True})

    message = data.get("data", {})
    if message.get("isFromMe", False):
        return jsonify({"ok": True})

    text = extract_message_text(data)
    if not text:
        return jsonify({"ok": True})

    sender = extract_sender_number(data)
    if not sender or sender != MY_NUMBER:
        logger.info(f"Ignoring message from unknown sender: {sender}")
        return jsonify({"ok": True})

    chat_guid = extract_chat_guid(data)
    if not chat_guid:
        logger.error("Could not extract chat GUID")
        return jsonify({"ok": True})

    try:
        handle_message(chat_guid, sender, text)
    except Exception as e:
        logger.error(f"Error handling message: {e}", exc_info=True)
        send_message(chat_guid, "Sorry, something went wrong. Try again?")

    return jsonify({"ok": True})


def handle_message(chat_guid: str, sender: str, text: str):
    session = sessions.get(sender, {})

    # Continue active sessions
    if session.get("state") in ("recapping", "selecting_event_for_recap"):
        handle_recap_response(chat_guid, sender, text, session)
        return
    if session.get("state") == "creating_event":
        handle_create_event_response(chat_guid, sender, text, session)
        return
    if session.get("state") == "editing_page":
        handle_edit_page_response(chat_guid, sender, text, session)
        return
    if session.get("state") == "selecting_event_for_edit":
        handle_edit_page_response(chat_guid, sender, text, session)
        return
    if session.get("state") in ("noting_contact", "selecting_contact_for_note"):
        handle_contact_note_response(chat_guid, sender, text, session)
        return
    if session.get("state") in ("updating_people", "selecting_event_for_people", "selecting_contact_for_people", "creating_contact"):
        handle_update_people_response(chat_guid, sender, text, session)
        return

    # Detect intent
    intent = detect_intent(text)
    logger.info(f"Detected intent: {intent}")

    if intent and intent.get("intent") == "recap":
        start_recap_session(chat_guid, sender, text, intent)
    elif intent and intent.get("intent") == "add_to_calendar":
        start_create_event_session(chat_guid, sender, text)
    elif intent and intent.get("intent") == "edit_page":
        start_edit_page_session(chat_guid, sender, text, intent)
    elif intent and intent.get("intent") == "update_people":
        start_update_people_session(chat_guid, sender, text, intent)
    elif intent and intent.get("intent") == "update_contact":
        start_contact_note_session(chat_guid, sender, text, intent)
    elif intent and intent.get("intent") == "query_calendar":
        handle_calendar_query(chat_guid, sender, text, intent)
    else:
        handle_general_message(chat_guid, sender, text, needs_web_search=bool(intent and intent.get("needs_web_search")))


def search_events_with_fallback(query_date, event_type, name_query, days_back):
    events = search_events(query_date=query_date, event_type=event_type, name_query=name_query, days_back=days_back)
    if not events and event_type:
        events = search_events(query_date=query_date, name_query=name_query, days_back=days_back)
    return events


def detect_intent(text: str) -> dict | None:
    try:
        today = datetime.now().strftime("%Y-%m-%d")
        prompt = INTENT_PROMPT.replace("{today}", today)
        messages = [{"role": "user", "content": text}]
        response = get_claude_response(prompt, messages, model="claude-haiku-4-5")
        start = response.find("{")
        end = response.rfind("}") + 1
        if start >= 0 and end > start:
            return json.loads(response[start:end])
        return None
    except Exception as e:
        logger.error(f"Error detecting intent: {e}")
        return None


# ── RECAP SESSION ───────────────────────────────────────────────

def start_recap_session(chat_guid: str, sender: str, text: str, intent: dict):
    events = search_events_with_fallback(
        query_date=intent.get("date"),
        event_type=intent.get("event_type"),
        name_query=intent.get("name_query"),
        days_back=intent.get("days_back", 1)
    )

    if not events:
        send_message(chat_guid, "I couldn't find any matching events in your calendar. Can you be more specific?")
        return

    if len(events) == 1:
        start_event_recap(chat_guid, sender, events[0])
    else:
        event_list = "\n".join([
            f"{i+1}. {e['name']} ({e['type']}) — {e['scheduled'][:10]}"
            for i, e in enumerate(events[:5])
        ])
        sessions[sender] = {
            "state": "selecting_event_for_recap",
            "events": events[:5],
            "chat_guid": chat_guid
        }
        send_message(chat_guid, f"Found a few events. Which one?\n\n{event_list}")


def start_event_recap(chat_guid: str, sender: str, event: dict):
    sessions[sender] = {
        "state": "recapping",
        "event": event,
        "messages": [],
        "chat_guid": chat_guid
    }
    event_desc = event["name"]
    if event.get("scheduled"):
        event_desc += f" on {event['scheduled'][:10]}"
    send_message(chat_guid, f"Recap mode for {event_desc}. Dump everything — say 'done' when you're finished.")


def handle_recap_response(chat_guid: str, sender: str, text: str, session: dict):
    if session.get("state") == "selecting_event_for_recap":
        try:
            idx = int(text.strip()) - 1
            events = session.get("events", [])
            if 0 <= idx < len(events):
                start_event_recap(chat_guid, sender, events[idx])
            else:
                send_message(chat_guid, "Please reply with a number from the list.")
        except ValueError:
            send_message(chat_guid, "Please reply with the number of the event.")
        return

    if text.strip().lower() == "done":
        messages = session.get("messages", [])
        if not messages:
            send_message(chat_guid, "You haven't shared anything yet — tell me about the event first.")
            return
        send_message(chat_guid, "Give me a sec...")
        finalize_recap(chat_guid, sender, session)
    else:
        session["messages"].append(text)
        sessions[sender] = session


def finalize_recap(chat_guid: str, sender: str, session: dict):
    event = session["event"]
    recap_text = "\n".join(session["messages"])
    event_date = event.get("scheduled", "")[:10] if event.get("scheduled") else "Unknown"

    contacts = get_contacts_by_ids(event.get("people_ids", []))
    contact_names = ", ".join([c["name"] for c in contacts]) if contacts else "None listed"

    prompt = RECAP_PROMPT.format(
        event_name=event.get("name", "Unknown"),
        event_type=event.get("type", "Unknown"),
        event_date=event_date,
        contact_names=contact_names,
        recap_text=recap_text
    )

    try:
        raw = get_claude_response(prompt, [{"role": "user", "content": "Process this recap."}], model="claude-sonnet-4-6")
        start = raw.find("{")
        end = raw.rfind("}") + 1
        parsed = json.loads(raw[start:end])
    except Exception as e:
        logger.error(f"Error parsing recap response: {e}")
        send_message(chat_guid, "Had trouble processing the recap — try again?")
        sessions.pop(sender, None)
        return

    # Write summary to event page body
    event_blocks = [
        {
            "object": "block",
            "type": "heading_2",
            "heading_2": {"rich_text": [{"type": "text", "text": {"content": f"Recap — {event_date}"}}]}
        },
        {
            "object": "block",
            "type": "paragraph",
            "paragraph": {"rich_text": [{"type": "text", "text": {"content": parsed.get("event_summary", "")}}]}
        }
    ]
    append_blocks(event["id"], event_blocks)

    # Write to each contact page
    contact_map = {c["name"].lower(): c["id"] for c in contacts}
    updated_contacts = []
    for c_data in parsed.get("contacts", []):
        name = c_data.get("name", "")
        cid = contact_map.get(name.lower())
        if not cid:
            continue
        write_contact_recap(
            contact_id=cid,
            event_name=event.get("name", ""),
            event_date=event_date,
            bullets=c_data.get("bullets", []),
            facts=c_data.get("facts", [])
        )
        updated_contacts.append(name)

    send_message(chat_guid, parsed.get("closing_message", "Recap saved!"))
    sessions.pop(sender, None)


# ── CREATE EVENT SESSION ────────────────────────────────────────

def start_create_event_session(chat_guid: str, sender: str, text: str):
    sessions[sender] = {
        "state": "creating_event",
        "history": [],
        "collected": {},
        "chat_guid": chat_guid
    }

    today = datetime.now().strftime("%Y-%m-%d")
    prompt = ADD_EVENT_PROMPT.replace("{today}", today).replace("{collected}", "{}")
    messages = [{"role": "user", "content": text}]
    response = get_claude_response(prompt, messages, model="claude-sonnet-4-6")

    # Check if Claude already has enough to create immediately
    try:
        start = response.find("{")
        end = response.rfind("}") + 1
        if start >= 0 and end > start:
            parsed = json.loads(response[start:end])
            if parsed.get("ready"):
                finalize_event_creation(chat_guid, sender, parsed)
                return
    except (json.JSONDecodeError, ValueError):
        pass

    sessions[sender]["history"].append({"role": "assistant", "content": response})
    send_message(chat_guid, response)


def handle_create_event_response(chat_guid: str, sender: str, text: str, session: dict):
    history = session["history"]
    collected = session.get("collected", {})
    history.append({"role": "user", "content": text})

    today = datetime.now().strftime("%Y-%m-%d")
    prompt = ADD_EVENT_PROMPT.replace("{today}", today).replace("{collected}", json.dumps(collected))
    word_count = len(text.split())
    model = "claude-haiku-4-5" if word_count < 50 else "claude-sonnet-4-6"
    response = get_claude_response(prompt, history, model=model)

    # Check if ready to create
    try:
        start = response.find("{")
        end = response.rfind("}") + 1
        if start >= 0 and end > start:
            parsed = json.loads(response[start:end])
            if parsed.get("ready"):
                finalize_event_creation(chat_guid, sender, parsed)
                return
    except (json.JSONDecodeError, ValueError):
        pass

    history.append({"role": "assistant", "content": response})
    session["history"] = history
    sessions[sender] = session
    send_message(chat_guid, response)


def finalize_event_creation(chat_guid: str, sender: str, event_data: dict):
    success = create_calendar_event(
        name=event_data.get("name", ""),
        date=event_data.get("date", ""),
        event_type=event_data.get("event_type", ""),
        location=event_data.get("location", ""),
        notes=event_data.get("notes", "")
    )

    if success:
        send_message(chat_guid, event_data.get("confirm_message", f"Added \"{event_data.get('name')}\" to your calendar! ✅"))
    else:
        send_message(chat_guid, f"Couldn't add to Notion — check the connection. Details were: {event_data.get('name')} on {event_data.get('date')}")

    sessions.pop(sender, None)


# ── EDIT PAGE SESSION ──────────────────────────────────────────

def start_edit_page_session(chat_guid: str, sender: str, text: str, intent: dict):
    events = search_events_with_fallback(
        query_date=intent.get("date"),
        event_type=intent.get("event_type"),
        name_query=intent.get("name_query"),
        days_back=intent.get("days_back", 1)
    )

    if not events:
        send_message(chat_guid, "I couldn't find any matching events. Can you be more specific?")
        return

    if len(events) == 1:
        start_page_edit(chat_guid, sender, events[0], text)
    else:
        event_list = "\n".join([
            f"{i+1}. {e['name']} ({e['type']}) — {e['scheduled'][:10]}"
            for i, e in enumerate(events[:5])
        ])
        sessions[sender] = {
            "state": "selecting_event_for_edit",
            "events": events[:5],
            "original_text": text,
            "chat_guid": chat_guid
        }
        send_message(chat_guid, f"Found a few events. Which one?\n\n{event_list}")


def start_page_edit(chat_guid: str, sender: str, event: dict, original_text: str):
    sessions[sender] = {
        "state": "editing_page",
        "event": event,
        "history": [],
        "chat_guid": chat_guid
    }

    prompt = EDIT_PAGE_PROMPT.format(
        event_name=event.get("name", "Unknown"),
        event_type=event.get("type", "Unknown"),
        event_date=event.get("scheduled", "")[:10] if event.get("scheduled") else "Unknown"
    )
    messages = [{"role": "user", "content": original_text}]
    response = get_claude_response(prompt, messages, model="claude-haiku-4-5")

    # Check if content was already provided in the original message
    try:
        start = response.find("{")
        end = response.rfind("}") + 1
        if start >= 0 and end > start:
            parsed = json.loads(response[start:end])
            if parsed.get("ready"):
                finalize_page_edit(chat_guid, sender, event, parsed)
                return
    except (json.JSONDecodeError, ValueError):
        pass

    sessions[sender]["history"].append({"role": "assistant", "content": response})
    send_message(chat_guid, response)


def handle_edit_page_response(chat_guid: str, sender: str, text: str, session: dict):
    if session.get("state") == "selecting_event_for_edit":
        try:
            idx = int(text.strip()) - 1
            events = session.get("events", [])
            if 0 <= idx < len(events):
                start_page_edit(chat_guid, sender, events[idx], session.get("original_text", text))
            else:
                send_message(chat_guid, "Please reply with a number from the list.")
        except ValueError:
            send_message(chat_guid, "Please reply with the number of the event you want to edit.")
        return

    event = session["event"]
    history = session["history"]
    history.append({"role": "user", "content": text})

    prompt = EDIT_PAGE_PROMPT.format(
        event_name=event.get("name", "Unknown"),
        event_type=event.get("type", "Unknown"),
        event_date=event.get("scheduled", "")[:10] if event.get("scheduled") else "Unknown"
    )
    response = get_claude_response(prompt, history, model="claude-haiku-4-5")

    try:
        start = response.find("{")
        end = response.rfind("}") + 1
        if start >= 0 and end > start:
            parsed = json.loads(response[start:end])
            if parsed.get("ready"):
                finalize_page_edit(chat_guid, sender, event, parsed)
                return
    except (json.JSONDecodeError, ValueError):
        pass

    history.append({"role": "assistant", "content": response})
    session["history"] = history
    sessions[sender] = session
    send_message(chat_guid, response)


def finalize_page_edit(chat_guid: str, sender: str, event: dict, parsed: dict):
    content = parsed.get("content", "")
    success = append_page_blocks(event["id"], content)
    closing = parsed.get("closing_message", "Added to your Notion page!")
    msg = closing if success else f"{closing}\n\n(Couldn't save to Notion — check the connection.)"
    send_message(chat_guid, msg)
    sessions.pop(sender, None)


# ── UPDATE PEOPLE SESSION ──────────────────────────────────────

def start_update_people_session(chat_guid: str, sender: str, text: str, intent: dict):
    action = intent.get("action", "add")
    contact_name = intent.get("contact_name")

    if not contact_name:
        send_message(chat_guid, "Who do you want to add or remove?")
        sessions[sender] = {"state": "updating_people", "action": action, "event": None, "chat_guid": chat_guid}
        return

    events = search_events_with_fallback(
        query_date=intent.get("date"),
        event_type=intent.get("event_type"),
        name_query=intent.get("name_query"),
        days_back=intent.get("days_back", 1)
    )

    if not events:
        send_message(chat_guid, "I couldn't find any matching events. Can you be more specific?")
        return

    if len(events) == 1:
        resolve_contact_for_event(chat_guid, sender, events[0], action, contact_name)
    else:
        event_list = "\n".join([
            f"{i+1}. {e['name']} ({e['type']}) — {e['scheduled'][:10]}"
            for i, e in enumerate(events[:5])
        ])
        sessions[sender] = {
            "state": "selecting_event_for_people",
            "events": events[:5],
            "action": action,
            "contact_name": contact_name,
            "chat_guid": chat_guid
        }
        send_message(chat_guid, f"Which event?\n\n{event_list}")


def resolve_contact_for_event(chat_guid: str, sender: str, event: dict, action: str, contact_name: str):
    contacts = search_contacts(contact_name)

    if not contacts:
        sessions[sender] = {
            "state": "creating_contact",
            "contact_name": contact_name,
            "event": event,
            "action": action,
            "chat_guid": chat_guid
        }
        send_message(chat_guid, f"No contact found for {contact_name}. Want me to create a new one?")
        return

    if len(contacts) == 1:
        finalize_people_update(chat_guid, sender, event, action, contacts[0])
    else:
        top = contacts[:3]
        contact_list = "\n".join([
            f"{i+1}. {c['name']}" + (f" (last saw {c['last_saw'][:10]})" if c.get('last_saw') else "")
            for i, c in enumerate(top)
        ])
        sessions[sender] = {
            "state": "selecting_contact_for_people",
            "event": event,
            "action": action,
            "contacts": top,
            "chat_guid": chat_guid
        }
        send_message(chat_guid, f"Which {contact_name}?\n\n{contact_list}")


def handle_update_people_response(chat_guid: str, sender: str, text: str, session: dict):
    state = session.get("state")

    if state == "selecting_event_for_people":
        try:
            idx = int(text.strip()) - 1
            events = session.get("events", [])
            if 0 <= idx < len(events):
                resolve_contact_for_event(chat_guid, sender, events[idx], session["action"], session["contact_name"])
            else:
                send_message(chat_guid, "Please reply with a number from the list.")
        except ValueError:
            send_message(chat_guid, "Please reply with the number of the event.")
        return

    if state == "selecting_contact_for_people":
        try:
            idx = int(text.strip()) - 1
            contacts = session.get("contacts", [])
            if 0 <= idx < len(contacts):
                finalize_people_update(chat_guid, sender, session["event"], session["action"], contacts[idx])
            else:
                send_message(chat_guid, "Please reply with a number from the list.")
        except ValueError:
            send_message(chat_guid, "Please reply with the number of the contact.")
        return

    if state == "creating_contact":
        if text.strip().lower() in ("yes", "y", "yeah", "yep", "sure", "yup", "ok", "okay"):
            contact = create_contact(session["contact_name"])
            if contact:
                finalize_people_update(chat_guid, sender, session["event"], session["action"], contact)
            else:
                send_message(chat_guid, "Couldn't create the contact — check the Notion connection.")
                sessions.pop(sender, None)
        else:
            send_message(chat_guid, "Got it, skipping.")
            sessions.pop(sender, None)
        return


def finalize_people_update(chat_guid: str, sender: str, event: dict, action: str, contact: dict):
    current_ids = event.get("people_ids", [])

    if action == "add":
        if contact["id"] in current_ids:
            send_message(chat_guid, f"{contact['name']} is already on {event['name']}.")
            sessions.pop(sender, None)
            return
        new_ids = current_ids + [contact["id"]]
        verb = "Added"
        prep = "to"
    else:
        if contact["id"] not in current_ids:
            send_message(chat_guid, f"{contact['name']} isn't on {event['name']}.")
            sessions.pop(sender, None)
            return
        new_ids = [cid for cid in current_ids if cid != contact["id"]]
        verb = "Removed"
        prep = "from"

    success = update_people_involved(event["id"], new_ids)
    if success:
        send_message(chat_guid, f"{verb} {contact['name']} {prep} {event['name']}.")
    else:
        send_message(chat_guid, f"Couldn't update People Involved — check the Notion connection.")
    sessions.pop(sender, None)


# ── CONTACT NOTE SESSION ───────────────────────────────────────

def start_contact_note_session(chat_guid: str, sender: str, text: str, intent: dict):
    contact_name = intent.get("contact_name")
    if not contact_name:
        send_message(chat_guid, "Who do you want to update?")
        sessions[sender] = {"state": "noting_contact", "contact": None, "messages": [], "chat_guid": chat_guid}
        return

    contacts = search_contacts(contact_name)

    if not contacts:
        send_message(chat_guid, f"No contact found for {contact_name}. Add them first with 'add {contact_name} to [event]'.")
        return

    if len(contacts) == 1:
        start_contact_note(chat_guid, sender, contacts[0])
    else:
        top = contacts[:3]
        contact_list = "\n".join([
            f"{i+1}. {c['name']}" + (f" (last saw {c['last_saw'][:10]})" if c.get('last_saw') else "")
            for i, c in enumerate(top)
        ])
        sessions[sender] = {
            "state": "selecting_contact_for_note",
            "contacts": top,
            "chat_guid": chat_guid
        }
        send_message(chat_guid, f"Which {contact_name}?\n\n{contact_list}")


def start_contact_note(chat_guid: str, sender: str, contact: dict):
    sessions[sender] = {
        "state": "noting_contact",
        "contact": contact,
        "messages": [],
        "chat_guid": chat_guid
    }
    send_message(chat_guid, f"What do you know about {contact['name']}? Dump everything — say 'done' when finished.")


def handle_contact_note_response(chat_guid: str, sender: str, text: str, session: dict):
    if session.get("state") == "selecting_contact_for_note":
        try:
            idx = int(text.strip()) - 1
            contacts = session.get("contacts", [])
            if 0 <= idx < len(contacts):
                start_contact_note(chat_guid, sender, contacts[idx])
            else:
                send_message(chat_guid, "Please reply with a number from the list.")
        except ValueError:
            send_message(chat_guid, "Please reply with the number of the contact.")
        return

    if not session.get("contact"):
        contacts = search_contacts(text.strip())
        if not contacts:
            send_message(chat_guid, f"No contact found for {text.strip()}.")
            sessions.pop(sender, None)
        elif len(contacts) == 1:
            start_contact_note(chat_guid, sender, contacts[0])
        else:
            top = contacts[:3]
            contact_list = "\n".join([
                f"{i+1}. {c['name']}" + (f" (last saw {c['last_saw'][:10]})" if c.get('last_saw') else "")
                for i, c in enumerate(top)
            ])
            session["state"] = "selecting_contact_for_note"
            session["contacts"] = top
            sessions[sender] = session
            send_message(chat_guid, f"Which one?\n\n{contact_list}")
        return

    if text.strip().lower() == "done":
        if not session.get("messages"):
            send_message(chat_guid, "You haven't shared anything yet — tell me about them first.")
            return
        send_message(chat_guid, "Give me a sec...")
        finalize_contact_note(chat_guid, sender, session)
    else:
        session["messages"].append(text)
        sessions[sender] = session


def finalize_contact_note(chat_guid: str, sender: str, session: dict):
    contact = session["contact"]
    notes_text = "\n".join(session["messages"])

    prompt = CONTACT_NOTE_PROMPT.format(
        contact_name=contact["name"],
        notes_text=notes_text
    )

    try:
        raw = get_claude_response(prompt, [{"role": "user", "content": "Process these notes."}], model="claude-sonnet-4-6")
        start = raw.find("{")
        end = raw.rfind("}") + 1
        parsed = json.loads(raw[start:end])
    except Exception as e:
        logger.error(f"Error parsing contact note response: {e}")
        send_message(chat_guid, "Had trouble processing — try again?")
        sessions.pop(sender, None)
        return

    success = write_contact_summary(contact["id"], parsed.get("bullets", []))
    if success:
        send_message(chat_guid, parsed.get("closing_message", f"Updated {contact['name']}'s profile!"))
    else:
        send_message(chat_guid, f"Couldn't write to Notion — check the connection.")
    sessions.pop(sender, None)


# ── CALENDAR QUERY ─────────────────────────────────────────────

def handle_calendar_query(chat_guid: str, sender: str, text: str, intent: dict):
    days_ahead = intent.get("days_ahead") or 7
    events = get_upcoming_events(days_ahead=days_ahead)

    if not events:
        send_message(chat_guid, "Nothing on your calendar for that period!")
        return

    today = datetime.now().strftime("%Y-%m-%d")
    event_lines = []
    for e in events:
        date = e["scheduled"][:10] if e.get("scheduled") else "?"
        line = f"{date}: {e['name']} ({e['type']})"
        if e.get("location"):
            line += f" @ {e['location']}"
        event_lines.append(line)

    events_text = "\n".join(event_lines)
    system = f"""You are Rocky, a personal AI assistant on iMessage. Today is {today}.

The user asked: "{text}"

Here are their upcoming calendar events:
{events_text}

Reply in a friendly, concise iMessage style. Group by day if there are multiple events. Keep it readable on a phone screen."""

    response = get_claude_response(system, [{"role": "user", "content": text}], model="claude-haiku-4-5")
    send_message(chat_guid, response)


# ── GENERAL ────────────────────────────────────────────────────

def handle_general_message(chat_guid: str, sender: str, text: str, needs_web_search: bool = False):
    if needs_web_search:
        logger.info("General message with web search, routing to claude-sonnet-4-6")
        response = get_claude_response(SYSTEM_PROMPT, [{"role": "user", "content": text}], enable_web_search=True)
    else:
        word_count = len(text.split())
        model = "claude-haiku-4-5" if word_count < 50 else "claude-sonnet-4-6"
        logger.info(f"General message, routing to {model}")
        response = get_claude_response(SYSTEM_PROMPT, [{"role": "user", "content": text}], model=model)
    send_message(chat_guid, response)


@app.route("/health", methods=["GET"])
def health():
    return jsonify({"ok": True, "status": "running"})


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port)
