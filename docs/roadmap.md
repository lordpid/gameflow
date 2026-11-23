# Roadmap

Milestone 1 is the core event flow. The first game waits until that flow is closed.

- 001 initial domain: done
- 002 achievement and progression: done
- 003 event taxonomy: done
- 004 event ingestion API: done
- 005 achievement evaluator: done
- 006 profile read model: done
- 007 challenge engine model

Future read model: `GET /v1/profile/events` for progression history and the
XP ledger, including the source and amount of each XP transaction. Its
contract and authorization are not yet defined.

After this milestone: Snake MVP.

Not yet: ranking tables, tournaments, Pac-Man, Snake, frontend. Rankings stay a query when they arrive.
