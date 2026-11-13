import csv
import json
from pathlib import Path

from normalization import normalize_header


SCHEMA_DIR = Path("assignment-a-data/schemas")


def load_schemas():
    """Load all workflow schemas from JSON files."""
    schemas = {}

    for schema_file in SCHEMA_DIR.glob("*.json"):
        with open(schema_file, "r", encoding="utf-8") as file:
            schema = json.load(file)

        schemas[schema["workflow_id"]] = schema

    return schemas


def map_csv_headers(headers, schema):
    """Map CSV column names and aliases to schema field names."""
    header_mapping = {}

    for field in schema["fields"]:
        possible_names = [field["name"]] + field.get("aliases", [])
        normalized_names = {
            normalize_header(name) for name in possible_names
        }

        for header in headers:
            normalized_header = normalize_header(header)

            if normalized_header in normalized_names:
                header_mapping[header] = field["name"]
                break

    return header_mapping


def read_lead_csv(csv_path, schema):
    """Read CSV rows and return rows with mapped field names."""
    records = []

    with open(csv_path, "r", encoding="utf-8-sig", newline="") as file:
        reader = csv.DictReader(file)

        mapping = map_csv_headers(reader.fieldnames or [], schema)

        for row in reader:
            mapped_row = {}

            for original_header, field_name in mapping.items():
                mapped_row[field_name] = row.get(original_header, "")

            records.append(mapped_row)

    return records

from normalization import (
    normalize_phone,
    normalize_date,
    normalize_decimal,
    normalize_enum,
)


def validate_lead(row, schema):
    """Validate and normalize one customer record."""
    cleaned = {}
    warnings = []

    for field in schema["fields"]:
        name = field["name"]
        value = row.get(name, "")

        value = value.strip() if value else ""

        if not value:
            cleaned[name] = None

            if field.get("required", False):
                return None, f"missing_required:{name}", []

            continue

        try:
            field_type = field["type"]

            if field_type == "string":
                cleaned[name] = value

            elif field_type == "phone":
                cleaned[name] = normalize_phone(value)

            elif field_type == "date":
                cleaned[name] = normalize_date(value)

            elif field_type == "decimal":
                cleaned[name] = normalize_decimal(value)

            elif field_type == "enum":
                cleaned[name] = normalize_enum(
                    value, field.get("values", [])
                )

            else:
                cleaned[name] = value

        except ValueError as error:
            if field.get("required", False):
                return None, str(error), []

            cleaned[name] = None
            warnings.append(name)

    return cleaned, None, warnings


def process_lead_csv(csv_path, schema):
    """Process all nonblank customer rows in one CSV file."""
    leads = []
    rejections = []
    warnings = []

    batch_id = Path(csv_path).stem
    workflow_id = schema["workflow_id"]

    with open(csv_path, "r", encoding="utf-8-sig", newline="") as file:
        reader = csv.DictReader(file)
        mapping = map_csv_headers(reader.fieldnames or [], schema)

        row_number = 0

        for row in reader:
            if not any(value and value.strip() for value in row.values() if isinstance(value, str)):
                continue

            row_number += 1
            mapped_row = {}

            for original_header, field_name in mapping.items():
                mapped_row[field_name] = row.get(original_header, "")

            cleaned, reason, field_warnings = validate_lead(
                mapped_row, schema
            )

            if reason:
                rejections.append({
                    "workflow_id": workflow_id,
                    "batch_id": batch_id,
                    "row_number": row_number,
                    "reason": reason,
                })
                continue

            leads.append(cleaned)

            for field_name in field_warnings:
                warnings.append(field_name)

    return leads, rejections, warnings


def merge_leads(existing_leads, new_leads, workflow_id, batch_id):
    """Merge leads using workflow and canonical phone as identity."""
    merged = {
        (lead["workflow_id"], lead["phone"]): lead
        for lead in existing_leads
    }

    duplicate_count = 0

    for new_lead in new_leads:
        key = (workflow_id, new_lead["phone"])

        if key not in merged:
            record = new_lead.copy()
            record["workflow_id"] = workflow_id
            record["batch_id"] = batch_id
            merged[key] = record
        else:
            duplicate_count += 1
            existing = merged[key]

            for field, value in new_lead.items():
                if value is not None:
                    existing[field] = value

    return list(merged.values()), duplicate_count


def apply_defaults(lead, schema):
    """Apply schema defaults to fields that remain empty after merging."""
    for field in schema["fields"]:
        name = field["name"]

        if lead.get(name) is None and "default" in field:
            lead[name] = field["default"]

    return lead
