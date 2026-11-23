-- GameFlow initial domain model.
-- PostgreSQL 13+.
--
-- Identity Provider -> GameFlow -> Integrations
-- MembersFlow is the first identity provider, not a dependency.
-- GameFlow does not store passwords, union membership, or an IdP user id
-- as its own primary key.

BEGIN;

CREATE TABLE identity_providers (
    id smallserial PRIMARY KEY,
    code text NOT NULL UNIQUE,
    name text NOT NULL,
    created_at timestamptz NOT NULL DEFAULT now(),
    CONSTRAINT identity_providers_code_format CHECK (code ~ '^[a-z][a-z0-9_]{1,31}$')
);

COMMENT ON TABLE identity_providers IS
    'External authenticators. membersflow is the first. google, microsoft, github, or a private oauth can be added without a schema change.';

CREATE TABLE identities (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    provider_id smallint NOT NULL REFERENCES identity_providers (id),
    external_subject text NOT NULL,
    created_at timestamptz NOT NULL DEFAULT now(),
    CONSTRAINT identities_provider_subject_unique UNIQUE (provider_id, external_subject),
    CONSTRAINT identities_external_subject_not_blank CHECK (btrim(external_subject) <> '')
);

COMMENT ON TABLE identities IS
    'GameFlow subject. external_subject is the IdP sub and is never interpreted here.';

CREATE TABLE organizations (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    code text NOT NULL UNIQUE,
    name text NOT NULL,
    created_at timestamptz NOT NULL DEFAULT now(),
    CONSTRAINT organizations_code_format CHECK (code ~ '^[a-z][a-z0-9_]{1,63}$')
);

COMMENT ON TABLE organizations IS
    'Optional affiliation. Not an identity and not tied to a union, school, foundation, or company.';

CREATE TABLE games (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    code text NOT NULL UNIQUE,
    name text NOT NULL,
    created_at timestamptz NOT NULL DEFAULT now(),
    CONSTRAINT games_code_format CHECK (code ~ '^[a-z][a-z0-9_]{1,63}$')
);

COMMENT ON TABLE games IS
    'A game or activity module behind the SDK contract: snake, pacman, quiz, city.';

CREATE TABLE game_profiles (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    identity_id uuid NOT NULL UNIQUE REFERENCES identities (id),
    organization_id uuid REFERENCES organizations (id),
    display_name text NOT NULL,
    xp integer NOT NULL DEFAULT 0,
    created_at timestamptz NOT NULL DEFAULT now(),
    updated_at timestamptz NOT NULL DEFAULT now(),
    CONSTRAINT game_profiles_display_name_not_blank CHECK (btrim(display_name) <> ''),
    CONSTRAINT game_profiles_xp_non_negative CHECK (xp >= 0)
);

COMMENT ON COLUMN game_profiles.xp IS
    'Shared progression across games. Games do not write this column. The application projects it from score and achievement events.';

CREATE TABLE scores (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    game_id uuid NOT NULL REFERENCES games (id),
    profile_id uuid NOT NULL REFERENCES game_profiles (id),
    value integer NOT NULL,
    metadata jsonb NOT NULL DEFAULT '{}'::jsonb,
    played_at timestamptz NOT NULL DEFAULT now(),
    CONSTRAINT scores_metadata_object CHECK (jsonb_typeof(metadata) = 'object')
);

COMMENT ON TABLE scores IS
    'Append-only play facts. Rankings are queries over this table, not a stored table.';

CREATE INDEX scores_game_ranking_idx
    ON scores (game_id, value DESC, played_at DESC);

CREATE INDEX scores_profile_recent_idx
    ON scores (profile_id, played_at DESC);

CREATE TABLE achievements (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    code text NOT NULL UNIQUE,
    name text NOT NULL,
    description text NOT NULL DEFAULT '',
    xp_reward integer NOT NULL DEFAULT 0,
    game_id uuid REFERENCES games (id),
    created_at timestamptz NOT NULL DEFAULT now(),
    CONSTRAINT achievements_code_format CHECK (code ~ '^[a-z][a-z0-9_]{1,63}$'),
    CONSTRAINT achievements_xp_reward_non_negative CHECK (xp_reward >= 0)
);

COMMENT ON COLUMN achievements.game_id IS
    'Null means a platform achievement. Set means it can be unlocked only in that game.';

CREATE TABLE user_achievements (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    profile_id uuid NOT NULL REFERENCES game_profiles (id),
    achievement_id uuid NOT NULL REFERENCES achievements (id),
    unlocked_at timestamptz NOT NULL DEFAULT now(),
    CONSTRAINT user_achievements_once UNIQUE (profile_id, achievement_id)
);

COMMENT ON TABLE user_achievements IS
    'Unlock facts for a GameFlow profile. "user" is the profile, not an IdP account.';

CREATE INDEX user_achievements_profile_idx
    ON user_achievements (profile_id, unlocked_at DESC);

CREATE TABLE badges (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    code text NOT NULL UNIQUE,
    name text NOT NULL,
    description text NOT NULL DEFAULT '',
    achievement_id uuid REFERENCES achievements (id),
    created_at timestamptz NOT NULL DEFAULT now(),
    CONSTRAINT badges_code_format CHECK (code ~ '^[a-z][a-z0-9_]{1,63}$')
);

COMMENT ON TABLE badges IS
    'Presentational award. Earned by unlocking the linked achievement, not by a separate grant.';

INSERT INTO identity_providers (code, name)
VALUES ('membersflow', 'MembersFlow');

COMMIT;
