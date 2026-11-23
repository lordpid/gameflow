"""Grant achievements from stored events. Idempotent and non-revoking."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any
from uuid import UUID

from psycopg import Connection
from psycopg.errors import UniqueViolation
from psycopg.rows import dict_row


@dataclass(frozen=True)
class Unlock:
    profile_id: str
    achievement_code: str
    xp: int


def recalculate(conn: Connection[Any], profile_id: UUID | str | None = None) -> list[Unlock]:
    """Append unlocks and XP for rules that are now satisfied.

    A second call grants nothing new. A tighter rule does not remove an
    unlock or rewrite the ledger. Games do not choose the XP amount.
    """
    with conn.transaction():
        with conn.cursor(row_factory=dict_row) as cur:
            cur.execute(
                """
                SELECT id
                FROM game_profiles
                WHERE %s::uuid IS NULL OR id = %s::uuid
                ORDER BY id
                """,
                (profile_id, profile_id),
            )
            profiles = [row["id"] for row in cur.fetchall()]
            if not profiles:
                return []

            cur.execute(
                """
                SELECT id, code, xp_reward, game_id
                FROM achievements
                ORDER BY code
                """
            )
            achievements = cur.fetchall()
            cur.execute(
                """
                SELECT achievement_id, event_type_id, aggregation, payload_field, operator, threshold
                FROM achievement_requirements
                ORDER BY achievement_id, code
                """
            )
            requirements: dict[Any, list[dict[str, Any]]] = {}
            for row in cur.fetchall():
                requirements.setdefault(row["achievement_id"], []).append(row)

            cur.execute(
                """
                SELECT profile_id, achievement_id
                FROM user_achievements
                WHERE profile_id = ANY(%s)
                """,
                (profiles,),
            )
            unlocked = {(row["profile_id"], row["achievement_id"]) for row in cur.fetchall()}

            cur.execute(
                """
                SELECT profile_id, event_type_id, game_id, payload
                FROM achievement_events
                WHERE profile_id = ANY(%s)
                ORDER BY occurred_at, id
                """,
                (profiles,),
            )
            events: dict[Any, list[dict[str, Any]]] = {profile: [] for profile in profiles}
            for row in cur.fetchall():
                events[row["profile_id"]].append(row)

        granted: list[Unlock] = []
        for profile in profiles:
            for achievement in achievements:
                key = (profile, achievement["id"])
                if key in unlocked:
                    continue
                rules = requirements.get(achievement["id"], [])
                if not rules:
                    continue
                scoped = _scoped(events[profile], achievement["game_id"])
                if not all(_matches(rule, scoped) for rule in rules):
                    continue
                unlock = _grant(conn, profile, achievement)
                if unlock is not None:
                    unlocked.add(key)
                    granted.append(unlock)
        return granted


def _scoped(events: list[dict[str, Any]], game_id: Any) -> list[dict[str, Any]]:
    if game_id is None:
        return events
    return [event for event in events if event["game_id"] == game_id]


def _matches(rule: dict[str, Any], events: list[dict[str, Any]]) -> bool:
    typed = [
        event
        for event in events
        if rule["event_type_id"] is None or event["event_type_id"] == rule["event_type_id"]
    ]
    if rule["aggregation"] == "count":
        value = len(typed)
    else:
        numbers = [_number(event["payload"], rule["payload_field"]) for event in typed]
        numbers = [number for number in numbers if number is not None]
        if not numbers:
            return False
        value = max(numbers) if rule["aggregation"] == "max" else sum(numbers)
    return _compare(value, rule["operator"], rule["threshold"])


def _number(payload: Any, field: str | None) -> int | float | None:
    if not isinstance(payload, dict) or field is None:
        return None
    value = payload.get(field)
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    return value


def _compare(value: int | float, operator: str, threshold: int) -> bool:
    if operator == "gte":
        return value >= threshold
    if operator == "lte":
        return value <= threshold
    if operator == "eq":
        return value == threshold
    return False


def _grant(conn: Connection[Any], profile_id: Any, achievement: dict[str, Any]) -> Unlock | None:
    xp_reward = int(achievement["xp_reward"])
    try:
        with conn.transaction():
            with conn.cursor() as cur:
                cur.execute(
                    """
                    INSERT INTO user_achievements (profile_id, achievement_id)
                    VALUES (%s, %s)
                    RETURNING id
                    """,
                    (profile_id, achievement["id"]),
                )
                user_achievement_id = cur.fetchone()[0]
                if xp_reward > 0:
                    cur.execute(
                        """
                        INSERT INTO xp_transactions (
                            profile_id, amount, reason, achievement_id, user_achievement_id
                        )
                        VALUES (%s, %s, 'achievement_unlock', %s, %s)
                        """,
                        (profile_id, xp_reward, achievement["id"], user_achievement_id),
                    )
    except UniqueViolation:
        return None
    return Unlock(str(profile_id), achievement["code"], xp_reward)
