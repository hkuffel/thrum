# Dedicated `thrum` Postgres schema, with Thrum-owned independent migrations

Thrum's tables live in a dedicated Postgres schema (`thrum.runs`, `thrum.schedules`, …) inside the user's own database, never in `public`. Thrum ships and runs its own migrations against that schema via its own CLI (`thrum db upgrade`) using its own version table — fully independent of whatever migration tool (Alembic, Django, raw SQL, or none) the user runs against their application schema.

We chose this over prefixed tables in `public` (collision risk, namespace pollution) and over emitting migrations into the user's migration tool (can't assume they have one; entangling Thrum's schema evolution with the user's migration history means a Thrum upgrade can break the user's deploy). A dedicated schema makes the "Postgres is the protocol" boundary physical: cleanly dumpable (`pg_dump --schema=thrum`), permission-scopable, and targetable by future non-Python SDKs without knowing the host app's layout.

## Consequences

- Two migration systems touch one database, but against **disjoint schemas** — isolation, not conflict. This redundancy is the point.
- Transactional enqueue requires the user's app role to hold `INSERT` on `thrum.runs` (and read on what enqueue needs), because the Run is written inside the user's transaction. The dedicated schema makes this a clean, documented one-line grant (`GRANT INSERT ON thrum.runs TO app_role`) rather than scattered public-table grants. Accepted.
- Schema evolution is tested in isolation and applies uniformly across self-hosted databases Thrum does not control.
