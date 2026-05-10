import os
import json
import logging
import requests
from flask import Flask, request, jsonify
from datetime import datetime
from claude_client import get_claude_response
from notion_tools import NOTION_TOOLS

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

app = Flask(__name__)

MY_NUMBER = "+19168331436"
BLUEBUBBLES_URL = os.environ.get("BLUEBUBBLES_URL", "http://localhost:1234")
BLUEBUBBLES_PASSWORD = os.environ.get("BLUEBUBBLES_PASSWORD", "")

# sender -> {"messages": [{role, content}, ...], "chat_guid": str}
sessions = {}

MAX_HISTORY = 30  # cap conversation history per sender

SYSTEM_PROMPT = """You are Rocky, Ryan Kageyama's personal AI assistant over iMessage. Be helpful, concise, and punchy — this is iMessage, not email. Keep responses short and phone-screen friendly.

TODAY: {today}

You have access to Ryan's Notion calendar and contacts database via the notion_* tools.

CALENDAR EVENT TYPES (use exact strings):
Exercise, Dinner, Concert, Reminder, Comedy, Call, Vacation, Lunch, Party, Coffee, FaceTime, Happy Hour, Sports, Wedding, Festival, Work, Food, Remote Work Trip, Haircut, Movie, Coffee Club, Podcast, Appointment, Art, Date, Comedy Show, Basketball, Therapy, Birthday, Drinks, Hangout, Grocery, Laundry, Beach, Airport, Speaker Event, Open Mic, Errand, Breakfast, Cowork, Cultural Event, Volunteering, Sick, Music, Art Show, Doctors, Pop Up, Bars, Project Work, Travel, Brunch, Self Care, Theater, Trivia, Meeting, Broadway, Clubbing, Baseball, Bachelor Party, House Warming, Visitors, Short Trip, Holiday Trip

--- CAPABILITIES ---

CALENDAR QUERY: Use notion_query_calendar with date_from/date_to for date ranges.
- Future queries: date_from=today, date_to=N days out
- Past queries: date_from=N days ago, date_to=today

ADD TO CALENDAR: Collect name, date, and event_type (infer what you can). Confirm once, then notion_create_calendar_event.

EDIT EVENT PAGE: Find event with notion_query_calendar, then notion_append_to_page.

UPDATE PEOPLE INVOLVED:
1. notion_query_calendar to get the event (includes current people_ids + names)
2. notion_search_contacts to find the contact
3. notion_update_event_people with the full updated list of contact IDs

--- MULTI-TURN FLOWS ---

EVENT RECAP (triggered by "recap", "let's recap", "review [event]"):
1. notion_query_calendar to find the event
2. Reply EXACTLY: "Recap mode for [Event Name] on [date]. Dump everything — say 'done' when finished."
3. For every message the user sends until "done": reply with ONLY "👍" — no commentary, no other text
4. When user says "done":
   a. Synthesize all the accumulated notes from the conversation
   b. Write a 3-5 sentence past-tense summary → notion_write_recap_to_event
   c. For EACH person mentioned in the notes: write bullets + personal facts → notion_write_contact_recap
      - bullets: key observations about this person at the event
      - facts: personal details (job, life updates, plans, relationships, opinions, etc.)
   d. Confirm briefly: "Recap saved! Updated [Name1] and [Name2]'s profiles." (or similar)

CONTACT UPDATE (triggered by "update [name]", "add notes about [name]", "update [name]'s profile"):
1. notion_search_contacts to find the person
2. Reply EXACTLY: "What do you know about [Name]? Dump everything — say 'done' when finished."
3. For every message until "done": reply with ONLY "👍"
4. When user says "done":
   a. Extract 5-15 standalone fact bullets
   b. Write → notion_write_contact_summary
   c. Confirm: "[Name]'s profile updated!"

--- RULES ---
- If a contact isn't found when needed, offer to create them (notion_create_contact)
- For People Involved updates: always fetch the current list from the event before adding/removing
- During note accumulation (between starting a recap/contact flow and "done"): send ONLY "👍"
- If the user sends something clearly off-topic during accumulation (e.g. asks a question), reply: "Still in [recap/update] mode — send 'done' to finish or 'cancel' to exit."
- Web search: use it for current events, news, sports scores, weather, stock prices
- Keep all responses short"""


def send_message(chat_guid: str, text: str):
    url = f"{BLUEBUBBLES_URL}/api/v1/message/text"
    payload = {
        "chatGuid": chat_guid,
        "tempGuid": f"temp-{datetime.now().timestamp()}",
        "message": text,
        "method": "private-api",
    }
    try:
        response = requests.post(url, json=payload, params={"password": BLUEBUBBLES_PASSWORD}, timeout=10)
        response.raise_for_status()
        logger.info(f"Sent message to {chat_guid}")
    except Exception as e:
        logger.error(f"Failed to send message: {e}", exc_info=True)


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
        logger.info(f"Ignoring message from: {sender}")
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
    # Global cancel
    if text.strip().lower() in ("cancel", "stop", "nevermind", "never mind"):
        if sender in sessions:
            sessions.pop(sender)
            send_message(chat_guid, "Cancelled.")
        else:
            send_message(chat_guid, "Nothing active to cancel.")
        return

    # Get or create session
    session = sessions.setdefault(sender, {"messages": [], "chat_guid": chat_guid})
    session["chat_guid"] = chat_guid
    session["messages"].append({"role": "user", "content": text})

    # Build system prompt with today's date
    today = datetime.now().strftime("%Y-%m-%d")
    system = SYSTEM_PROMPT.replace("{today}", today)

    # Determine whether web search is needed (quick keyword check — no extra API call)
    web_search_keywords = ("weather", "score", "news", "stock", "price", "today in", "latest", "current", "who won", "search")
    needs_web_search = any(kw in text.lower() for kw in web_search_keywords)

    response_text = get_claude_response(
        system_prompt=system,
        messages=session["messages"],
        enable_web_search=needs_web_search,
        notion_tools=NOTION_TOOLS,
    )

    session["messages"].append({"role": "assistant", "content": response_text})

    # Keep history bounded
    if len(session["messages"]) > MAX_HISTORY:
        session["messages"] = session["messages"][-MAX_HISTORY:]

    sessions[sender] = session
    send_message(chat_guid, response_text)


@app.route("/health", methods=["GET"])
def health():
    return jsonify({"ok": True, "status": "running"})


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port)
