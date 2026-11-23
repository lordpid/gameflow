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

## Progression read model

`GET /v1/profile/progression?provider=membersflow&subject=123` returns the
profile's `xp` and all its ledger transactions, oldest first (ties ordered by
transaction ID). Each transaction contains `xp`, the amount recorded at the
time, and `source`: the achievement code when linked to an achievement, or
the transaction's reason otherwise. Adjustments can be negative.

The same authentication, authorization, and error behavior as the profile
endpoint applies. This endpoint also requires a trusted adapter.

