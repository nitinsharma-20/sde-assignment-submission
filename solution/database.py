import json
import sqlite3
import os
import hmac
import hashlib
import base64

from pathlib import Path
from cryptography.hazmat.primitives.ciphers.aead import AESGCM


def get_encryption_key():
    secret = os.environ.get("ENCRYPTION_SECRET")

    if not secret:
        raise ValueError("ENCRYPTION_SECRET is not configured")

    return hashlib.sha256(secret.encode()).digest()


def get_hmac_key():
    secret = os.environ.get("HMAC_SECRET")

    if not secret:
        raise ValueError("HMAC_SECRET is not configured")

    return secret.encode()


def encrypt_value(value):
    if value is None:
        return None

    key = get_encryption_key()
    nonce = os.urandom(12)

    encrypted = AESGCM(key).encrypt(
        nonce,
        value.encode(),
        None
    )

    return base64.b64encode(nonce + encrypted).decode()


def decrypt_value(encrypted_value):
    if encrypted_value is None:
        return None

    key = get_encryption_key()
    data = base64.b64decode(encrypted_value)

    nonce = data[:12]
    ciphertext = data[12:]

    decrypted = AESGCM(key).decrypt(
        nonce,
        ciphertext,
        None
    )

    return decrypted.decode()


def hash_phone(phone):
    if phone is None:
        return None

    return hmac.new(
        get_hmac_key(),
        phone.encode(),
        hashlib.sha256
    ).hexdigest()


