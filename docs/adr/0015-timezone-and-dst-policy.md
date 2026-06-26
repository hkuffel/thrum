# Schedules store cron + IANA timezone; Postgres is the timezone authority; bounded horizon defuses tzdata changes

A Schedule's recurrence is stored as a **cron expression plus an IANA timezone name** (`America/Vancouver`, never a fixed UTC offset). This is the durable *intent*. The resolved UTC instant — the Run's `fire_time` — is only ever precomputed within the ~24h Materialization Horizon (ADR-0003) and re-derived from `(cron, tz)` every materialization cycle. Cron expansion happens in Python (producing local wall-clock datetimes); the **local→UTC conversion happens in Postgres via `AT TIME ZONE`**, so Postgres's bundled `tzdata` is the single authority for both zone rules and the `now()` comparisons that gate dispatch (extending ADR's "Postgres is the clock authority" to timezones — no Python-`zoneinfo`-vs-Postgres-`tzdata` skew).

## Why

The Crunchy Data "British Columbia and time zone changes" case (BC abolished seasonal DST on 2026-03-08) shows the trap: storing future events as fixed UTC instants means a later `tzdata` update silently re-converts them to the wrong wall-clock time the user never intended. The article's fix is to keep local intent authoritative. For a *recurring* Schedule we get a better-shaped version of that for free: the cron + tz rule *is* the preserved intent, and because we materialize only ~24h ahead and continuously re-derive, a rule change propagates into materialized Runs within one horizon and cannot silently corrupt occurrences further out — they don't exist as rows yet, they're still just the rule. The horizon built for missed-detection durability doubles as the tzdata-safety window.

## DST policy (within a horizon)

- **Fall-back (local time occurs twice):** fire **once**, on the first occurrence. Double-firing is the cardinal sin.
- **Spring-forward (local time does not exist):** **shift forward** to the next valid instant (e.g. 02:30 → 03:00), so a "daily" job still runs that day. Chosen over *skip* because a silently skipped day looks exactly like the missed-run failure Thrum exists to flag. Default-with-seam (ADR-0009): a per-Schedule override can be added later without migration.

## Consequences

- **Keeping Postgres's `tzdata` current is a correctness dependency** — a stale rule (e.g. pre-2026 BC) yields wrong fire times. Document as an operational requirement.
- Fixed UTC offsets are rejected as Schedule timezones; only IANA names, so DST/abolition rules are honored.
- Ad-hoc enqueues with a future deadline are unaffected (no recurrence rule); only Schedules carry a timezone.
