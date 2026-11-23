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

Like event ingestion, this endpoint does **not** authenticate the caller or
verify that the caller owns the requested identity. Deploy it only behind a
trusted adapter that authenticates and authorizes access to the profile.
Unknown identities return `404 identity_not_found`; identities without a profile
return `422 profile_not_found`.

