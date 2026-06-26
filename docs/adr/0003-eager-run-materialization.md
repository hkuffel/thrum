# Eager pre-materialization of Runs over a bounded horizon

The scheduler pre-creates future Runs from each Schedule in a `scheduled` state, out to a bounded **Materialization Horizon** (default 24h, configurable). A reconciliation sweep marks any `scheduled` Run whose fire time has passed without transitioning to `running` as **missed** — a first-class Run state, not a derived inference.

We chose this over **lazy/at-fire-time creation** (create the Run only when the moment arrives) because lazy creation structurally cannot distinguish "the job didn't fire" from "the job was never scheduled" — if the scheduler is down at 02:00, no row exists and nothing looks wrong, reproducing the exact crontab blindness Thrum sells against. We also chose it over a separate **expectation ledger** (record expected fire times without materializing Runs) because that splits truth across two tables; at our target scale the row volume of full materialization is trivial and a single source of truth is worth more.

## Consequences

- **Missed-run detection works**, because a missed Run is just a row in the wrong state at the wrong time. This is the substrate for the SLO-engine differentiator.
- The horizon *is* the missed-detection durability window: because materialized Runs live in Postgres independent of the scheduler, an outage of up to the horizon length is fully reconstructable; an outage longer than the horizon has a blind tail.
- **Forward simulation is nearly free** — the "next 24h of Runs" already exist as rows to query.
- **Cost:** Schedule changes within the horizon invalidate already-materialized future Runs, which must be regenerated/reconciled. This bookkeeping is bounded and is the same logic that powers "you made this change, here's the new next-24h."
- A future engineer must not "simplify" away the pre-created rows — they are load-bearing for missed-detection and simulation.
- **Materialization is idempotent via a unique constraint on `(schedule_id, fire_time)`** (materialize as `INSERT … ON CONFLICT DO NOTHING`). The advisory lock (ADR-0007) is only a process-level guard; a leader that dies mid-materialization is recovered by the next leader simply re-asserting the horizon, with the constraint absorbing the overlap. The scheduler keeps no "how far did I get" bookkeeping — idempotency lives in the schema, not in scheduler state. `fire_time` is the logical identity of an occurrence; the constraint is load-bearing for crash-safety and must not be relaxed.
