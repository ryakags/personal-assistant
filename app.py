import os
import json
import logging
import requests
from flask import Flask, request, jsonify
from datetime import datetime
from notion_client_wrapper import search_events, write_event_notes
from claude_client import get_claude_response

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

app = Flask(__name__)

MY_NUMBER = "+19168331436"
BLUEBUBBLES_URL = os.environ.get("BLUEBUBBLES_URL", "http://localhost:1234")
BLUEBUBBLES_PASSWORD = os.environ.get("BLUEBUBBLES_PASSWORD", "")

# In-memory session store: { phone_number: session_dict }
# session_dict keys: event, history, state ("reviewing" | None)
sessions = {}

SYSTEM_PROMPT = """You are Rocky, a personal AI assistant available over iMessage. You are helpful, concise, and conversational — this is iMessage, not email. Keep responses short and punchy. You can help with anything: questions, drafting, thinking through problems, recommendations, math, etc.

You also have access to the user's Notion calendar. When the user wants to review or recap a past activity, you will be given details about the event and asked to conduct the review."""

INTENT_PROMPT = """You are analyzing a message to determine if the user wants to review/recap a past calendar event.

Return a JSON object with:
- "is_review": true/false — whether they want to review an event
- "date": ISO date string like "2026-04-13" or null if today/recent
- "event_type": one of the Notion event types or null (e.g. "Exercise", "Dinner", "Lunch", "Coffee", "Meeting", "Workout")
- "name_query": a partial name to search for, or null
- "days_back": how many days back to search (default 1)

Notion event types: Exercise, Dinner, Concert, Reminder, Comedy, Call, Vacation, Lunch, Party, Coffee, FaceTime, Happy Hour, Sports, Wedding, Festival, Work, Food, Remote Work Trip, Haircut, Movie, Coffee Club, Podcast, Appointment, Art, Date, Comedy Show, Basketball, Therapy, Notes & Food Planning, Birthday, Drinks, Hangout, Grocery, Laundry, Beach, Airport, Speaker Event, Open Mic, Errand, Breakfast, Cowork, Cultural Event, Volunteering, Sick, Music, Art Show, Doctors, Pop Up, Canceled, Bars, Project Work, Travel, Brunch, Self Care, Theater, Trivia, Meeting, Broadway

Examples:
- "let's recap my workout today" → {"is_review": true, "date": null, "event_type": "Exercise", "name_query": null, "days_back": 1}
- "review dinner with Sarah last night" → {"is_review": true, "date": null, "event_type": "Dinner", "name_query": "Sarah", "days_back": 2}
- "hey what's the weather" → {"is_review": false, "date": null, "event_type": null, "name_query": null, "days_back": 1}
- "recap my meeting on monday" → {"is_review": true, "date": null, "event_type": "Meeting", "name_query": null, "days_back": 7}

Today's date is: {today}

Respond with ONLY the JSON object, no other text."""


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

    # If in an active review session, continue it
    if session.get("state") == "reviewing":
        handle_review_response(chat_guid, sender, text, session)
        return

    # Check if this is a review/recap intent
    intent = detect_review_intent(text)

    if intent and intent.get("is_review"):
        start_review_session(chat_guid, sender, text, intent)
    else:
        handle_general_message(chat_guid, sender, text)


def detect_review_intent(text: str) -> dict | None:
    """Use Claude to detect if the user wants to review a calendar event."""
    try:
        today = datetime.now().strftime("%Y-%m-%d")
        prompt = INTENT_PROMPT.replace("{today}", today)
        messages = [{"role": "user", "content": text}]
        response = get_claude_response(prompt, messages, model="claude-haiku-4-5")
        parsed = json.loads(response.strip())
        return parsed
    except Exception as e:
        logger.error(f"Error detecting intent: {e}")
        return None


def start_review_session(chat_guid: str, sender: str, text: str, intent: dict):
    """Find matching events and start a review session."""
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
        # Only one match — start the review
        event = events[0]
        start_event_review(chat_guid, sender, event)
    else:
        # Multiple matches — ask which one
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
    """Kick off the review conversation for a specific event."""
    sessions[sender] = {
        "state": "reviewing",
        "event": event,
        "history": [],
        "chat_guid": chat_guid
    }

    event_desc = f"{event['name']}"
    if event.get("type"):
        event_desc += f" ({event['type']})"
    if event.get("scheduled"):
        event_desc += f" on {event['scheduled'][:10]}"
    if event.get("location"):
        event_desc += f" at {event['location']}"

    review_system = build_review_system_prompt(event)
    opening_messages = [{"role": "user", "content": f"Let's review: {event_desc}"}]
    opening = get_claude_response(review_system, opening_messages, model="claude-sonnet-4-6")

    sessions[sender]["history"].append({"role": "assistant", "content": opening})
    send_message(chat_guid, opening)


def handle_review_response(chat_guid: str, sender: str, text: str, session: dict):
    """Handle a message during an active review session."""

    # Handle event selection
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

    # Check if review is done (Claude returns JSON with done:true)
    try:
        start = response_text.find("{")
        end = response_text.rfind("}") + 1
        if start >= 0 and end > start:
            parsed = json.loads(response_text[start:end])
            if parsed.get("done"):
                summary = parsed.get("summary", "")
                closing = parsed.get("closing_message", "Got it, saved to Notion! ✅")

                # Write notes back to Notion
                success = write_event_notes(event["id"], summary)
                if success:
                    send_message(chat_guid, closing)
                else:
                    send_message(chat_guid, f"{closing}\n\n(Note: couldn't save to Notion — check the connection.)")

                # Clear session
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

Your job is to have a short, natural conversation to capture what happened. Keep it casual and conversational — this is iMessage.

RULES:
- Ask 2-3 focused questions max, all in one message
- Keep messages short
- After 1-2 rounds of answers, wrap up

When you have enough info, respond with ONLY this JSON (no other text):
{{"done": true, "summary": "A concise summary of what happened, 2-5 sentences.", "closing_message": "Short friendly closing message"}}

Start by acknowledging the event and asking your first questions."""


def handle_general_message(chat_guid: str, sender: str, text: str):
    """Handle general conversation."""
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
