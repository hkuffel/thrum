# The scheduler is a leader-elected role within the Worker fleet, not a separate process

The user deploys two process types: `thrum worker` (scaled horizontally) and the dashboard. There is no separate scheduler process. On startup every Worker contends for a Postgres advisory lock; the winner additionally runs the scheduler loop (materialize future Runs from Schedules, run the reconciliation sweep that marks `missed`) on top of executing Runs. The rest are warm failover — if the leader dies, another Worker takes the lock and resumes scheduling.

We chose this over a distinct `thrum scheduler` deployable because the operational-simplicity wedge *is* the product: "your Postgres + our worker + the dashboard." Celery's separate `beat` scheduler is exactly the moving part Thrum mocks — reintroducing it under a new name would betray the contrast. The scheduler is I/O-light, so co-locating it on a leader-elected Worker costs negligible capacity, and the advisory-lock HA mechanism is identical either way.

## Consequences

- Two process types to deploy, monitor, and scale — not three.
- The leader Worker does double duty, so a pathological scheduler bug could affect a Worker that is also executing Runs. Accepted: the scheduler workload is tiny and bounded.
- A `--scheduler-only` flag preserves hard isolation (the separate-deployable topology) as an opt-in, per the project's "ship the simple default, build the seam for the richer thing" pattern.
