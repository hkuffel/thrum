# Schedules are declared in code and reconciled into Postgres on Worker startup

A Schedule's durable intent (cron + IANA timezone + expectation policy) is **declared in the user's Python**, co-located with the Task — not created imperatively through the Control Plane or hand-inserted as SQL. On Worker startup, the declared set is **reconciled** into the `schedules` table: upsert by the Schedule's stable identity, and `paused = true` (never hard-delete) any row whose declaration has vanished from code. This is the Celery-beat / Oban mental model, and it matches Thrum's core positioning — *a library you import, not a platform you adopt*. Schedules live in version control next to the Task they fire.

## Identity and the reconcile

A Schedule's identity is its target `(task_namespace, task_name)` — the same user-owned, refactor-stable identity a Task has (CONTEXT). v1 is **one Schedule per Task** (a unique constraint on the pair); multiple cadences per Task is a non-breaking additive change later (extend the key with an optional schedule label). Reconcile is an idempotent upsert on that key: a changed cron/tz/policy updates the existing row; a newly-declared Schedule inserts; a Schedule removed from code is **paused, not deleted**, so its history (past Runs, missed occurrences) survives and a re-declaration revives the same row rather than orphaning the old one. Hard-delete is deliberately not a reconcile outcome — losing the audit trail of a Schedule that *used* to run contradicts the wedge.

## Reconcile is split into an always-safe assert half and a grace-gated pause half

No single Worker ever knows the union of what *all* live Workers declare, so "pause the Schedules that vanished from code" is a decision that can be wrong during a rolling deploy (a new Worker would pause a Schedule the still-draining old version legitimately declares). The reconcile is therefore split:

- **Assert (every Worker, on startup):** upsert each declared Schedule's *definitional* columns, set the **declaration gate** active, and stamp `last_declared_at = now()`. This half is purely additive and idempotent — every Worker only ever asserts "my Schedules exist and are declared," so two Workers can never conflict. Thrash is structurally impossible here. Critically, the assert half writes **only** the declaration gate; it never touches the operational gate (see the ownership split below), so it cannot clobber a control-plane pause.
- **Pause-stale (leader-gated sweep):** clear the declaration gate for Schedules whose `last_declared_at` is older than a `stale_threshold` (config; must exceed the max deploy+drain window — practical default a few minutes). Joins the Reaper/missed "one query family" already run by the Scheduler role.

During a deploy the old Workers keep stamping a to-be-removed Schedule's `last_declared_at` until they fully drain; only once no Worker has declared it for the whole threshold does the leader pause it. The trade is eventual-consistency: a Schedule removed from code keeps materializing for up to `stale_threshold` after the last declaring Worker drains — harmless, and strictly safer than pausing something still in use.

## The deliberate v1 limitation: old schedule wins for the materialized horizon

Reconcile rewrites the Schedule row, but it does **not** retract or regenerate already-materialized future Runs. So when a cron/tz edit lands mid-horizon, the ≤24h of Runs already materialized under the old rule fire on the **old** schedule; the new rule takes effect only for occurrences materialized *after* the edit. This is a bounded, documented inconsistency (the Materialization Horizon is short by design), and it is the price of **not** pulling the full mid-horizon Schedule-mutation reconciliation slice (PRD-0003 "Out of Scope", ADR-0003) into v1. Pausing *is* honored immediately at the next materialization sweep; only the retroactive retraction of already-created rows is deferred.

## Definition is code-owned; operational state is control-plane-owned

A Schedule row carries two kinds of state with two different owners, and the v1 schema must separate them now even though v1 only implements the first — conflating them into one `paused` boolean is the expensive-to-reverse mistake.

- **Definitional columns** (cron + IANA timezone + expectation policy + the Schedule's existence) are **code-owned**. Reconcile is their sole writer; the Control Plane treats them as read-only. You cannot edit a cron expression or create a Schedule from the dashboard — that is a code change and a deploy. This is not a gap; it is the declarative positioning, and it keeps the definition in version control where the wedge needs it.
- **Operational state** is **control-plane-owned** and is the *only* surface a v2 write-action (or an agent) may mutate: the execution rows (Run/Attempt — retry, trigger, backfill all write *new execution*, never the definition) and an operational pause flag. Reconcile must never write these.

Pause is therefore modeled as **two independent gates**, and a Schedule materializes only when both are active:

- the **declaration gate** — reconcile-owned, cleared when a declaration goes stale (the pause-stale sweep above);
- the **operational gate** — control-plane-owned, cleared when an operator or agent pauses the Schedule (e.g. an `operationally_paused_at` / `paused_by` pair; null = active).

Two gates rather than one boolean is what lets a dashboard pause **survive a deploy**: the assert half re-asserts the declaration gate on every startup without ever seeing the operational gate, so it cannot un-pause what a human or agent deliberately paused. With a single shared `paused` boolean, every Worker restart would silently revive a paused Schedule — a nasty production surprise, and unfixable without a schema migration once history depends on the column.

v1 ships only the declaration gate (manual pause is the deferred v2 write-action); the operational gate's column is reserved in the v1 schema and simply unused. Recording the seam costs a column now and is expensive to backfill later — the same reasoning the VISION applies to modeling expectation up front.

## Considered options

- **Imperative management via the Control Plane / CLI** (`thrum schedule create --cron …`) — rejected for v1: it is the Airflow-shaped "manage state inside the platform" model that the VISION explicitly positions *against*, and it makes schedules runtime state divorced from version control. Imperative pause/resume is additionally a **write action**, which the VISION reserves for the v2 Pro line (gated behind RBAC + audit). v1 stays read-mostly: declare in code, observe in dashboard.
- **Full mid-horizon reconciliation in v1** (retract/regenerate materialized Runs on edit) — rejected: a meaningful slice of its own (PRD-0003 deferred it deliberately), unjustified when the horizon is short and the inconsistency is documentable.
- **Hard-delete vanished Schedules** — rejected: destroys the history the wedge exists to preserve; pausing is reversible and keeps the audit trail.

## Consequences

- v1 needs an SDK surface to declare a Schedule (the headline cron API everyone copies) and a startup reconcile step in the Worker — the concrete fix for "nothing writes the `schedules` table" today.
- A cron edit is eventually-consistent within one Materialization Horizon. Document this as expected behavior, not a bug.
- A new column `last_declared_at` and a `stale_threshold` config knob are added to support grace-gated pausing; in v1 declared code is the sole source of truth for whether a Schedule is active, so the assert half re-activates the **declaration gate** on every startup.
- Pause is two independent gates (declaration-gate, reconcile-owned; operational-gate, control-plane-owned). The v1 schema **reserves** the operational-gate column(s) even though v1 only writes the declaration gate, so the v2 dashboard/agent pause survives a deploy without a migration. A Schedule materializes only when both gates are active.
- **Definitional** schedule mutation (editing cron/tz/policy, creating a Schedule) is foreclosed from the Control Plane entirely — it is always a code change. Only **operational** mutation (pause/resume, retry, trigger, backfill) lands as a v2 write-action behind the Pro governance line — consistent with ADR-0011 and the reads-free / writes-governed boundary.
- Reviving a previously-removed Schedule reuses its original row and history because reconcile keys on stable identity, not row lifetime.
