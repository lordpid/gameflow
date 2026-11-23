import os
import subprocess
from pathlib import Path

import psycopg
import pytest

from gameflow.evaluate import recalculate

ROOT = Path(__file__).resolve().parents[1]
MIGRATIONS = (
    "001_initial_domain.sql",
    "002_achievement_progression.sql",
    "003_event_taxonomy.sql",
    "004_achievement_evaluator.sql",
)


@pytest.fixture
def database_url():
    name = f"gameflow_eval_test_{os.getpid()}"
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


def _profile(conn, subject):
    provider = conn.execute("SELECT id FROM identity_providers WHERE code = 'membersflow'").fetchone()[0]
    identity = conn.execute(
        "INSERT INTO identities (provider_id, external_subject) VALUES (%s, %s) RETURNING id",
        (provider, subject),
    ).fetchone()[0]
    profile = conn.execute(
        "INSERT INTO game_profiles (identity_id, display_name) VALUES (%s, %s) RETURNING id",
        (identity, subject),
    ).fetchone()[0]
    return identity, profile


def _event(conn, profile, identity, code, key, payload=None):
    event_type = conn.execute("SELECT id FROM event_types WHERE code = %s", (code,)).fetchone()[0]
    conn.execute(
        """
        INSERT INTO achievement_events (
            profile_id, identity_id, event_type_id, payload, occurred_at, idempotency_key
        )
        VALUES (%s, %s, %s, %s, now(), %s)
        """,
        (profile, identity, event_type, psycopg.types.json.Jsonb(payload or {}), key),
    )


def _codes(conn, profile):
    rows = conn.execute(
        """
        SELECT a.code
        FROM user_achievements AS ua
        JOIN achievements AS a ON a.id = ua.achievement_id
        WHERE ua.profile_id = %s
        ORDER BY a.code
        """,
        (profile,),
    ).fetchall()
    return [row[0] for row in rows]


def _xp(conn, profile):
    return conn.execute("SELECT xp FROM game_profiles WHERE id = %s", (profile,)).fetchone()[0]


def test_quiz_grants_once_and_recalculate_is_idempotent(database_url):
    with psycopg.connect(database_url) as conn:
        identity, profile = _profile(conn, "ada")
        _event(conn, profile, identity, "QUIZ_COMPLETED", "q1", {"quiz_id": "quiz-001"})
        first = recalculate(conn, profile)
        second = recalculate(conn, profile)
        conn.commit()
    assert sorted(item.achievement_code for item in first) == ["FIRST_EVENT", "FIRST_QUIZ"]
    assert sum(item.xp for item in first) == 30
    assert second == []
    with psycopg.connect(database_url) as conn:
        assert _codes(conn, profile) == ["FIRST_EVENT", "FIRST_QUIZ"]
        assert _xp(conn, profile) == 30
        assert conn.execute("SELECT count(*) FROM xp_transactions WHERE profile_id = %s", (profile,)).fetchone()[0] == 2


def test_city_explorer_waits_for_five_checkpoints(database_url):
    with psycopg.connect(database_url) as conn:
        identity, profile = _profile(conn, "ada")
        for index in range(4):
            _event(conn, profile, identity, "CHECKPOINT_VISITED", f"c{index}", {"checkpoint_id": str(index)})
        assert [item.achievement_code for item in recalculate(conn, profile)] == ["FIRST_EVENT"]
        _event(conn, profile, identity, "CHECKPOINT_VISITED", "c4", {"checkpoint_id": "4"})
        granted = recalculate(conn, profile)
        conn.commit()
    assert [item.achievement_code for item in granted] == ["CITY_EXPLORER"]
    assert granted[0].xp == 50
    with psycopg.connect(database_url) as conn:
        assert _xp(conn, profile) == 60


def test_other_profile_events_do_not_count(database_url):
    with psycopg.connect(database_url) as conn:
        ada_identity, ada = _profile(conn, "ada")
        bob_identity, bob = _profile(conn, "bob")
        _event(conn, bob, bob_identity, "QUIZ_COMPLETED", "bob-q", {})
        recalculate(conn)
        conn.commit()
    with psycopg.connect(database_url) as conn:
        assert _codes(conn, ada) == []
        assert _codes(conn, bob) == ["FIRST_EVENT", "FIRST_QUIZ"]
        assert _xp(conn, ada) == 0


