-- Achievement and progression model.
-- PostgreSQL 13+. Apply after 001_initial_domain.sql.
--
-- Integrations produce events. GameFlow produces progress.
-- scores stay facts for rankings. xp_transactions is the XP ledger.
-- game_profiles.xp is a projection of that ledger, not a value a game may set.
-- Changing an achievement's xp_reward affects later unlocks only.
-- Past transactions are not rewritten.

BEGIN;

CREATE TABLE achievement_requirements (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    achievement_id uuid NOT NULL REFERENCES achievements (id),
    code text NOT NULL,
    event_type text NOT NULL,
    aggregation text NOT NULL,
    payload_field text,
    operator text NOT NULL,
    threshold integer NOT NULL,
    created_at timestamptz NOT NULL DEFAULT now(),
    CONSTRAINT achievement_requirements_code_unique UNIQUE (achievement_id, code),
    CONSTRAINT achievement_requirements_code_format CHECK (code ~ '^[a-z][a-z0-9_]{1,63}$'),
    CONSTRAINT achievement_requirements_event_type_format CHECK (event_type ~ '^[a-z][a-z0-9_.]{1,63}$'),
    CONSTRAINT achievement_requirements_aggregation CHECK (aggregation IN ('count', 'max', 'sum')),
    CONSTRAINT achievement_requirements_operator CHECK (operator IN ('gte', 'eq', 'lte')),
    CONSTRAINT achievement_requirements_threshold_non_negative CHECK (threshold >= 0),
    CONSTRAINT achievement_requirements_field_matches_aggregation CHECK (
        (aggregation = 'count' AND payload_field IS NULL)
        OR (
            aggregation IN ('max', 'sum')
            AND payload_field ~ '^[a-z][a-z0-9_]{0,63}$'
        )
    )
);

COMMENT ON TABLE achievement_requirements IS
    'AND rules for one achievement. The engine evaluates them. An achievement with no requirements cannot be unlocked. payload_field is a top-level payload key, not a path.';

CREATE INDEX achievement_requirements_event_type_idx
    ON achievement_requirements (event_type);

CREATE TABLE achievement_events (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    profile_id uuid NOT NULL REFERENCES game_profiles (id),
    game_id uuid REFERENCES games (id),
    event_type text NOT NULL,
    payload jsonb NOT NULL DEFAULT '{}'::jsonb,
    occurred_at timestamptz NOT NULL,
    ingested_at timestamptz NOT NULL DEFAULT now(),
    idempotency_key text NOT NULL,
    score_id uuid REFERENCES scores (id),
    CONSTRAINT achievement_events_idempotent UNIQUE (profile_id, idempotency_key),
    CONSTRAINT achievement_events_type_format CHECK (event_type ~ '^[a-z][a-z0-9_.]{1,63}$'),
    CONSTRAINT achievement_events_key_not_blank CHECK (btrim(idempotency_key) <> ''),
    CONSTRAINT achievement_events_payload_object CHECK (jsonb_typeof(payload) = 'object')
);

COMMENT ON TABLE achievement_events IS
    'Append-only facts from integrations. This is the only progression input. A score row does not grant XP by itself.';

CREATE INDEX achievement_events_profile_type_idx
    ON achievement_events (profile_id, event_type, occurred_at);

CREATE TABLE xp_transactions (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    profile_id uuid NOT NULL REFERENCES game_profiles (id),
    amount integer NOT NULL,
    reason text NOT NULL,
    achievement_id uuid REFERENCES achievements (id),
    user_achievement_id uuid REFERENCES user_achievements (id),
    source_event_id uuid REFERENCES achievement_events (id),
    created_at timestamptz NOT NULL DEFAULT now(),
    CONSTRAINT xp_transactions_amount_nonzero CHECK (amount <> 0),
    CONSTRAINT xp_transactions_reason_format CHECK (reason ~ '^[a-z][a-z0-9_]{1,63}$'),
    CONSTRAINT xp_transactions_unlock_has_achievement CHECK (
        reason <> 'achievement_unlock' OR (
            achievement_id IS NOT NULL AND user_achievement_id IS NOT NULL
        )
    )
);

COMMENT ON TABLE xp_transactions IS
    'Append-only XP ledger. Integrations do not insert here. achievement_unlock copies xp_reward at grant time. Later reward edits do not rewrite old rows. adjustment is a new row, not an update.';

CREATE UNIQUE INDEX xp_transactions_unlock_once_idx
    ON xp_transactions (user_achievement_id)
    WHERE user_achievement_id IS NOT NULL;

CREATE INDEX xp_transactions_profile_idx
    ON xp_transactions (profile_id, created_at);

CREATE FUNCTION gameflow_reject_append_only() RETURNS trigger
LANGUAGE plpgsql AS $$
BEGIN
    RAISE EXCEPTION '% is append-only', TG_TABLE_NAME
        USING ERRCODE = '55000';
END;
$$;

CREATE TRIGGER scores_append_only
    BEFORE UPDATE OR DELETE ON scores
    FOR EACH ROW EXECUTE FUNCTION gameflow_reject_append_only();

CREATE TRIGGER user_achievements_append_only
    BEFORE UPDATE OR DELETE ON user_achievements
    FOR EACH ROW EXECUTE FUNCTION gameflow_reject_append_only();

CREATE TRIGGER achievement_events_append_only
    BEFORE UPDATE OR DELETE ON achievement_events
    FOR EACH ROW EXECUTE FUNCTION gameflow_reject_append_only();

CREATE TRIGGER xp_transactions_append_only
    BEFORE UPDATE OR DELETE ON xp_transactions
    FOR EACH ROW EXECUTE FUNCTION gameflow_reject_append_only();

CREATE FUNCTION gameflow_reject_direct_xp_write() RETURNS trigger
LANGUAGE plpgsql AS $$
BEGIN
    IF current_setting('gameflow.xp_projection', true) IS DISTINCT FROM 'on' THEN
        IF TG_OP = 'INSERT' AND NEW.xp <> 0 THEN
            RAISE EXCEPTION 'game_profiles.xp is a projection of xp_transactions'
                USING ERRCODE = '55000';
        END IF;
        IF TG_OP = 'UPDATE' AND NEW.xp IS DISTINCT FROM OLD.xp THEN
            RAISE EXCEPTION 'game_profiles.xp is a projection of xp_transactions'
                USING ERRCODE = '55000';
        END IF;
    END IF;
    RETURN NEW;
END;
$$;

CREATE TRIGGER game_profiles_xp_projection_guard
    BEFORE INSERT OR UPDATE ON game_profiles
    FOR EACH ROW EXECUTE FUNCTION gameflow_reject_direct_xp_write();

CREATE FUNCTION gameflow_project_xp() RETURNS trigger
LANGUAGE plpgsql AS $$
DECLARE
    target_profile uuid := COALESCE(NEW.profile_id, OLD.profile_id);
BEGIN
    PERFORM set_config('gameflow.xp_projection', 'on', true);
    UPDATE game_profiles
    SET xp = (
            SELECT COALESCE(SUM(amount), 0)
            FROM xp_transactions
            WHERE profile_id = target_profile
        ),
        updated_at = now()
    WHERE id = target_profile;
    RETURN NULL;
END;
$$;

CREATE TRIGGER xp_transactions_project_xp
    AFTER INSERT ON xp_transactions
    FOR EACH ROW EXECUTE FUNCTION gameflow_project_xp();

COMMENT ON COLUMN game_profiles.xp IS
    'Projection of SUM(xp_transactions.amount) for this profile. The trigger maintains it. Direct writes are rejected. Games do not write this column.';

COMMIT;
