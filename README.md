# GameFlow

Universal gamification platform for achievements, rankings, progression, tournaments and game integrations.

## Core Principles
- API First
- Organization Agnostic
- Shared XP
- Event Driven
- Reusable Components

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

