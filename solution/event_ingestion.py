
import json
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

from normalization import normalize_phone
from database import hash_phone

IST = ZoneInfo("Asia/Kolkata")


def normalize_event_phone(value):
    if not value:
        return None

    try:
        return normalize_phone(str(value))
    except ValueError:
        return None


def normalize_event_timestamp(value):
    try:
        timestamp = datetime.fromisoformat(
            value.replace("Z", "+00:00")
        )

        if timestamp.tzinfo is None:
            timestamp = timestamp.replace(tzinfo=IST)

        return timestamp.astimezone(timezone.utc).isoformat()

    except (ValueError, TypeError):
        raise ValueError("invalid_timestamp")


def read_event_file(file_path):
    events = []
    events_total = 0
    malformed_events = 0

    with open(file_path, "r", encoding="utf-8") as file:
        for line in file:
            if not line.strip():
                continue

            events_total += 1

            try:
                event = json.loads(line)

                if not isinstance(event, dict) or not event.get("event_id"):
                    malformed_events += 1
                    continue

                events.append(event)

            except json.JSONDecodeError:
                malformed_events += 1

    return events, events_total, malformed_events


def remove_duplicate_events(events, seen_event_ids):
    unique_events = []
    duplicates = 0

    for event in events:
        event_id = event["event_id"]

        if event_id in seen_event_ids:
            duplicates += 1
            continue

        seen_event_ids.add(event_id)
        unique_events.append(event)

    return unique_events, duplicates


def find_event_lead(database, event):
    phone = normalize_event_phone(event.get("phone"))

    if phone is not None:
        result = database.find_lead_by_phone(
            event["workflow_id"],
            phone
        )

        if result is not None:
            return result["id"]

    if event.get("event_type") == "disposition.override":
        return database.find_lead_by_call_id(
            event["workflow_id"],
            event.get("call_id")
        )

    return None


def process_events(database, events, seen_event_ids):
    unique_events = []
    duplicate_count = 0

    for event in events:
        event_id = event.get("event_id")

        if not event_id:
            continue

        if event_id in seen_event_ids:
            duplicate_count += 1
            continue

        try:
            event["occurred_at"] = normalize_event_timestamp(
                event["occurred_at"]
            )
        except (KeyError, ValueError):
            continue

        seen_event_ids.add(event_id)

        if not database.claim_event_id(event_id):
            duplicate_count += 1
            continue

        unique_events.append(event)

    orphan_count = 0
    stored_count = 0

    for event in unique_events:
        lead_id = find_event_lead(database, event)

        if lead_id is None:
            phone = normalize_event_phone(event.get("phone"))
            phone_hash = hash_phone(phone) if phone else None

            database.store_orphan_event(event, phone_hash)
            orphan_count += 1
        else:
            database.store_event(event, lead_id)
            stored_count += 1

    return {
        "stored_events": stored_count,
        "orphan_events": orphan_count,
        "duplicate_events": duplicate_count,
    }
