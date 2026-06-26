# Polling is the dispatch source of truth; LISTEN/NOTIFY is a later latency accelerator

Workers find claimable Runs by polling: claim via `SELECT … FOR UPDATE SKIP LOCKED`, sleep (default 5s, configurable), repeat. v1 ships polling only. The claim loop uses an **interruptible sleep** so that `LISTEN/NOTIFY` can short-circuit it later without redesign. `LISTEN/NOTIFY` is deferred to v2, arriving with queues.

We chose polling-first because `NOTIFY` only helps the ad-hoc "run this now" case (a queue concern, v2) — cron Runs are due at a future clock time, so a Worker wakes on the timer regardless, and polling latency is irrelevant to the v1 cron wedge (02:00:00 vs 02:00:03 is meaningless). Transactional `NOTIFY` (fires on commit) composes perfectly with transactional enqueue and will slot in cleanly when queues need sub-second pickup.

## Consequences

- **Polling remains the source of truth even after `NOTIFY` is added.** Only polling correctly handles future-due Runs and recovers lost notifications (a `NOTIFY` with no listener is silently dropped). `NOTIFY` is strictly a latency accelerator layered on top — never the mechanism of record. A future engineer must not make dispatch purely event-driven.
- v1's only forward-proofing obligation is that the poll sleep be interruptible, not a flat `sleep(N)`.
- Default poll interval 5s balances pickup latency against DB query rate; tunable per deployment.