def test_tighter_rule_does_not_rewrite_history(database_url):
    with psycopg.connect(database_url) as conn:
        identity, profile = _profile(conn, "ada")
        _event(conn, profile, identity, "TRAINING_COMPLETED", "t1", {})
        recalculate(conn, profile)
        conn.execute(
            """
            UPDATE achievement_requirements
            SET threshold = 99
            WHERE code = 'training_completed'
            """
        )
        again = recalculate(conn, profile)
        conn.commit()
    assert again == []
    with psycopg.connect(database_url) as conn:
        assert "TRAINING_BEGINNER" in _codes(conn, profile)
        assert _xp(conn, profile) == 30
        assert conn.execute("SELECT count(*) FROM xp_transactions WHERE profile_id = %s", (profile,)).fetchone()[0] == 2


def test_new_rule_applies_to_stored_events_once(database_url):
    with psycopg.connect(database_url) as conn:
        identity, profile = _profile(conn, "ada")
        _event(conn, profile, identity, "SURVEY_COMPLETED", "s1", {})
        recalculate(conn, profile)
        conn.execute(
            """
            INSERT INTO achievements (code, name, xp_reward)
            VALUES ('SURVEY_STARTER', 'Survey starter', 15)
            """
        )
        conn.execute(
            """
            INSERT INTO achievement_requirements (
                achievement_id, code, event_type_id, aggregation, operator, threshold
            )
            SELECT a.id, 'survey_completed', t.id, 'count', 'gte', 1
            FROM achievements AS a
            JOIN event_types AS t ON t.code = 'SURVEY_COMPLETED'
            WHERE a.code = 'SURVEY_STARTER'
            """
        )
        granted = recalculate(conn, profile)
        repeated = recalculate(conn, profile)
        conn.commit()
    assert [item.achievement_code for item in granted] == ["SURVEY_STARTER"]
    assert granted[0].xp == 15
    assert repeated == []
    with psycopg.connect(database_url) as conn:
        assert _xp(conn, profile) == 25


def test_requirements_are_conjunctions_and_game_scope_is_not_payload(database_url):
    with psycopg.connect(database_url) as conn:
        identity, profile = _profile(conn, "ada")
        game = conn.execute("INSERT INTO games (code, name) VALUES ('snake', 'Snake') RETURNING id").fetchone()[0]
        conn.execute(
            """
            INSERT INTO achievements (code, name, xp_reward)
            VALUES ('QUIZ_AND_TRAINING', 'Quiz and training', 5)
            """
        )
        conn.execute(
            """
            INSERT INTO achievements (code, name, xp_reward, game_id)
            VALUES ('SNAKE_QUIZ', 'Snake quiz', 5, %s)
            """,
            (game,),
        )
        conn.execute(
            """
            INSERT INTO achievement_requirements (
                achievement_id, code, event_type_id, aggregation, operator, threshold
            )
            SELECT a.id, lower(t.code), t.id, 'count', 'gte', 1
            FROM achievements AS a
            JOIN event_types AS t ON t.code IN ('QUIZ_COMPLETED', 'TRAINING_COMPLETED')
            WHERE a.code = 'QUIZ_AND_TRAINING'
            """
        )
        conn.execute(
            """
            INSERT INTO achievement_requirements (
                achievement_id, code, event_type_id, aggregation, operator, threshold
            )
            SELECT a.id, 'needs_quiz', t.id, 'count', 'gte', 1
            FROM achievements AS a
            JOIN event_types AS t ON t.code = 'QUIZ_COMPLETED'
            WHERE a.code = 'SNAKE_QUIZ'
            """
        )
        _event(conn, profile, identity, "QUIZ_COMPLETED", "q1", {"game": "snake"})
        after_quiz = {item.achievement_code for item in recalculate(conn, profile)}
        _event(conn, profile, identity, "TRAINING_COMPLETED", "t1", {})
        after_both = {item.achievement_code for item in recalculate(conn, profile)}
        conn.commit()
    assert "QUIZ_AND_TRAINING" not in after_quiz
    assert "SNAKE_QUIZ" not in after_quiz
    assert "QUIZ_AND_TRAINING" in after_both
    assert "SNAKE_QUIZ" not in after_both
