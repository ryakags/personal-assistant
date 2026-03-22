import os
import json
import logging
from flask import Flask, request, jsonify
from datetime import datetime

from telegram_client import send_message
from supabase_client import get_active_session, create_session, update_session, close_session
from notion_client_wrapper import get_todays_events, update_event_notes, update_contact
from claude_client import get_claude_response

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

app = Flask(__name__)

TELEGRAM_CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID")


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
    history.append({"role": "user", "content": text})

    system_prompt = f"""You are a warm, conversational personal assistant helping the user recap their day over Telegram.

You are currently discussing this calendar event:
- Title: {current_event.get('title', 'Unknown event')}
- Type: {current_event.get('type', 'Unknown')}
- People involved: {', '.join(current_event.get('people', [])) if current_event.get('people') else 'No one listed'}

Your job is to ask follow-up questions to get a good summary of what happened. Follow these rules strictly:

QUESTIONING RULES:
- Ask a MAXIMUM of 2-3 follow-up questions total across the whole conversation
- Always number your questions like: "1. How did it go?\n2. Any follow-ups needed?"
- Ask all your questions in one message — never one question at a time
- Keep messages short and conversational — this is Telegram, not email

WHEN TO WRAP UP:
- After the user has answered 1-2 rounds of questions, you have enough info — wrap up
- Do NOT keep asking more questions after that

SUMMARY FORMAT:
- Write the summary as 2-5 bullet points (use • character)
- Each bullet should be one clear, specific fact or takeaway
- Include any follow-up actions as the last bullet(s) if applicable

When you have enough info, respond with ONLY this exact JSON (no other text):
{{"done": true, "summary": "• Bullet one\n• Bullet two\n• Bullet three", "followups": ["follow-up action if any"], "next_message": "Short friendly transition message"}}

The summary field must use bullet points with the • character and \n between each bullet."""

    response_text = get_claude_response(system_prompt, history)

    # Try to parse as done JSON
    try:
        start = response_text.find("{")
        end = response_text.rfind("}") + 1
        if start >= 0 and end > start:
            parsed = json.loads(response_text[start:end])
            if parsed.get("done"):
                summary = parsed.get("summary", "")
                followups = parsed.get("followups", [])
                next_message = parsed.get("next_message", "Moving on!")

                update_event_notes(page_id=current_event["id"], summary=summary, followups=followups)

                for contact in current_event.get("contacts", []):
                    update_contact(
                        page_id=contact["id"],
                        name=contact.get("name", ""),
                        summary=summary,
                        followups=followups,
                        event_title=current_event.get("title", "")
                    )

                new_index = current_index + 1
                update_session(session["id"], {
                    "current_event_index": new_index,
                    "conversation_history": []
                })

                send_message(chat_id, next_message)

                if new_index < len(events):
                    next_event = events[new_index]
                    send_message(chat_id, f"Next up: *{next_event['title']}*. What happened?")
                else:
                    send_message(chat_id, "That's all your events for today. Great recap! 🎉")
                    close_session(session["id"])

                return
    except (json.JSONDecodeError, ValueError):
        pass

    history.append({"role": "assistant", "content": response_text})
    update_session(session["id"], {"conversation_history": history})
    send_message(chat_id, response_text)


def nightly_recap():
    """Triggered externally via cron-job.org hitting /trigger-recap."""
    logger.info("Running nightly recap trigger...")

    if not TELEGRAM_CHAT_ID:
        logger.error("TELEGRAM_CHAT_ID not set")
        return

    try:
        events = get_todays_events()
        if not events:
            logger.info("No events today, skipping recap.")
            return

        create_session(TELEGRAM_CHAT_ID, events)

        event_titles = [e["title"] for e in events]
        if len(event_titles) == 1:
            intro = f"Hey! You had *{event_titles[0]}* today."
        else:
            listed = ", ".join(f"*{t}*" for t in event_titles[:-1]) + f" and *{event_titles[-1]}*"
            intro = f"Hey! You had {listed} today."

        send_message(TELEGRAM_CHAT_ID, f"{intro}\n\nLet's do a quick recap. What happened during *{event_titles[0]}*?")

    except Exception as e:
        logger.error(f"Error in nightly recap: {e}", exc_info=True)


@app.route("/trigger-recap", methods=["GET", "POST"])
def trigger_recap():
    nightly_recap()
    return jsonify({"ok": True, "message": "Recap triggered"})


@app.route("/health", methods=["GET"])
def health():
    return jsonify({"ok": True, "status": "running"})


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port)
