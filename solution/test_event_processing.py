from database import Database
from event_ingestion import process_events


def test_process_linked_event():
    db = Database(":memory:")

    db.store_lead(
        {
            "workflow_id": "motor_renewal",
            "phone": "+919876543210",
            "customer_name": "Test Customer",
            "batch_id": "batch_001",
        },
        "POL-001",
    )

    events = [
        {
            "event_id": "evt_process_001",
            "event_type": "call.initiated",
            "workflow_id": "motor_renewal",
            "call_id": "call_process_001",
            "occurred_at": "2026-11-13T10:00:00Z",
            "phone": "9876543210",
        }
    ]

    result = process_events(db, events, set())

    assert result["stored_events"] == 1
    assert result["orphan_events"] == 0
    assert result["duplicate_events"] == 0

    count = db.connection.execute(
        "SELECT COUNT(*) FROM interaction_events"
    ).fetchone()[0]

    assert count == 1

    db.close()
