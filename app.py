import os
import json
import logging
import requests
from flask import Flask, request, jsonify
from datetime import datetime
from notion_client_wrapper import search_events, write_event_notes, create_calendar_event, append_page_blocks
from claude_client import get_claude_response

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

app = Flask(__name__)

MY_NUMBER = "+19168331436"
BLUEBUBBLES_URL = os.environ.get("BLUEBUBBLES_URL", "http://localhost:1234")
BLUEBUBBLES_PASSWORD = os.environ.get("BLUEBUBBLES_PASSWORD", "")

sessions = {}

SYSTEM_PROMPT = """You are Rocky, a personal AI assistant available over iMessage. You are helpful, concise, and conversational — this is iMessage, not email. Keep responses short and punchy. You can help with anything: questions, drafting, thinking through problems, recommendations, math, etc.

You also have access to the user's Notion calendar. When the user wants to review a past activity or add something to their calendar, you will handle that."""

INTENT_PROMPT = """You are analyzing a message to determine the user's intent.

Return a JSON object with:
- "intent": one of "review", "add_to_calendar", "edit_page", or "general"
- "date": ISO date string like "2026-04-13" or null
- "event_type": Notion event type or null (e.g. "Exercise", "Dinner", "Lunch", "Coffee", "Meeting")
- "name_query": partial name to search for, or null
- "days_back": how many days back to search (default 1, for review/edit_page intent only)

Notion event types: Exercise, Dinner, Concert, Reminder, Comedy, Call, Vacation, Lunch, Party, Coffee, FaceTime, Happy Hour, Sports, Wedding, Festival, Work, Food, Remote Work Trip, Haircut, Movie, Coffee Club, Podcast, Appointment, Art, Date, Comedy Show, Basketball, Therapy, Birthday, Drinks, Hangout, Grocery, Laundry, Beach, Airport, Speaker Event, Open Mic, Errand, Breakfast, Cowork, Cultural Event, Volunteering, Sick, Music, Art Show, Doctors, Pop Up, Bars, Project Work, Travel, Brunch, Self Care, Theater, Trivia, Meeting, Broadway, Bars, Clubbing, Baseball, Bachelor Party, House Warming, Visitors, Short Trip, Holiday Trip

"add_to_calendar" intent examples: "add dinner with Jake to my calendar", "put my workout on the cal", "add to my notion", "create a calendar event for lunch tomorrow", "schedule a meeting for friday"
"review" intent examples: "let's recap my workout today", "review dinner with Sarah last night", "recap my meeting"
"edit_page" intent examples: "update my workout today", "add notes to my dinner last night", "edit my meeting from this morning", "I want to add something to today's workout page"
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
    if session.get("state") == "reviewing":
        handle_review_response(chat_guid, sender, text, session)
        return
    if session.get("state") == "selecting_event":
        handle_review_response(chat_guid, sender, text, session)
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

    # Detect intent
    intent = detect_intent(text)
    logger.info(f"Detected intent: {intent}")

    if intent and intent.get("intent") == "review":
        start_review_session(chat_guid, sender, text, intent)
    elif intent and intent.get("intent") == "add_to_calendar":
        start_create_event_session(chat_guid, sender, text)
    elif intent and intent.get("intent") == "edit_page":
        start_edit_page_session(chat_guid, sender, text, intent)
    else:
        handle_general_message(chat_guid, sender, text)


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


# ── REVIEW SESSION ──────────────────────────────────────────────

def start_review_session(chat_guid: str, sender: str, text: str, intent: dict):
    events = search_events(
        query_date=intent.get("date"),
        event_type=intent.get("event_type"),
        name_query=intent.get("name_query"),
        days_back=intent.get("days_back", 1)
    )

    if not events:
        send_message(chat_guid, "I couldn't find any matching events in your calendar. Can you be more specific?")
        return

    if len(events) == 1:
        start_event_review(chat_guid, sender, events[0])
    else:
        event_list = "\n".join([
            f"{i+1}. {e['name']} ({e['type']}) — {e['scheduled'][:10]}"
            for i, e in enumerate(events[:5])
        ])
        sessions[sender] = {
            "state": "selecting_event",
            "events": events[:5],
            "chat_guid": chat_guid
        }
        send_message(chat_guid, f"Found a few events. Which one?\n\n{event_list}")


def start_event_review(chat_guid: str, sender: str, event: dict):
    sessions[sender] = {
        "state": "reviewing",
        "event": event,
        "history": [],
        "chat_guid": chat_guid
    }

    review_system = build_review_system_prompt(event)
    event_desc = event['name']
    if event.get('scheduled'):
        event_desc += f" on {event['scheduled'][:10]}"
    opening_messages = [{"role": "user", "content": f"Let's review: {event_desc}"}]
    opening = get_claude_response(review_system, opening_messages, model="claude-sonnet-4-6")

    sessions[sender]["history"].append({"role": "assistant", "content": opening})
    send_message(chat_guid, opening)


def handle_review_response(chat_guid: str, sender: str, text: str, session: dict):
    if session.get("state") == "selecting_event":
        try:
            idx = int(text.strip()) - 1
            events = session.get("events", [])
            if 0 <= idx < len(events):
                start_event_review(chat_guid, sender, events[idx])
            else:
                send_message(chat_guid, "Please reply with a number from the list.")
        except ValueError:
            send_message(chat_guid, "Please reply with the number of the event you want to review.")
        return

    event = session["event"]
    history = session["history"]
    history.append({"role": "user", "content": text})

    review_system = build_review_system_prompt(event)
    word_count = len(text.split())
    model = "claude-haiku-4-5" if word_count < 50 else "claude-sonnet-4-6"
    response_text = get_claude_response(review_system, history, model=model)

    try:
        start = response_text.find("{")
        end = response_text.rfind("}") + 1
        if start >= 0 and end > start:
            parsed = json.loads(response_text[start:end])
            if parsed.get("done"):
                summary = parsed.get("summary", "")
                closing = parsed.get("closing_message", "Saved to Notion! ✅")
                success = write_event_notes(event["id"], summary)
                msg = closing if success else f"{closing}\n\n(Couldn't save to Notion — check the connection.)"
                send_message(chat_guid, msg)
                sessions.pop(sender, None)
                return
    except (json.JSONDecodeError, ValueError):
        pass

    history.append({"role": "assistant", "content": response_text})
    session["history"] = history
    sessions[sender] = session
    send_message(chat_guid, response_text)


def build_review_system_prompt(event: dict) -> str:
    existing_notes = event.get("notes", "")
    notes_context = f"\nExisting notes: {existing_notes}" if existing_notes else ""

    return f"""You are Rocky, a personal AI assistant conducting a review of a calendar event over iMessage.

