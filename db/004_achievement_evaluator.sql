-- Achievement evaluator support and the first platform rules.
-- PostgreSQL 13+. Apply after 003_event_taxonomy.sql.
--
-- recalculate() grants a newly satisfied achievement and appends XP.
-- It does not revoke an unlock and it does not rewrite xp_transactions.
-- A null requirement event type means any catalog event.

BEGIN;

ALTER TABLE achievements DROP CONSTRAINT achievements_code_format;

ALTER TABLE achievements
    ADD CONSTRAINT achievements_code_format CHECK (code ~ '^[A-Za-z][A-Za-z0-9_]{1,63}$');

ALTER TABLE achievement_requirements
    ALTER COLUMN event_type_id DROP NOT NULL;

CREATE OR REPLACE FUNCTION gameflow_reject_inactive_event_type() RETURNS trigger
LANGUAGE plpgsql AS $$
BEGIN
    IF NEW.event_type_id IS NULL THEN
        RETURN NEW;
    END IF;
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

COMMENT ON COLUMN achievement_requirements.event_type_id IS
    'Null matches any catalog event. Set matches only that type. An achievement with no requirements cannot be unlocked.';

INSERT INTO achievements (code, name, description, xp_reward)
VALUES
    ('FIRST_EVENT', 'First event', 'Any catalog event has been recorded.', 10),
    ('FIRST_QUIZ', 'First quiz', 'One quiz has been completed.', 20),
    ('FIRST_CROSSWORD', 'First crossword', 'One crossword has been completed.', 20),
    ('CITY_EXPLORER', 'City explorer', 'Five checkpoints have been visited.', 50),
    ('TRAINING_BEGINNER', 'Training beginner', 'One training has been completed.', 20);

INSERT INTO achievement_requirements (achievement_id, code, event_type_id, aggregation, operator, threshold)
SELECT id, 'any_event', NULL, 'count', 'gte', 1
FROM achievements
WHERE code = 'FIRST_EVENT';

INSERT INTO achievement_requirements (achievement_id, code, event_type_id, aggregation, operator, threshold)
SELECT a.id, 'quiz_completed', t.id, 'count', 'gte', 1
FROM achievements AS a
JOIN event_types AS t ON t.code = 'QUIZ_COMPLETED'
WHERE a.code = 'FIRST_QUIZ';

INSERT INTO achievement_requirements (achievement_id, code, event_type_id, aggregation, operator, threshold)
SELECT a.id, 'crossword_completed', t.id, 'count', 'gte', 1
FROM achievements AS a
JOIN event_types AS t ON t.code = 'CROSSWORD_COMPLETED'
WHERE a.code = 'FIRST_CROSSWORD';

INSERT INTO achievement_requirements (achievement_id, code, event_type_id, aggregation, operator, threshold)
SELECT a.id, 'checkpoints_visited', t.id, 'count', 'gte', 5
FROM achievements AS a
JOIN event_types AS t ON t.code = 'CHECKPOINT_VISITED'
WHERE a.code = 'CITY_EXPLORER';

INSERT INTO achievement_requirements (achievement_id, code, event_type_id, aggregation, operator, threshold)
SELECT a.id, 'training_completed', t.id, 'count', 'gte', 1
FROM achievements AS a
JOIN event_types AS t ON t.code = 'TRAINING_COMPLETED'
WHERE a.code = 'TRAINING_BEGINNER';

COMMIT;
