
cat > solution/reporting.py <<'PY'
import csv
import json
from pathlib import Path
from datetime import datetime
from zoneinfo import ZoneInfo

from database import decrypt_value

IST = ZoneInfo("Asia/Kolkata")

BATCH_COLUMNS = [
    "workflow_id",
    "batch_id",
    "total_leads",
    "yet_to_reach_out",
    "in_progress_telephony_failed",
    "in_progress_dialed",
    "completed",
]

DAILY_COLUMNS = [
    "workflow_id",
    "date",
    "total_calls",
    "connected_calls",
    "connectivity_rate",
    "success_leads",
]

LEAD_COLUMNS = [
    "workflow_id",
    "external_ref",
    "batch_id",
    "phone_masked",
    "bucket",
    "total_calls",
    "connected_calls",
    "last_call_at",
    "current_disposition",
    "ever_success",
]

REJECTION_COLUMNS = [
    "workflow_id",
    "batch_id",
    "row_number",
    "reason",
]


def to_ist(value):
    timestamp = datetime.fromisoformat(value.replace("Z", "+00:00"))

    if timestamp.tzinfo is None:
        timestamp = timestamp.replace(tzinfo=IST)

    return timestamp.astimezone(IST)


def load_dispositions(path="assignment-a-data/schemas/dispositions.csv"):
    result = {}

    with open(path, newline="", encoding="utf-8-sig") as file:
        for row in csv.DictReader(file):
            result[(row["workflow_id"], row["code"])] = {
                "label": row["label"],
                "terminal": row["is_terminal"].lower() == "true",
                "success": row["is_success"].lower() == "true",
            }

    return result


def write_csv(path, columns, rows):
    with open(path, "w", newline="", encoding="utf-8") as file:
        writer = csv.DictWriter(file, fieldnames=columns)
        writer.writeheader()
        writer.writerows(rows)


