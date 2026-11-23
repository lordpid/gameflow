-- Event taxonomy and ingestion model.
-- PostgreSQL 13+. Apply after 002_achievement_progression.sql.
--
-- An event is a closed-catalog fact. Integrations do not invent type names.
-- The envelope GameFlow reads is event type, payload, identity, and timestamp.
-- game, score, quiz_id, and checkpoint_id stay inside payload.
-- This is not an API. It is the storage contract the API will accept later.

BEGIN;

CREATE TABLE event_types (
    id smallserial PRIMARY KEY,
    code text NOT NULL UNIQUE,
    name text NOT NULL,
    description text NOT NULL DEFAULT '',
    is_active boolean NOT NULL DEFAULT true,
    CONSTRAINT event_types_code_format CHECK (code ~ '^[A-Z][A-Z0-9_]{1,63}$')
);

COMMENT ON TABLE event_types IS
    'The only event vocabulary. Inactive types stay for history but cannot be ingested or newly required.';

INSERT INTO event_types (code, name, description)
VALUES
    ('GAME_STARTED', 'Game started', 'A play session started. Payload may include game.'),
    ('GAME_FINISHED', 'Game finished', 'A play session ended. Payload may include game and score.'),
    ('SCORE_RECORDED', 'Score recorded', 'A score was recorded. Payload may include game and score. This does not grant XP.'),
    ('QUIZ_COMPLETED', 'Quiz completed', 'A quiz was completed. Payload may include quiz_id.'),
    ('CROSSWORD_COMPLETED', 'Crossword completed', 'A crossword was completed. Payload may include crossword_id.'),
    ('CHECKPOINT_VISITED', 'Checkpoint visited', 'A city-game checkpoint was visited. Payload may include checkpoint_id.'),
    ('MISSION_COMPLETED', 'Mission completed', 'A mission was completed. Payload may include mission_id.'),
    ('QR_SCANNED', 'QR scanned', 'A QR code was scanned. Payload may include qr_id.'),
    ('TRAINING_COMPLETED', 'Training completed', 'A training was completed. Payload may include training_id.'),
    ('SURVEY_COMPLETED', 'Survey completed', 'A survey was completed. Payload may include survey_id.');

-- Backfill rewrites existing rows once. After this migration the log is append-only again.
ALTER TABLE achievement_events DISABLE TRIGGER achievement_events_append_only;

ALTER TABLE achievement_events
    ADD COLUMN event_type_id smallint;

UPDATE achievement_events AS e
SET event_type_id = t.id
FROM event_types AS t
WHERE e.event_type_id IS NULL
  AND e.event_type = t.code;

UPDATE achievement_events AS e
SET event_type_id = t.id
FROM event_types AS t
JOIN (
    VALUES
        ('game.started', 'GAME_STARTED'),
        ('game.finished', 'GAME_FINISHED'),
        ('score.submitted', 'SCORE_RECORDED'),
        ('score.recorded', 'SCORE_RECORDED'),
        ('quiz.completed', 'QUIZ_COMPLETED'),
        ('crossword.completed', 'CROSSWORD_COMPLETED'),
        ('checkpoint.visited', 'CHECKPOINT_VISITED'),
        ('mission.completed', 'MISSION_COMPLETED'),
        ('qr.scanned', 'QR_SCANNED'),
        ('training.completed', 'TRAINING_COMPLETED'),
        ('survey.completed', 'SURVEY_COMPLETED')
) AS alias (old_code, new_code) ON alias.new_code = t.code
WHERE e.event_type_id IS NULL
  AND e.event_type = alias.old_code;

DO $$
DECLARE
    leftovers text;
BEGIN
    SELECT string_agg(DISTINCT event_type, ', ' ORDER BY event_type)
    INTO leftovers
    FROM achievement_events
    WHERE event_type_id IS NULL;

    IF leftovers IS NOT NULL THEN
        RAISE EXCEPTION 'unmapped achievement_events.event_type values: %', leftovers;
    END IF;
END;
$$;

ALTER TABLE achievement_events
    ALTER COLUMN event_type_id SET NOT NULL;

ALTER TABLE achievement_events
    ADD CONSTRAINT achievement_events_event_type_fk
    FOREIGN KEY (event_type_id) REFERENCES event_types (id);

DROP INDEX achievement_events_profile_type_idx;

ALTER TABLE achievement_events
    DROP CONSTRAINT achievement_events_type_format;

ALTER TABLE achievement_events
    DROP COLUMN event_type;

ALTER TABLE achievement_requirements
    ADD COLUMN event_type_id smallint;

UPDATE achievement_requirements AS r
SET event_type_id = t.id
FROM event_types AS t
WHERE r.event_type_id IS NULL
  AND r.event_type = t.code;

