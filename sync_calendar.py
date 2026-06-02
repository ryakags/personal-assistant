import logging
from gcal_client import fetch_events
from notion_client_wrapper import find_event_by_gcal_id, upsert_gcal_event

logger = logging.getLogger(__name__)


def sync_gcal_to_notion(days_back: int = 7, days_ahead: int = 30) -> dict:
    """Pull events from Google Calendar and upsert into Notion. Returns counts."""
    events = fetch_events(days_back=days_back, days_ahead=days_ahead)

    created = updated = failed = 0
    for event in events:
        try:
            existing_id = find_event_by_gcal_id(event["id"])
            success = upsert_gcal_event(event, page_id=existing_id)
            if success:
                if existing_id:
                    updated += 1
                else:
                    created += 1
            else:
                failed += 1
        except Exception as e:
            logger.error(f"Failed to sync event {event.get('id')}: {e}", exc_info=True)
            failed += 1

    logger.info(f"Sync complete — created: {created}, updated: {updated}, failed: {failed}")
    return {"created": created, "updated": updated, "failed": failed}
