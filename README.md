# GameFlow

Universal gamification platform for achievements, rankings, progression, tournaments and game integrations.

## Core Principles
- API First
- Organization Agnostic
- Shared XP
- Event Driven
- Reusable Components

## Development profile screen

The test profile screen is served at `/`. It shows actual XP, unlocked
achievements, and XP transactions from the API. It is not a MembersFlow
login; games, challenges, rankings, and levels are not implemented.

For a local demonstration, install the project (`pip install -e '.[dev]'`)
and prepare a **new, isolated** PostgreSQL database:

```sh
createdb gameflow_dev
export DATABASE_URL="dbname=gameflow_dev"
for migration in db/001_initial_domain.sql db/002_achievement_progression.sql db/003_event_taxonomy.sql db/004_achievement_evaluator.sql; do
    psql "$DATABASE_URL" -v ON_ERROR_STOP=1 -f "$migration"
done
psql "$DATABASE_URL" -v ON_ERROR_STOP=1 <<'SQL'
WITH identity AS (
    INSERT INTO identities (provider_id, external_subject)
    SELECT id, 'demo-player' FROM identity_providers WHERE code = 'membersflow'
    RETURNING id
)
INSERT INTO game_profiles (identity_id, display_name)
SELECT id, 'Demo' FROM identity;
SQL
```

In the same shell, configure a dedicated random test secret, generate a
15-minute token, and run the server:

```sh
export GAMEFLOW_ENV=development
export GAMEFLOW_DEV_JWT_SECRET="$(python -c 'import secrets; print(secrets.token_urlsafe(48))')"
python -m gameflow.dev_auth demo-player
uvicorn gameflow.api:app --host 127.0.0.1 --port 8000
```

Open `http://127.0.0.1:8000/`, enter `demo-player`, and paste the token.
The token remains in the browser tab's memory only. The new profile initially
has no XP or achievements. See the security limitations below; do not use
this setup with a production database or expose it publicly.

## Profile read model

`GET /v1/profile?provider=membersflow&subject=123` returns the claimed identity,
the profile's ledger-derived `xp`, and its unlocked achievements (`code` and
`name`). It does not expose a level: no level model has been defined yet.

The development JWT described below must identify the requested profile.
Unknown identities return `404 identity_not_found`; identities without a
profile return `422 profile_not_found`.

## Progression read model

`GET /v1/profile/progression?provider=membersflow&subject=123` returns the
profile's `xp` and all its ledger transactions, oldest first (ties ordered by
transaction ID). Each transaction contains `xp`, the amount recorded at the
time, and `source`: the achievement code when linked to an achievement, or
the transaction's reason otherwise. Adjustments can be negative.

The same authentication, authorization, and error behavior as the profile
endpoint applies.

## Development-only authentication

The API currently accepts only locally issued test JWTs. This is **not**
MembersFlow authentication and is not suitable for production. Outside
`GAMEFLOW_ENV=development`, or without a `GAMEFLOW_DEV_JWT_SECRET` of at
least 32 bytes, authentication fails closed with `503 auth_not_configured`.
Use an isolated test database and bind the development server to localhost;
anyone with this secret can issue tokens for any test identity.

Set `GAMEFLOW_ENV=development` and `GAMEFLOW_DEV_JWT_SECRET` to a new random
secret in the server and issuer environments. Do not reuse another service's
secret or commit it. Create a 15-minute token for an existing test identity:

```sh
python -m gameflow.dev_auth SUBJECT
```

Send it as `Authorization: Bearer <token>` with requests. The `membersflow`
provider and subject in the request must match the test token; a mismatch is
`403 identity_forbidden`, and missing or invalid tokens yield `401 invalid_token`.
The local issuer is deliberately separate from MembersFlow.

`POST /v1/events` is also available only in this development mode. A valid
token identifies the player but does **not** prove that an event is genuine:
the player can claim achievements and XP. Production needs a real identity
provider and trusted event verification before enabling these flows.

