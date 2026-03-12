import os
import httpx
import logging
import json
from typing import Optional

logger = logging.getLogger(__name__)

SUPABASE_URL = os.environ.get("SUPABASE_URL")
SUPABASE_KEY = os.environ.get("SUPABASE_KEY")

HEADERS = {
    "apikey": SUPABASE_KEY,
    "Authorization": f"Bearer {SUPABASE_KEY}",
    "Content-Type": "application/json",
    "Prefer": "return=representation"
}


def get_active_session(phone: str) -> Optional[dict]:
    """Get the active session for a given chat ID."""
    try:
        response = httpx.get(
            f"{SUPABASE_URL}/rest/v1/sessions",
            headers=HEADERS,
            params={
                "phone": f"eq.{phone}",
                "status": "eq.active",
                "order": "created_at.desc",
                "limit": "1"
            },
            timeout=10
        )
        response.raise_for_status()
        results = response.json()
        logger.info(f"Session lookup for {phone}: found {len(results)} rows")
        if results:
            session = results[0]
            logger.info(f"Session status={session.get('status')} events_null={session.get('events') is None} index={session.get('current_event_index')}")
            return session
        return None
    except Exception as e:
        logger.error(f"Failed to get session: {e}")
        return None


def create_session(phone: str, events: list) -> Optional[dict]:
    """Create a new active session, closing any previous ones first."""
    try:
        # Close any existing active sessions first
        httpx.patch(
            f"{SUPABASE_URL}/rest/v1/sessions",
            headers=HEADERS,
            params={"phone": f"eq.{phone}", "status": "eq.active"},
            json={"status": "complete"},
            timeout=10
        )

        # Create new session
        payload = {
            "phone": phone,
            "status": "active",
            "events": events,
            "current_event_index": 0,
            "conversation_history": []
        }
        logger.info(f"Creating session with {len(events)} events: {json.dumps(payload)[:200]}")

        response = httpx.post(
            f"{SUPABASE_URL}/rest/v1/sessions",
            headers=HEADERS,
            json=payload,
            timeout=10
        )
        response.raise_for_status()
        results = response.json()
        logger.info(f"Session created: {results}")
        return results[0] if results else None
    except Exception as e:
        logger.error(f"Failed to create session: {e}", exc_info=True)
        return None


def update_session(session_id: int, updates: dict) -> bool:
    """Update a session by ID."""
    try:
        response = httpx.patch(
            f"{SUPABASE_URL}/rest/v1/sessions",
            headers=HEADERS,
            params={"id": f"eq.{session_id}"},
            json=updates,
            timeout=10
        )
        response.raise_for_status()
        return True
    except Exception as e:
        logger.error(f"Failed to update session: {e}")
        return False


def close_session(session_id: int) -> bool:
    """Mark a session as complete."""
    return update_session(session_id, {"status": "complete"})
