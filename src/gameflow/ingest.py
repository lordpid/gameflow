"""Resolve a claimed identity and append one catalog event.

This does not evaluate achievements and does not write XP.
"""

from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from typing import Any

from psycopg import Connection
from psycopg.errors import UniqueViolation
from psycopg.types.json import Jsonb


class IngestError(Exception):
    def __init__(self, code: str, status: int) -> None:
        self.code = code
        self.status = status
        super().__init__(code)


def ingest_event(conn: Connection[Any], body: dict[str, Any]) -> tuple[dict[str, str], int]:
    provider = _required_text(body.get("provider"), "provider")
    subject = _required_text(body.get("subject"), "subject")
    event_code = _required_text(body.get("event_type"), "event_type")
    occurred_at = _aware_timestamp(body.get("occurred_at"))
    payload = body.get("payload")
    if not isinstance(payload, dict):
        raise IngestError("invalid_body", 422)
    idempotency_key = _idempotency_key(
        body.get("idempotency_key"),
        provider,
        subject,
        event_code,
        occurred_at,
        payload,
    )

    with conn.transaction():
        identity = conn.execute(
            """
            SELECT i.id AS identity_id, p.id AS profile_id
            FROM identity_providers AS ip
            JOIN identities AS i ON i.provider_id = ip.id
            LEFT JOIN game_profiles AS p ON p.identity_id = i.id
            WHERE ip.code = %s AND i.external_subject = %s
            """,
            (provider, subject),
        ).fetchone()
        if identity is None:
            raise IngestError("identity_not_found", 404)
        if identity[1] is None:
            raise IngestError("profile_not_found", 422)

        event_type = conn.execute(
            "SELECT id, is_active FROM event_types WHERE code = %s",
            (event_code,),
        ).fetchone()
        if event_type is None:
            raise IngestError("event_type_unknown", 422)
        if not event_type[1]:
            raise IngestError("event_type_inactive", 422)

        try:
            with conn.transaction():
                inserted = conn.execute(
                    """
                    INSERT INTO achievement_events (
                        profile_id, identity_id, event_type_id, payload, occurred_at, idempotency_key
                    )
                    VALUES (%s, %s, %s, %s, %s, %s)
                    RETURNING id
                    """,
                    (
                        identity[1],
                        identity[0],
                        event_type[0],
                        Jsonb(payload),
                        occurred_at,
                        idempotency_key,
                    ),
                ).fetchone()
        except UniqueViolation:
            inserted = None

        if inserted is not None:
            return _accepted(inserted[0], event_code), 201

        existing = conn.execute(
            """
            SELECT e.id, t.code, e.payload, e.occurred_at, e.identity_id
            FROM achievement_events AS e
            JOIN event_types AS t ON t.id = e.event_type_id
            WHERE e.profile_id = %s AND e.idempotency_key = %s
            """,
            (identity[1], idempotency_key),
        ).fetchone()
        if existing is None:
            raise IngestError("idempotency_conflict", 409)
        same = (
            existing[1] == event_code
            and existing[2] == payload
            and existing[3] == occurred_at
            and existing[4] == identity[0]
        )
        if not same:
            raise IngestError("idempotency_conflict", 409)
        return _accepted(existing[0], event_code), 200


def _accepted(event_id: Any, event_code: str) -> dict[str, str]:
    return {"status": "accepted", "id": str(event_id), "event_type": event_code}


def _required_text(value: Any, field: str) -> str:
    if not isinstance(value, str) or value.strip() == "":
        raise IngestError("invalid_body", 422)
    if field == "event_type":
        return value
    return value.strip()


def _aware_timestamp(value: Any) -> datetime:
    if not isinstance(value, datetime) or value.tzinfo is None or value.utcoffset() is None:
        raise IngestError("invalid_body", 422)
    return value.astimezone(timezone.utc)


def _idempotency_key(
    explicit: Any,
    provider: str,
    subject: str,
    event_code: str,
    occurred_at: datetime,
    payload: dict[str, Any],
) -> str:
    if explicit is not None:
        if not isinstance(explicit, str) or explicit.strip() == "" or len(explicit) > 200:
            raise IngestError("invalid_body", 422)
        return explicit
    canonical = json.dumps(
        {
            "provider": provider,
            "subject": subject,
            "event_type": event_code,
            "occurred_at": occurred_at.isoformat(),
            "payload": payload,
        },
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
    )
    return "auto:" + hashlib.sha256(canonical.encode()).hexdigest()
