import os
import secrets
import subprocess
from datetime import datetime, timedelta, timezone
from pathlib import Path

import jwt
import psycopg
import pytest
from fastapi.testclient import TestClient

from gameflow.api import create_app
from gameflow.dev_auth import AUDIENCE, ISSUER

ROOT = Path(__file__).resolve().parents[1]
TEST_SECRET = secrets.token_urlsafe(48)
MIGRATIONS = (
    "001_initial_domain.sql",
    "002_achievement_progression.sql",
    "003_event_taxonomy.sql",
    "004_achievement_evaluator.sql",
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
    client = TestClient(create_app(database_url, environment="development", dev_jwt_secret=TEST_SECRET))
    client.headers.update(_auth("123"))
    return client


def _token(subject, **claims):
    now = datetime.now(timezone.utc)
    payload = {
        "iss": ISSUER,
        "aud": AUDIENCE,
        "sub": subject,
        "iat": now,
        "exp": now + timedelta(minutes=15),
    }
    payload.update(claims)
    return jwt.encode(payload, TEST_SECRET, algorithm="HS256")


def _auth(subject):
    return {"Authorization": f"Bearer {_token(subject)}"}


def _get(client, path, params):
    subject = params.get("subject", "123")
    return client.get(path, params=params, headers=_auth(subject.strip() or "123"))


def _post(client, event_type, payload, **extra):
    body = {
        "provider": "membersflow",
        "subject": "123",
        "event_type": event_type,
        "occurred_at": "2026-11-21T15:00:00Z",
        "payload": payload,
    }
    body.update(extra)
    return client.post("/v1/events", json=body, headers=_auth(body["subject"]))


def test_profile_screen_and_assets_are_available():
    client = TestClient(create_app(""))
    page = client.get("/")
    assert page.status_code == 200
    assert "Połącz profil testowy" in page.text
    for path, content_type in (
        ("/static/style.css", "text/css"),
        ("/static/profile.js", "text/javascript"),
    ):
        asset = client.get(path)
        assert asset.status_code == 200
        assert asset.headers["content-type"].startswith(content_type)


def test_quiz_crossword_and_pacman_are_accepted_and_grant_progress(client, database_url):
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
        transactions = conn.execute("SELECT COALESCE(SUM(amount), 0) FROM xp_transactions").fetchone()[0]
        codes = conn.execute(
            """
            SELECT a.code
            FROM user_achievements AS ua
            JOIN achievements AS a ON a.id = ua.achievement_id
            ORDER BY a.code
            """
        ).fetchall()

    assert [row[0] for row in envelopes] == [
        "CROSSWORD_COMPLETED",
        "QUIZ_COMPLETED",
        "GAME_FINISHED",
    ]
    assert envelopes[2][1]["score"] == 1230
    assert [row[0] for row in codes] == ["FIRST_CROSSWORD", "FIRST_EVENT", "FIRST_QUIZ"]
    assert xp == 50
    assert transactions == 50


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
        headers=_auth("missing"),
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


def test_profile_read_model_tracks_progress_and_isolates_identities(client, database_url):
    with psycopg.connect(database_url) as conn:
        conn.execute("INSERT INTO identity_providers (code, name) VALUES ('other', 'Other')")
        conn.execute(
            """
            INSERT INTO identities (provider_id, external_subject)
            SELECT id, 'profile-reader' FROM identity_providers
            WHERE code IN ('membersflow', 'other')
            """
        )
        conn.execute(
            """
            INSERT INTO game_profiles (identity_id, display_name)
            SELECT id, 'Reader' FROM identities WHERE external_subject = 'profile-reader'
            """
        )
    params = {"provider": "membersflow", "subject": "profile-reader"}
    empty = _get(client, "/v1/profile", params)
    assert empty.status_code == 200
    assert empty.json() == {
        "identity": params,
        "profile": {"xp": 0},
        "achievements": [],
    }

    event = _post(client, "QUIZ_COMPLETED", {"quiz_id": "read-model"}, subject="profile-reader")
    assert event.status_code == 201
    profile = _get(client, "/v1/profile", params)
    assert profile.status_code == 200
    assert profile.json() == {
        "identity": params,
        "profile": {"xp": 30},
        "achievements": [
            {"code": "FIRST_EVENT", "name": "First event"},
            {"code": "FIRST_QUIZ", "name": "First quiz"},
        ],
    }

    other = _get(client, "/v1/profile", {"provider": "other", "subject": "profile-reader"})
    assert other.status_code == 403
    assert other.json() == {"error": "identity_forbidden"}


def test_profile_read_rejects_missing_identity_profile_and_invalid_input(client, database_url):
    with psycopg.connect(database_url) as conn:
        conn.execute(
            """
            INSERT INTO identities (provider_id, external_subject)
            SELECT id, 'without-profile' FROM identity_providers WHERE code = 'membersflow'
            """
        )
    missing = _get(client, "/v1/profile", {"provider": "membersflow", "subject": "missing"})
    assert missing.status_code == 404
    assert missing.json() == {"error": "identity_not_found"}
    no_profile = _get(client, "/v1/profile", {"provider": "membersflow", "subject": "without-profile"})
    assert no_profile.status_code == 422
    assert no_profile.json() == {"error": "profile_not_found"}
    blank = client.get("/v1/profile", params={"provider": " ", "subject": "123"})
    assert blank.status_code == 422
    assert blank.json() == {"error": "invalid_body"}
    assert client.get("/v1/profile", params={"provider": "membersflow"}).status_code == 422
    unconfigured = TestClient(
        create_app("", environment="development", dev_jwt_secret=TEST_SECRET)
    ).get(
        "/v1/profile", params={"provider": "membersflow", "subject": "123"}, headers=_auth("123")
    )
    assert unconfigured.status_code == 500
    assert unconfigured.json() == {"error": "not_configured"}


def test_progression_reads_ledger_and_isolates_profiles(client, database_url):
    with psycopg.connect(database_url) as conn:
        conn.execute(
            """
            INSERT INTO identities (provider_id, external_subject)
            SELECT id, subject FROM identity_providers
            CROSS JOIN (VALUES ('progression-reader'), ('progression-other')) AS s(subject)
            WHERE code = 'membersflow'
            """
        )
        conn.execute(
            """
            INSERT INTO game_profiles (identity_id, display_name)
            SELECT id, external_subject FROM identities
            WHERE external_subject IN ('progression-reader', 'progression-other')
            """
        )

    params = {"provider": "membersflow", "subject": "progression-reader"}
    empty = _get(client, "/v1/profile/progression", params)
    assert empty.status_code == 200
    assert empty.json() == {"xp": 0, "transactions": []}

    for subject in ("progression-reader", "progression-other"):
        event = _post(client, "QUIZ_COMPLETED", {"quiz_id": "progression"}, subject=subject)
        assert event.status_code == 201

    with psycopg.connect(database_url) as conn:
        conn.execute("UPDATE achievements SET xp_reward = 99 WHERE code = 'FIRST_QUIZ'")
        conn.execute(
            """
            INSERT INTO xp_transactions (profile_id, amount, reason)
            SELECT p.id, -5, 'adjustment'
            FROM game_profiles AS p
            JOIN identities AS i ON i.id = p.identity_id
            WHERE i.external_subject = 'progression-reader'
            """
        )

    try:
        response = _get(client, "/v1/profile/progression", params)
    finally:
        with psycopg.connect(database_url) as conn:
            conn.execute("UPDATE achievements SET xp_reward = 20 WHERE code = 'FIRST_QUIZ'")
    assert response.status_code == 200
    data = response.json()
    assert data["xp"] == 25
    assert sorted(data["transactions"], key=lambda transaction: transaction["source"]) == [
        {"source": "FIRST_EVENT", "xp": 10},
        {"source": "FIRST_QUIZ", "xp": 20},
        {"source": "adjustment", "xp": -5},
    ]
    assert sum(transaction["xp"] for transaction in data["transactions"]) == data["xp"]

    other = _get(
        client,
        "/v1/profile/progression",
        {"provider": "membersflow", "subject": "progression-other"},
    )
    assert other.status_code == 200
    assert other.json()["xp"] == 30
    assert len(other.json()["transactions"]) == 2


def test_progression_read_rejects_missing_identity_profile_and_invalid_input(client, database_url):
    with psycopg.connect(database_url) as conn:
        conn.execute(
            """
            INSERT INTO identities (provider_id, external_subject)
            SELECT id, 'progression-no-profile' FROM identity_providers WHERE code = 'membersflow'
            """
        )
    missing = _get(
        client, "/v1/profile/progression", {"provider": "membersflow", "subject": "missing"}
    )
    assert missing.status_code == 404
    assert missing.json() == {"error": "identity_not_found"}
    no_profile = _get(
        client,
        "/v1/profile/progression",
        {"provider": "membersflow", "subject": "progression-no-profile"},
    )
    assert no_profile.status_code == 422
    assert no_profile.json() == {"error": "profile_not_found"}
    blank = client.get("/v1/profile/progression", params={"provider": " ", "subject": "123"})
    assert blank.status_code == 422
    assert blank.json() == {"error": "invalid_body"}
    assert client.get("/v1/profile/progression", params={"provider": "membersflow"}).status_code == 422
    unconfigured = TestClient(
        create_app("", environment="development", dev_jwt_secret=TEST_SECRET)
    ).get(
        "/v1/profile/progression", params={"provider": "membersflow", "subject": "123"},
        headers=_auth("123")
    )
    assert unconfigured.status_code == 500
    assert unconfigured.json() == {"error": "not_configured"}


@pytest.mark.parametrize("path", ["/v1/profile", "/v1/profile/progression"])
def test_profile_reads_require_valid_token_and_own_identity(client, path):
    params = {"provider": "membersflow", "subject": "123"}
    for token in (
        "",
        "not-a-token",
        _token("123", iss="wrong-issuer"),
        _token("123", aud="wrong-audience"),
        _token("123", exp=datetime.now(timezone.utc) - timedelta(seconds=1)),
        _token(""),
        jwt.encode(
            {"iss": ISSUER, "aud": AUDIENCE, "sub": "123", "iat": datetime.now(timezone.utc),
             "exp": datetime.now(timezone.utc) + timedelta(minutes=5)},
            secrets.token_urlsafe(48),
            algorithm="HS256",
        ),
    ):
        response = client.get(path, params=params, headers={"Authorization": f"Bearer {token}"})
        assert response.status_code == 401
        assert response.json() == {"error": "invalid_token"}
        assert response.headers["www-authenticate"] == "Bearer"

    forbidden = client.get(path, params=params, headers=_auth("another-player"))
    assert forbidden.status_code == 403
    assert forbidden.json() == {"error": "identity_forbidden"}


def test_events_cannot_claim_another_identity(client, database_url):
    with psycopg.connect(database_url) as conn:
        before = conn.execute("SELECT count(*) FROM achievement_events").fetchone()[0]
    response = client.post(
        "/v1/events",
        headers=_auth("another-player"),
        json={
            "provider": "membersflow",
            "subject": "123",
            "event_type": "QUIZ_COMPLETED",
            "occurred_at": "2026-11-21T15:00:00Z",
            "payload": {},
        },
    )
    assert response.status_code == 403
    assert response.json() == {"error": "identity_forbidden"}
    with psycopg.connect(database_url) as conn:
        assert conn.execute("SELECT count(*) FROM achievement_events").fetchone()[0] == before


def test_missing_token_rejected_for_reads_and_events(database_url):
    client = TestClient(create_app(database_url, environment="development", dev_jwt_secret=TEST_SECRET))
    for path in ("/v1/profile", "/v1/profile/progression"):
        response = client.get(path, params={"provider": "membersflow", "subject": "123"})
        assert response.status_code == 401
        assert response.json() == {"error": "invalid_token"}
    event = client.post(
        "/v1/events",
        json={
            "provider": "membersflow",
            "subject": "123",
            "event_type": "QUIZ_COMPLETED",
            "occurred_at": "2026-11-21T15:00:00Z",
            "payload": {},
        },
    )
    assert event.status_code == 401
    assert event.json() == {"error": "invalid_token"}


def test_development_auth_fails_closed_outside_development(database_url):
    params = {"provider": "membersflow", "subject": "123"}
    for environment, secret in (("", TEST_SECRET), ("production", TEST_SECRET), ("development", "")):
        client = TestClient(create_app(database_url, environment=environment, dev_jwt_secret=secret))
        response = client.get("/v1/profile", params=params, headers=_auth("123"))
        assert response.status_code == 503
        assert response.json() == {"error": "auth_not_configured"}
        event = client.post(
            "/v1/events",
            headers=_auth("123"),
            json={
                **params,
                "event_type": "QUIZ_COMPLETED",
                "occurred_at": "2026-11-21T15:00:00Z",
                "payload": {},
            },
        )
        assert event.status_code == 503
        assert event.json() == {"error": "auth_not_configured"}


def test_local_issuer_creates_a_working_token_and_refuses_production(client):
    env = os.environ.copy()
    env.update(GAMEFLOW_ENV="development", GAMEFLOW_DEV_JWT_SECRET=TEST_SECRET)
    issued = subprocess.run(
        [os.sys.executable, "-m", "gameflow.dev_auth", "123"],
        env=env, check=True, capture_output=True, text=True,
    )
    response = client.get(
        "/v1/profile",
        params={"provider": "membersflow", "subject": "123"},
        headers={"Authorization": f"Bearer {issued.stdout.strip()}"},
    )
    assert response.status_code == 200
    env["GAMEFLOW_ENV"] = "production"
    refused = subprocess.run(
        [os.sys.executable, "-m", "gameflow.dev_auth", "123"],
        env=env, capture_output=True, text=True,
    )
    assert refused.returncode != 0
    assert not refused.stdout