def generate_reports(database, output_dir, quality, rejections):
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    connection = database.connection
    dispositions = load_dispositions()

    lead_rows = connection.execute(
        "SELECT * FROM leads ORDER BY workflow_id, external_ref"
    ).fetchall()

    event_rows = connection.execute(
        """
        SELECT event_id, event_type, workflow_id, call_id,
               lead_id, occurred_at, payload
        FROM interaction_events
        WHERE lead_id IS NOT NULL
        ORDER BY occurred_at
        """
    ).fetchall()

    events_by_lead = {}
    events_by_call = {}

    for row in event_rows:
        event = dict(row)

        try:
            event["data"] = json.loads(event["payload"])
        except (ValueError, TypeError):
            event["data"] = {}

        events_by_lead.setdefault(event["lead_id"], []).append(event)

        if event["call_id"]:
            key = (event["workflow_id"], event["call_id"])
            events_by_call.setdefault(key, []).append(event)

    calls_by_lead = {}
    daily_calls = {}

    for lead in lead_rows:
        lead_id = lead["id"]
        workflow_id = lead["workflow_id"]

        initiated = [
            event
            for event in events_by_lead.get(lead_id, [])
            if event["event_type"] == "call.initiated"
        ]

        calls = []

        for start in initiated:
            call_id = start["call_id"]

            call_events = events_by_call.get(
                (workflow_id, call_id), []
            )

            connected = any(
                event["event_type"] == "call.connected"
                for event in call_events
            )

            telephony_failed = any(
                event["event_type"] == "call.telephony_failed"
                for event in call_events
            )

            override_events = [
                event
                for event in call_events
                if event["event_type"] == "disposition.override"
            ]

            set_events = [
                event
                for event in call_events
                if event["event_type"] == "disposition.set"
            ]

            disposition_event = None

            if override_events:
                disposition_event = max(
                    override_events,
                    key=lambda item: item["occurred_at"],
                )

            elif set_events:
                disposition_event = max(
                    set_events,
                    key=lambda item: item["occurred_at"],
                )

            code = None
            disposition = None

            if disposition_event:
                payload = disposition_event["data"].get("payload", {})

                code = (
                    payload.get("disposition_code")
                    or payload.get("code")
                )

                disposition = dispositions.get(
                    (workflow_id, code)
                )

            call_time = to_ist(start["occurred_at"])

            call = {
                "call_id": call_id,
                "time": call_time,
                "connected": connected,
                "telephony_failed": telephony_failed,
                "code": code,
                "disposition": disposition,
            }

            calls.append(call)

            day_key = (
                workflow_id,
                call_time.strftime("%Y-%m-%d"),
            )

            daily = daily_calls.setdefault(
                day_key,
                {
                    "calls": 0,
                    "connected": 0,
                    "successful_leads": set(),
                },
            )

            daily["calls"] += 1

            if connected:
                daily["connected"] += 1

            if disposition and disposition["success"]:
                daily["successful_leads"].add(lead_id)

        calls_by_lead[lead_id] = calls

    batch_counts = {}
    lead_report = []

    for lead in lead_rows:
        lead_id = lead["id"]
        workflow_id = lead["workflow_id"]
        batch_id = lead["batch_id"]

        calls = calls_by_lead.get(lead_id, [])

        terminal = any(
            call["disposition"]
            and call["disposition"]["terminal"]
            for call in calls
        )

        if not calls:
            bucket = "yet_to_reach_out"

        elif terminal:
            bucket = "completed"

        elif all(
            call["telephony_failed"]
            for call in calls
        ):
            bucket = "in_progress_telephony_failed"

        else:
            bucket = "in_progress_dialed"

        batch_key = (workflow_id, batch_id)

        counts = batch_counts.setdefault(
            batch_key,
            {
                "total_leads": 0,
                "yet_to_reach_out": 0,
                "in_progress_telephony_failed": 0,
                "in_progress_dialed": 0,
                "completed": 0,
            },
        )

        counts["total_leads"] += 1
        counts[bucket] += 1

        latest_call = (
            max(calls, key=lambda call: call["time"])
            if calls
            else None
        )

        latest_disposition_call = max(
            (
                call
                for call in calls
                if call["code"]
            ),
            key=lambda call: call["time"],
            default=None,
        )

        current_disposition = (
            latest_disposition_call["code"]
            if latest_disposition_call
            else ""
        )

        ever_success = any(
            call["disposition"]
            and call["disposition"]["success"]
            for call in calls
        )

        phone = decrypt_value(lead["phone_encrypted"])
        masked_phone = "+91******" + phone[-4:]

        lead_report.append({
            "workflow_id": workflow_id,
            "external_ref": lead["external_ref"],
            "batch_id": batch_id,
            "phone_masked": masked_phone,
            "bucket": bucket,
            "total_calls": len(calls),
            "connected_calls": sum(
                call["connected"] for call in calls
            ),
            "last_call_at": (
                latest_call["time"].isoformat()
                if latest_call
                else ""
            ),
            "current_disposition": current_disposition,
            "ever_success": str(ever_success).lower(),
        })

    batch_report = []

    for (workflow_id, batch_id), counts in batch_counts.items():
        batch_report.append({
            "workflow_id": workflow_id,
            "batch_id": batch_id,
            **counts,
        })

    batch_report.sort(
        key=lambda row: (
            row["workflow_id"],
            row["batch_id"],
        )
    )

    lead_report.sort(
        key=lambda row: (
            row["workflow_id"],
            row["external_ref"],
        )
    )

    daily_report = []

    for (workflow_id, day), values in daily_calls.items():
        total = values["calls"]

        daily_report.append({
            "workflow_id": workflow_id,
            "date": day,
            "total_calls": total,
            "connected_calls": values["connected"],
            "connectivity_rate": (
                f"{values['connected'] / total:.4f}"
            ),
            "success_leads": len(values["successful_leads"]),
        })

    daily_report.sort(
        key=lambda row: (
            row["workflow_id"],
            row["date"],
        )
    )

    rejections = sorted(
        rejections,
        key=lambda row: (
            row["workflow_id"],
            row["batch_id"],
            row["row_number"],
        ),
    )

    write_csv(
        output_dir / "batch_summary.csv",
        BATCH_COLUMNS,
        batch_report,
    )

    write_csv(
        output_dir / "daily_overview.csv",
        DAILY_COLUMNS,
        daily_report,
    )

    write_csv(
        output_dir / "lead_report.csv",
        LEAD_COLUMNS,
        lead_report,
    )

    write_csv(
        output_dir / "rejections.csv",
        REJECTION_COLUMNS,
        rejections,
    )

    with open(
        output_dir / "data_quality.json",
        "w",
        encoding="utf-8",
    ) as file:
        json.dump(quality, file, indent=2)

    return output_dir
PY