Event details:
- Name: {event.get('name', 'Unknown')}
- Type: {event.get('type', 'Unknown')}
- Date: {event.get('scheduled', 'Unknown')[:10] if event.get('scheduled') else 'Unknown'}
- Location: {event.get('location', 'Not specified')}{notes_context}

Your job is to have a short, natural conversation to capture what happened. Keep it casual — this is iMessage.

RULES:
- Ask 2-3 focused questions max, all in one message
- Keep messages short
- After 1-2 rounds of answers, wrap up

When you have enough info, respond with ONLY this JSON:
{{"done": true, "summary": "Concise summary, 2-5 sentences.", "closing_message": "Short friendly closing"}}

Start by acknowledging the event and asking your first questions."""


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
    events = search_events(
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


# ── GENERAL ────────────────────────────────────────────────────

def handle_general_message(chat_guid: str, sender: str, text: str):
    word_count = len(text.split())
    model = "claude-haiku-4-5" if word_count < 50 else "claude-sonnet-4-6"
    logger.info(f"General message, routing to {model}")
    messages = [{"role": "user", "content": text}]
    response = get_claude_response(SYSTEM_PROMPT, messages, model=model)
    send_message(chat_guid, response)


@app.route("/health", methods=["GET"])
def health():
    return jsonify({"ok": True, "status": "running"})


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port)
