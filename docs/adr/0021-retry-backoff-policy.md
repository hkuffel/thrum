# Retry is exponential backoff on `next_attempt_at`, budgeted by Task-attributable outcomes, with full jitter

A Run whose Attempt ended `failed`/`timed_out` (the Task's fault — ADR-0020) is **retried** rather than failed terminally, until its **Attempt budget** (`max_attempts`) of Task-attributable tries is spent. A retry is not a new Run and not an immediate re-claim: it returns the Run to `pending` with `next_attempt_at = now() + backoff(failures_so_far)` — the same `next_attempt_at` gate ADR-0013 already named, mirroring `fire_time`. The dispatch predicate (`pending AND next_attempt_at <= now()`) already honors it, so retry adds **no new dispatch path** — it is entirely a decision made in Record.

## The budget is counted, not inferred from attempt_number

The budget question is "has the Task used its tries?", and per ADR-0020 only `failed`/`timed_out` Attempts count — `abandoned` (reaped orphan) and `requeued` (clean drain) never do. So the budget is `count(attempts WHERE run_id = R AND outcome IN ('failed','timed_out')) >= max_attempts`, evaluated **after** the current Attempt is closed (so it includes the try that just failed). `attempt_number` is the wrong basis: it increments for reaped/requeued tries too (claim takes `max(attempt_number)+1`), so a Worker death between tries would inflate it and cut the Task's retries short — the exact "infrastructure failure consumes a retry" mistake ADR-0020 forbids. Counting outcomes is the budget; `attempt_number` stays a monotonic per-Run ledger index, nothing more.

## The curve: capped exponential with full jitter

`backoff(n)` for the n-th Task failure (n = 1 after the first failure) is `min(max_delay, initial_delay × factor^(n−1))`, then **full jitter**: the actual delay is uniform in `[0, that ceiling]` (AWS "Exponential Backoff And Jitter"). Defaults are the simple-correct beachhead (ADR-0009): `initial_delay = 1s`, `factor = 2`, `max_delay = 300s`, `jitter = on` — a 1→2→4→…→300s envelope. Jitter exists because cron is a **thundering-herd amplifier**: a Schedule that materializes 500 occurrences which all fail against a flapping downstream would, without jitter, retry in lockstep forever; full jitter decorrelates them across the whole envelope. It is per-Task-configurable on the `@task` decorator and defaults on; a Task that needs deterministic spacing sets `retry_jitter=False`.

## Where the decision lives

In Record, not Claim. Record already owns the closed-Attempt → terminal-Run transition in one short transaction; the budget count and the `pending` + `next_attempt_at` write are the same transaction, so a retry can never be half-committed. Record needs the Task's policy (`max_attempts` + curve), which the Run row does **not** store — the Worker resolves it from its in-process Registry (the Task is the code; the Run is data). An **unresolved Task** (not registered in this Worker) cannot be retried meaningfully, so it is treated as `max_attempts=1` → terminal `failed`, surfacing the misconfiguration instead of hot-looping it.

## Considered options

- **Backoff stored as a column / computed DB-side** — rejected: the policy is a property of the Task (code), not the Run (data); storing it would duplicate config onto every row and fight refactors. `next_attempt_at` (the *result*) is stored; the *curve* is not.
- **No jitter (pure exponential)** — rejected for the cron wedge specifically: synchronized materialized occurrences make the herd real, not theoretical. Full jitter over equal jitter for the wider decorrelation; the loss of a guaranteed minimum spacing is irrelevant at the cron timescale.
- **Retry decision in Claim** (re-claim immediately, decrement a counter) — rejected: it reintroduces a busy-retry loop and splits the terminal decision across two transactions. Record already holds the right transaction.
- **Budget = `count(attempts) >= max_attempts`** — rejected by ADR-0020: a mid-deploy Worker death would burn a retry.

## Consequences

- `max_attempts` finally bites. Its default stays `1`, so existing single-attempt behavior is unchanged (one failure → budget spent → terminal `failed`); raising it opts a Task into retries with zero schema change.
- The Run genuinely loops `pending → running → pending …` (ADR-0013) for the first time, with Attempts accruing underneath it and the budget counted off the outcome ledger.
- **No schema change.** `next_attempt_at` and the Attempt `outcome` taxonomy already exist; this slice only writes them on the failure path.
- The infinite reap-loop caveat (ADR-0020) is unchanged — `abandoned` is still uncounted; an `abandoned`-ceiling circuit-breaker remains future work, now joined by a possible future `failed`-attempt dead-letter state (today the terminal is plain `failed`).
- Timeout enforcement (ADR-0017) is still a later slice, so `timed_out` is not produced yet; the budget counts it now purely so that slice needs no change here.
