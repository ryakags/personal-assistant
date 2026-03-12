import os
import httpx
import logging
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
        return results[0] if results else None
    except Exception as e:
        logger.error(f"Failed to get session: {e}")
        return None


def create_session(phone: str, events: list) -> Optional[dict]:
    """Create a new recap session."""
    try:
        response = httpx.post(
            f"{SUPABASE_URL}/rest/v1/sessions",
            headers=HEADERS,
            json={
                "phone": phone,
                "status": "active",
                "events": events,
                "current_event_index": 0,
                "conversation_history": []
            },
            timeout=10
        )
        response.raise_for_status()
        results = response.json()
        return results[0] if results else None
    except Exception as e:
        logger.error(f"Failed to create session: {e}")
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
