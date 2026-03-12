import os
import json
import logging
import httpx
from flask import Flask, request, jsonify
from apscheduler.schedulers.background import BackgroundScheduler
from datetime import datetime
import pytz

from telegram_client import send_message
from supabase_client import get_active_session, create_session, update_session, close_session
from notion_client_wrapper import get_todays_events, update_event_notes, update_contact
from claude_client import get_claude_response

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

app = Flask(__name__)

TELEGRAM_CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID")
TIMEZONE = os.environ.get("TIMEZONE", "America/Los_Angeles")


@app.route("/webhook", methods=["POST"])
def webhook():
    data = request.json
    logger.info(f"Incoming webhook: {json.dumps(data)}")

    message = data.get("message", {})
    if not message:
        return jsonify({"ok": True})

    chat_id = str(message.get("chat", {}).get("id", ""))
    text = message.get("text", "").strip()

    if not chat_id or not text:
        return jsonify({"ok": True})

    try:
        handle_message(chat_id, text)
    except Exception as e:
        logger.error(f"Error handling message: {e}", exc_info=True)
        send_message(chat_id, "Sorry, something went wrong. Please try again.")

    return jsonify({"ok": True})


def handle_message(chat_id: str, text: str):
    session = get_active_session(chat_id)

    if not session:
        send_message(chat_id, "No active recap session. I'll message you tonight at 10pm!")
        return

    events = session["events"]
    current_index = session["current_event_index"]
    history = session["conversation_history"] or []

    if current_index >= len(events):
        send_message(chat_id, "You've recapped all your events for today. Great job! 🎉")
        close_session(session["id"])
        return

    current_event = events[current_index]

    # Add user message to history
    history.append({"role": "user", "content": text})

    # Get Claude's response
    system_prompt = f"""You are a warm, conversational personal assistant helping the user recap their day over Telegram.

You are currently discussing this calendar event:
- Title: {current_event.get('title', 'Unknown event')}
- Type: {current_event.get('type', 'Unknown')}
- People involved: {', '.join(current_event.get('people', [])) if current_event.get('people') else 'No one listed'}

Your job is to ask natural follow-up questions to get a good summary of what happened.
After 3-5 exchanges, when you have enough information, respond with ONLY this exact JSON (no other text):
{{"done": true, "summary": "2-3 sentence summary of what happened", "followups": ["specific follow-up action if any"], "next_message": "Short friendly message to transition to next event or close out"}}

Until then, ask one focused follow-up question at a time. Be conversational, warm, and concise. Remember this is SMS so keep messages short."""

    response_text = get_claude_response(system_prompt, history)

    # Try to parse as JSON (done signal)
    try:
        # Look for JSON in the response
        start = response_text.find("{")
        end = response_text.rfind("}") + 1
        if start >= 0 and end > start:
            parsed = json.loads(response_text[start:end])
            if parsed.get("done"):
                # Write to Notion
                summary = parsed.get("summary", "")
                followups = parsed.get("followups", [])
                next_message = parsed.get("next_message", "Moving on!")

                # Update Notion event page
                update_event_notes(
                    page_id=current_event["id"],
                    summary=summary,
                    followups=followups
                )

                # Update contact pages
                for contact in current_event.get("contacts", []):
                    update_contact(
                        page_id=contact["id"],
                        name=contact.get("name", ""),
                        summary=summary,
                        followups=followups,
                        event_title=current_event.get("title", "")
                    )

                # Move to next event
                new_index = current_index + 1
                update_session(session["id"], {
                    "current_event_index": new_index,
                    "conversation_history": []
                })

                send_message(chat_id, next_message)

                # Check if more events
                if new_index < len(events):
                    next_event = events[new_index]
                    send_message(chat_id, f"Next up: *{next_event['title']}*. What happened?")
                else:
                    send_message(chat_id, "That's all your events for today. Great recap! 🎉")
                    close_session(session["id"])

                return
    except (json.JSONDecodeError, ValueError):
        pass

    # Still conversing — add assistant response to history and update session
    history.append({"role": "assistant", "content": response_text})
    update_session(session["id"], {"conversation_history": history})
    send_message(chat_id, response_text)


def nightly_recap():
    """Runs at 10pm to kick off the daily recap."""
    logger.info("Running nightly recap trigger...")
    
    if not TELEGRAM_CHAT_ID:
        logger.error("TELEGRAM_CHAT_ID not set")
        return

    try:
        events = get_todays_events()
        if not events:
            logger.info("No events today, skipping recap.")
            return

        # Create a new session
        create_session(TELEGRAM_CHAT_ID, events)

        # Send opening message
        event_titles = [e["title"] for e in events]
        if len(event_titles) == 1:
            intro = f"Hey! You had *{event_titles[0]}* today."
        else:
            listed = ", ".join(f"*{t}*" for t in event_titles[:-1]) + f" and *{event_titles[-1]}*"
            intro = f"Hey! You had {listed} today."

        send_message(TELEGRAM_CHAT_ID, f"{intro}\n\nLet's do a quick recap. What happened during *{event_titles[0]}*?")

    except Exception as e:
        logger.error(f"Error in nightly recap: {e}", exc_info=True)


@app.route("/trigger-recap", methods=["POST"])
def trigger_recap():
    """Manual trigger endpoint for testing."""
    nightly_recap()
    return jsonify({"ok": True, "message": "Recap triggered"})


@app.route("/health", methods=["GET"])
def health():
    return jsonify({"ok": True, "status": "running"})


if __name__ == "__main__":
    # Set up scheduler for 10pm nightly
    scheduler = BackgroundScheduler(timezone=TIMEZONE)
    scheduler.add_job(nightly_recap, "cron", hour=22, minute=0)
    scheduler.start()
    logger.info(f"Scheduler started - recap will run at 10pm {TIMEZONE}")

    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port)
