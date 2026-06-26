# The Attempt budget counts only Task-attributable outcomes, never Worker deaths

The retry budget (`max_attempts`) is exhausted by Attempts that ended for reasons **attributable to the Task** — `failed` (it raised) and `timed_out` (it overran a hard limit) — and **never** by `abandoned` (a reaped orphan whose Worker died, ADR-0013) or `requeued` (a clean-drain release on deploy, ADR-0016). So the Reaper returns a reaped Run to a claimable state (`pending`, `next_attempt_at = now()`) **unconditionally**, with no budget computation: a Worker dying or being rolling-deployed mid-Run must not consume a retry, because that is infrastructure failure, not the Task failing. The budget question ("is the Task out of tries?") only ever bites on `failed`/`timed_out`, which the Reaper never produces — which is why orphan recovery (this slice) is fully decoupled from retry/backoff (a later slice).

## Considered options

- **Any closed Attempt counts** (`count(attempts) >= max_attempts`) — rejected: a single mid-deploy Worker death would permanently `fail` a `max_attempts=1` Run that never actually failed, reproducing the silent-miss failure mode Thrum exists to defeat. Simpler predicate, wrong semantics.

## Consequences

- The reliability promise — "your Run still runs even if a Worker is rolling-deployed mid-execution" — is *true*, and matches the outcome taxonomy CONTEXT.md already calls load-bearing (`abandoned`/`requeued` are "the Worker, not the Task").
- A theoretical infinite reap-loop exists: a Run that reliably kills its Worker ~30s in is reaped and requeued forever. Accepted; a future `abandoned`-attempt ceiling / circuit-breaker is the real fix, not yet built.
- When retry/backoff lands, its budget query simply filters Attempt outcomes to `failed`/`timed_out`; `abandoned`/`requeued` are excluded by this rule with no further change.
