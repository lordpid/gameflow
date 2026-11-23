import os
import subprocess
from pathlib import Path

import psycopg
import pytest
from fastapi.testclient import TestClient

from gameflow.api import create_app

ROOT = Path(__file__).resolve().parents[1]
MIGRATIONS = (
    "001_initial_domain.sql",
    "002_achievement_progression.sql",
    "003_event_taxonomy.sql",
)


@pytest.fixture(scope="module")
def database_url():
    name = f"gameflow_api_test_{os.getpid()}"
    admin = psycopg.connect("dbname=postgres", autocommit=True)
    admin.execute(f'DROP DATABASE IF EXISTS "{name}"')
    admin.execute(f'CREATE DATABASE "{name}"')
    admin.close()
    url = f"dbname={name}"
    for migration in MIGRATIONS:
        subprocess.run(
            ["psql", url, "-v", "ON_ERROR_STOP=1", "-f", str(Path(ROOT, "db", migration))],
            check=True,
            capture_output=True,
            text=True,
        )
    with psycopg.connect(url, autocommit=True) as conn:
        conn.execute(
            """
            INSERT INTO identities (provider_id, external_subject)
            SELECT id, '123' FROM identity_providers WHERE code = 'membersflow'
            """
        )
        conn.execute(
            """
            INSERT INTO game_profiles (identity_id, display_name)
            SELECT id, 'Ada' FROM identities WHERE external_subject = '123'
            """
        )
    yield url
    admin = psycopg.connect("dbname=postgres", autocommit=True)
    admin.execute(
        """
        SELECT pg_terminate_backend(pid)
        FROM pg_stat_activity
        WHERE datname = %s AND pid <> pg_backend_pid()
        """,
        (name,),
    )
    admin.execute(f'DROP DATABASE IF EXISTS "{name}"')
    admin.close()


@pytest.fixture
def client(database_url):
    return TestClient(create_app(database_url))


def _post(client, event_type, payload, **extra):
    body = {
        "provider": "membersflow",
        "subject": "123",
        "event_type": event_type,
        "occurred_at": "2026-11-21T15:00:00Z",
        "payload": payload,
    }
    body.update(extra)
    return client.post("/v1/events", json=body)


def test_quiz_crossword_and_pacman_are_accepted_without_progress(client, database_url):
    quiz = _post(client, "QUIZ_COMPLETED", {"quiz_id": "quiz-001"})
    crossword = _post(client, "CROSSWORD_COMPLETED", {"crossword_id": "cw-9"})
    pacman = _post(
        client,
        "GAME_FINISHED",
        {"game": "pacman", "score": 1230},
        occurred_at="2026-11-21T15:05:00Z",
    )

    assert quiz.status_code == 201
    assert crossword.status_code == 201
    assert pacman.status_code == 201
    assert quiz.json()["status"] == "accepted"
    assert pacman.json()["event_type"] == "GAME_FINISHED"

    with psycopg.connect(database_url) as conn:
        envelopes = conn.execute(
            "SELECT event_type, payload FROM event_envelopes ORDER BY occurred_at, event_type"
        ).fetchall()
        xp = conn.execute("SELECT xp FROM game_profiles").fetchone()[0]
        transactions = conn.execute("SELECT count(*) FROM xp_transactions").fetchone()[0]
        unlocks = conn.execute("SELECT count(*) FROM user_achievements").fetchone()[0]

    assert [row[0] for row in envelopes] == [
        "CROSSWORD_COMPLETED",
        "QUIZ_COMPLETED",
        "GAME_FINISHED",
    ]
    assert envelopes[2][1]["score"] == 1230
    assert xp == 0
    assert transactions == 0
    assert unlocks == 0


def test_exact_retry_returns_the_same_event(client, database_url):
    first = _post(client, "SURVEY_COMPLETED", {"survey_id": "s-1"})
    second = _post(client, "SURVEY_COMPLETED", {"survey_id": "s-1"})
    assert first.status_code == 201
    assert second.status_code == 200
    assert second.json()["id"] == first.json()["id"]
    with psycopg.connect(database_url) as conn:
        count = conn.execute(
            """
            SELECT count(*)
            FROM achievement_events AS e
            JOIN event_types AS t ON t.id = e.event_type_id
            WHERE t.code = 'SURVEY_COMPLETED'
            """
        ).fetchone()[0]
    assert count == 1


def test_unknown_subject_and_unknown_type_are_rejected(client):
    missing = client.post(
        "/v1/events",
        json={
            "provider": "membersflow",
            "subject": "missing",
            "event_type": "QR_SCANNED",
            "occurred_at": "2026-11-21T15:00:00Z",
            "payload": {},
        },
    )
    unknown = _post(client, "NOT_A_TYPE", {})
    assert missing.status_code == 404
    assert missing.json()["error"] == "identity_not_found"
    assert unknown.status_code == 422
    assert unknown.json()["error"] == "event_type_unknown"


def test_inactive_type_and_key_conflict_are_rejected(client, database_url):
    with psycopg.connect(database_url, autocommit=True) as conn:
        conn.execute("UPDATE event_types SET is_active = false WHERE code = 'TRAINING_COMPLETED'")
    inactive = _post(client, "TRAINING_COMPLETED", {"training_id": "t-1"})
    assert inactive.status_code == 422
    assert inactive.json()["error"] == "event_type_inactive"

    first = _post(
        client,
        "MISSION_COMPLETED",
        {"mission_id": "m-1"},
        idempotency_key="mission-1",
        occurred_at="2026-11-21T16:00:00Z",
    )
    conflict = _post(
        client,
        "MISSION_COMPLETED",
        {"mission_id": "m-2"},
        idempotency_key="mission-1",
        occurred_at="2026-11-21T16:00:00Z",
    )
    assert first.status_code == 201
    assert conflict.status_code == 409
    assert conflict.json()["error"] == "idempotency_conflict"
