
import argparse
import csv
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from database import Database
from lead_ingestion import load_schemas, process_lead_csv, apply_defaults
from event_ingestion import read_event_file, process_events
from reporting import generate_reports, load_dispositions


STATE_PATH = Path("data/ingestion_state.json")

EMPTY_QUALITY = {
    "lead_rows_total": 0,
    "lead_rows_rejected": 0,
    "rejections_by_reason": {},
    "duplicate_lead_rows_merged": 0,
    "field_warnings": {},
    "events_total": 0,
    "malformed_events": 0,
    "duplicate_events_dropped": 0,
    "orphan_events": 0,
    "unknown_disposition_events": 0,
}


def load_state():
    if STATE_PATH.exists():
        with open(STATE_PATH, "r", encoding="utf-8") as file:
            state = json.load(file)
    else:
        state = {}

    state.setdefault("processed_files", [])
    state.setdefault("quality", EMPTY_QUALITY.copy())
    state.setdefault("rejections", [])

    for key, value in EMPTY_QUALITY.items():
        state["quality"].setdefault(key, value.copy() if isinstance(value, dict) else value)

    return state


def save_state(state):
    STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
    temporary = STATE_PATH.with_suffix(".tmp")

    with open(temporary, "w", encoding="utf-8") as file:
        json.dump(state, file, indent=2)

    temporary.replace(STATE_PATH)


def ingest_leads(database, drop_dir, schemas, state):
    quality = state["quality"]
    processed = set(state["processed_files"])

    lead_dir = drop_dir / "leads"
    if not lead_dir.exists():
        return

    for csv_path in sorted(lead_dir.glob("*/*.csv")):
        file_key = str(csv_path)

        if file_key in processed:
            print(f"Already ingested: {csv_path}")
            continue

        workflow_id = csv_path.parent.name
        schema = schemas.get(workflow_id)

        if schema is None:
            print(f"Skipping unknown workflow: {workflow_id}")
            continue

        leads, rejections, warnings = process_lead_csv(csv_path, schema)

        quality["lead_rows_total"] += len(leads) + len(rejections)
        quality["lead_rows_rejected"] += len(rejections)

        for rejection in rejections:
            reason = rejection["reason"]
            quality["rejections_by_reason"][reason] = (
                quality["rejections_by_reason"].get(reason, 0) + 1
            )

        state["rejections"].extend(rejections)

        for field in warnings:
            quality["field_warnings"][field] = (
                quality["field_warnings"].get(field, 0) + 1
            )

        reference_field = next(
            field["name"]
            for field in schema["fields"]
            if field.get("reference") is True
        )

        batch_id = csv_path.stem

        for lead in leads:
            lead["workflow_id"] = workflow_id
            lead["batch_id"] = batch_id
            lead = apply_defaults(lead, schema)

            existing = database.find_lead_by_phone(
                workflow_id, lead["phone"]
            )

            if existing is not None:
                quality["duplicate_lead_rows_merged"] += 1

            database.store_lead(
                lead,
                lead.get(reference_field)
            )

        processed.add(file_key)
        state["processed_files"] = sorted(processed)
        save_state(state)

        print(
            f"Ingested {csv_path}: "
            f"{len(leads)} accepted, {len(rejections)} rejected"
        )


def ingest_events(database, drop_dir, state):
    quality = state["quality"]
    processed = set(state["processed_files"])
    dispositions = load_dispositions()

    event_dir = drop_dir / "events"
    if not event_dir.exists():
        return

    for event_path in sorted(event_dir.glob("*.jsonl")):
        file_key = str(event_path)

        if file_key in processed:
            print(f"Already ingested: {event_path}")
            continue

        events, total, malformed = read_event_file(event_path)

        quality["events_total"] += total
        quality["malformed_events"] += malformed

        valid_events = []

        for event in events:
            event_type = event.get("event_type")

            if event_type in ("disposition.set", "disposition.override"):
                payload = event.get("payload") or {}
                code = payload.get("disposition_code") or payload.get("code")
                workflow_id = event.get("workflow_id")

                if (workflow_id, code) not in dispositions:
                    quality["unknown_disposition_events"] += 1
                    continue

            valid_events.append(event)

        valid_events.sort(
            key=lambda event: event.get("event_type") == "disposition.override"
        )

        result = process_events(database, valid_events, set())

        quality["duplicate_events_dropped"] += result["duplicate_events"]
        quality["orphan_events"] += result["orphan_events"]

        processed.add(file_key)
        state["processed_files"] = sorted(processed)
        save_state(state)

        print(
            f"Ingested {event_path}: "
            f"{result['stored_events']} linked, "
            f"{result['orphan_events']} orphaned, "
            f"{result['duplicate_events']} duplicates"
        )


def run_ingest(drop_path):
    drop_dir = Path(drop_path)

    if not drop_dir.exists() or not drop_dir.is_dir():
        raise SystemExit(f"Input directory not found: {drop_dir}")

    schemas = load_schemas()
    state = load_state()
    database = Database()

    try:
        ingest_leads(database, drop_dir, schemas, state)
        ingest_events(database, drop_dir, state)
    finally:
        database.close()

    print("Ingestion completed successfully.")


def run_report(output_path):
    state = load_state()
    database = Database()

    try:
        generate_reports(
            database,
            output_path,
            state["quality"],
            state["rejections"],
        )
    finally:
        database.close()

    print(f"Reports generated in: {output_path}")


def main():
    parser = argparse.ArgumentParser(
        description="Interaction Ledger and Reporting Pipeline"
    )

    subparsers = parser.add_subparsers(dest="command", required=True)

    ingest_parser = subparsers.add_parser("ingest")
    ingest_parser.add_argument("directory")

    report_parser = subparsers.add_parser("report")
    report_parser.add_argument("--out", default="reports")

    args = parser.parse_args()

    if args.command == "ingest":
        run_ingest(args.directory)
    elif args.command == "report":
        run_report(args.out)


if __name__ == "__main__":
    main()