UPDATE achievement_requirements AS r
SET event_type_id = t.id
FROM event_types AS t
JOIN (
    VALUES
        ('game.started', 'GAME_STARTED'),
        ('game.finished', 'GAME_FINISHED'),
        ('score.submitted', 'SCORE_RECORDED'),
        ('score.recorded', 'SCORE_RECORDED'),
        ('quiz.completed', 'QUIZ_COMPLETED'),
        ('crossword.completed', 'CROSSWORD_COMPLETED'),
        ('checkpoint.visited', 'CHECKPOINT_VISITED'),
        ('mission.completed', 'MISSION_COMPLETED'),
        ('qr.scanned', 'QR_SCANNED'),
        ('training.completed', 'TRAINING_COMPLETED'),
        ('survey.completed', 'SURVEY_COMPLETED')
) AS alias (old_code, new_code) ON alias.new_code = t.code
WHERE r.event_type_id IS NULL
  AND r.event_type = alias.old_code;

DO $$
DECLARE
    leftovers text;
BEGIN
    SELECT string_agg(DISTINCT event_type, ', ' ORDER BY event_type)
    INTO leftovers
    FROM achievement_requirements
    WHERE event_type_id IS NULL;

    IF leftovers IS NOT NULL THEN
        RAISE EXCEPTION 'unmapped achievement_requirements.event_type values: %', leftovers;
    END IF;
END;
$$;

ALTER TABLE achievement_requirements
    ALTER COLUMN event_type_id SET NOT NULL;

ALTER TABLE achievement_requirements
    ADD CONSTRAINT achievement_requirements_event_type_fk
    FOREIGN KEY (event_type_id) REFERENCES event_types (id);

DROP INDEX achievement_requirements_event_type_idx;

ALTER TABLE achievement_requirements
    DROP CONSTRAINT achievement_requirements_event_type_format;

ALTER TABLE achievement_requirements
    DROP COLUMN event_type;

ALTER TABLE achievement_events
    ADD COLUMN identity_id uuid;

UPDATE achievement_events AS e
SET identity_id = p.identity_id
FROM game_profiles AS p
WHERE p.id = e.profile_id
  AND e.identity_id IS NULL;

ALTER TABLE achievement_events
    ALTER COLUMN identity_id SET NOT NULL;

ALTER TABLE achievement_events
    ADD CONSTRAINT achievement_events_identity_fk
    FOREIGN KEY (identity_id) REFERENCES identities (id);

ALTER TABLE achievement_events ENABLE TRIGGER achievement_events_append_only;

CREATE FUNCTION gameflow_event_identity_matches_profile() RETURNS trigger
LANGUAGE plpgsql AS $$
DECLARE
    profile_identity uuid;
BEGIN
    SELECT identity_id
    INTO profile_identity
    FROM game_profiles
    WHERE id = NEW.profile_id;

    IF profile_identity IS DISTINCT FROM NEW.identity_id THEN
        RAISE EXCEPTION 'event identity does not match the profile identity'
            USING ERRCODE = '55000';
    END IF;
    RETURN NEW;
END;
$$;

CREATE TRIGGER achievement_events_identity_matches_profile
    BEFORE INSERT ON achievement_events
    FOR EACH ROW EXECUTE FUNCTION gameflow_event_identity_matches_profile();

CREATE FUNCTION gameflow_reject_inactive_event_type() RETURNS trigger
LANGUAGE plpgsql AS $$
BEGIN
    IF NOT EXISTS (
        SELECT 1
        FROM event_types
        WHERE id = NEW.event_type_id
          AND is_active
    ) THEN
        RAISE EXCEPTION 'event type is inactive'
            USING ERRCODE = '55000';
    END IF;
    RETURN NEW;
END;
$$;

CREATE TRIGGER achievement_events_active_type
    BEFORE INSERT ON achievement_events
    FOR EACH ROW EXECUTE FUNCTION gameflow_reject_inactive_event_type();

CREATE TRIGGER achievement_requirements_active_type
    BEFORE INSERT OR UPDATE OF event_type_id ON achievement_requirements
    FOR EACH ROW EXECUTE FUNCTION gameflow_reject_inactive_event_type();

CREATE INDEX achievement_events_profile_type_idx
    ON achievement_events (profile_id, event_type_id, occurred_at);

CREATE INDEX achievement_events_identity_idx
    ON achievement_events (identity_id, occurred_at);

CREATE INDEX achievement_requirements_event_type_idx
    ON achievement_requirements (event_type_id);

CREATE VIEW event_envelopes AS
SELECT
    e.id,
    t.code AS event_type,
    e.payload,
    e.identity_id,
    e.occurred_at
FROM achievement_events AS e
JOIN event_types AS t ON t.id = e.event_type_id;

COMMENT ON VIEW event_envelopes IS
    'Canonical event read model: event type, payload, identity, timestamp. game_id and score_id are optional resolved links, not part of the envelope.';

COMMENT ON TABLE achievement_events IS
    'Append-only domain events ingested for a resolved profile. Type must be an active event_types row. Payload is opaque. A score row still does not grant XP.';

COMMENT ON TABLE achievement_requirements IS
    'AND rules for one achievement, each bound to a catalog event type. The engine evaluates them. An achievement with no requirements cannot be unlocked. payload_field is a top-level payload key, not a path.';

COMMIT;