class Database:
    def __init__(self, db_path="data/ledger.db"):
        Path(db_path).parent.mkdir(parents=True, exist_ok=True)

        self.connection = sqlite3.connect(db_path)
        self.connection.row_factory = sqlite3.Row

        self.create_tables()

    def create_tables(self):
        self.connection.executescript("""
        CREATE TABLE IF NOT EXISTS leads (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            workflow_id TEXT NOT NULL,
            external_ref TEXT NOT NULL,
            batch_id TEXT NOT NULL,
            phone_hash TEXT NOT NULL,
            phone_encrypted TEXT NOT NULL,
            customer_name_encrypted TEXT NOT NULL,
            lead_data TEXT NOT NULL,
            UNIQUE(workflow_id, phone_hash)
        );

        CREATE TABLE IF NOT EXISTS interaction_events (
            event_id TEXT PRIMARY KEY,
            event_type TEXT NOT NULL,
            workflow_id TEXT NOT NULL,
            call_id TEXT,
            lead_id INTEGER,
            occurred_at TEXT NOT NULL,
            payload TEXT NOT NULL,
            FOREIGN KEY(lead_id) REFERENCES leads(id)
        );

                       CREATE TABLE IF NOT EXISTS orphan_events (
            event_id TEXT PRIMARY KEY,
            workflow_id TEXT,
            call_id TEXT,
            phone_hash TEXT,
            occurred_at TEXT,
            raw_event TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS processed_event_ids (
            event_id TEXT PRIMARY KEY
        );        """)


        self.connection.commit()
    def claim_event_id(self, event_id):
        """Record an event ID so it cannot be processed twice."""

        cursor = self.connection.execute(
            """
            INSERT OR IGNORE INTO processed_event_ids (event_id)
            VALUES (?)
            """,
            (event_id,),
        )

        self.connection.commit()
        return cursor.rowcount == 1
    def find_lead_by_phone(self, workflow_id, phone):
        """Find a lead using its workflow and hashed phone number."""

        phone_hash = hash_phone(phone)

        return self.connection.execute(
            """
            SELECT id
            FROM leads
            WHERE workflow_id = ? AND phone_hash = ?
            """,
            (workflow_id, phone_hash),
        ).fetchone()
    def find_lead_by_call_id(self, workflow_id, call_id):
        """Find a lead using a previously stored call ID."""

        result = self.connection.execute(
            """
            SELECT lead_id
            FROM interaction_events
            WHERE workflow_id = ? AND call_id = ?
            AND lead_id IS NOT NULL
            LIMIT 1
            """,
            (workflow_id, call_id),
        ).fetchone()

        if result is None:
            return None

        return result["lead_id"]
    def store_event(self, event, lead_id):
         """Save an event in the append-only interaction ledger."""

         stored_event = {
            key: value for key, value in event.items()
            if key != "phone"
        }

         self.connection.execute(
            """
            INSERT OR IGNORE INTO interaction_events (
                event_id,
                event_type,
                workflow_id,
                call_id,
                lead_id,
                occurred_at,
                payload
            )
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                event["event_id"],
                event["event_type"],
                event["workflow_id"],
                event.get("call_id"),
                lead_id,
                event["occurred_at"],
                json.dumps(stored_event),
            ),
        )

         
         self.connection.commit()
    def store_orphan_event(self, event, phone_hash=None):
        """Preserve an event that cannot currently be linked to a lead."""

        self.connection.execute(
            """
            INSERT OR IGNORE INTO orphan_events (
                event_id,
                workflow_id,
                call_id,
                phone_hash,
                occurred_at,
                raw_event
            )
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (
                event["event_id"],
                event.get("workflow_id"),
                event.get("call_id"),
                phone_hash,
                event.get("occurred_at"),
 json.dumps({key: value for key, value in event.items() if key != "phone"}),
            ),
        )

        self.connection.commit()
   
    def close(self):
        self.connection.close()

    def store_lead(self, lead, external_ref):
        """Insert or merge a lead using workflow and phone hash."""

        phone = lead["phone"]
        customer_name = lead["customer_name"]

        phone_hash = hash_phone(phone)
        phone_encrypted = encrypt_value(phone)
        name_encrypted = encrypt_value(customer_name)

        lead_data = {
            key: value
            for key, value in lead.items()
            if key not in ("phone", "customer_name", "workflow_id", "batch_id")
        }

        self.connection.execute("BEGIN")

        try:
            existing = self.connection.execute(
                """
                SELECT id, lead_data
                FROM leads
                WHERE workflow_id = ? AND phone_hash = ?
                """,
                (lead["workflow_id"], phone_hash),
            ).fetchone()

            if existing:
                old_data = json.loads(existing["lead_data"])

                for key, value in lead_data.items():
                    if value is not None:
                        old_data[key] = value

                self.connection.execute(
                    """
                    UPDATE leads
                    SET phone_encrypted = ?,
                        customer_name_encrypted = ?,
                        lead_data = ?
                    WHERE id = ?
                    """,
                    (
                        phone_encrypted,
                        name_encrypted,
                        json.dumps(old_data, default=str),
                        existing["id"],
                    ),
                )

                lead_id = existing["id"]

            else:
                cursor = self.connection.execute(
                    """
                    INSERT INTO leads (
                        workflow_id,
                        external_ref,
                        batch_id,
                        phone_hash,
                        phone_encrypted,
                        customer_name_encrypted,
                        lead_data
                    )
                    VALUES (?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        lead["workflow_id"],
                        external_ref,
                        lead["batch_id"],
                        phone_hash,
                        phone_encrypted,
                        name_encrypted,
                        json.dumps(lead_data, default=str),
                    ),
                )

                lead_id = cursor.lastrowid

            orphan_rows = self.connection.execute(
                """
                SELECT event_id, raw_event
                FROM orphan_events
                WHERE workflow_id = ? AND phone_hash = ?
                """,
                (lead["workflow_id"], phone_hash),
            ).fetchall()

            for orphan in orphan_rows:
                event = json.loads(orphan["raw_event"])

                self.connection.execute(
                    """
                    INSERT OR IGNORE INTO interaction_events (
                        event_id, event_type, workflow_id,
                        call_id, lead_id, occurred_at, payload
                    )
                    VALUES (?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        event["event_id"],
                        event["event_type"],
                        event["workflow_id"],
                        event.get("call_id"),
                        lead_id,
                        event["occurred_at"],
                        json.dumps(event),
                    ),
                )

                self.connection.execute(
                    "DELETE FROM orphan_events WHERE event_id = ?",
                    (orphan["event_id"],),
                )

            self.connection.commit()
            return lead_id

        except Exception:
            self.connection.rollback()
            raise
