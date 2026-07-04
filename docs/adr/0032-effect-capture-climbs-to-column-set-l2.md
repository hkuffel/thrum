# Effect capture climbs to the column-set (L2); the granularity ladder is the privacy ceiling, and capture precedes detection

An Effect is `(table, kind, row_count)` today — call it **L1**. With a historical
corpus L1 can catch a *wrong count*, and on its own it catches **Zero-Effect**;
but it is structurally blind to the silent failures skeptics name as the common
case — *wrong rows*, *wrong values*, *wrote the wrong column* (objection #4). So
"we added anomaly detection" does not answer the objection unless we also decide
how much more of each mutation we record. This ADR fixes that granularity, and —
just as importantly — fixes where we *stop*.

Vocabulary lives in [CONTEXT.md](../../CONTEXT.md) (**Effect**, **Zero-Effect**,
**Operation Fingerprint**, **Baseline**). This builds on the execution-injected,
framework-owned session (ADR-0024) that makes effects attributable by
construction, and is the substrate ADR-0033's detection design consumes.

## The ladder, and the rung we pick

- **L1 — `(table, kind, row_count)`** *(today)*. Catches zero and wrong-count.
- **L2 — + the set of column names touched** (an UPDATE's `SET` list, an INSERT's
  column list). Catches *"wrote a column it never writes"* and *"stopped writing
  a column it always writes"* — the structural shape of a wrong-value bug and a
  missing write leg.
- **L3 — + which rows** (PK identities, or a sketch of the affected key set).
  Catches *wrong rows* (right count, wrong identities).
- **L4 — + the values written.** Catches *wrong values* fully.

**Decision: climb to L2 for v1, and stop there.** L3 and L4 are **deferred and
uncommitted** — not foreclosed. We do not promise "never L4"; we promise "not
yet, and not without re-deciding," because the ceiling is a boundary we may move
and the ADR should not overclaim one we have not settled.

## Why L2 is the line

- **It is free.** The column-set is already in the DML the recorder observes
  (`worker/effects.py` classifies the parse tree); L2 reads the `SET`/column list
  it already has, with no new instrumentation.
- **The granularity ladder is the privacy boundary.** Column names are *schema*,
  not *data*. L2 records nothing about the user's business values, which is what
  keeps the effect log palatable to a security/DBA team — the direct answer to
  objection #8 ("a framework slurping our data is a deal-killer"). L4 would trade
  that trust away for coverage.
- **Conceding value-level correctness is deliberate, not a retreat.** "Status
  should be `cancelled`, not `active`" is exactly where a plain `assert` against
  your own data, in your own process, is the right tool: it is the developer's
  data, it never flows into Thrum, and keeping it out is what lets the honest
  claim be *"Thrum catches every silent failure observable from the shape of what
  you wrote — zero, wrong-count, wrong-column, missing-leg — without ever
  recording your data."* A narrower, defensible sentence beats "we catch
  everything."

## Capture must precede detection

The detection that consumes column-sets and version baselines is v2 (ADR-0033);
the *capture* must ship in v1 anyway, because **baselines cannot be built
retroactively — Thrum stores Runs, never code.** Anything the v2 detector needs
to know about a v1 Run must be recorded at the instant that Run executes or it is
gone. Two fields are therefore persisted from v1 despite nothing reading them
until v2 — deliberately against YAGNI, because they are substrate laid while the
concrete is wet, not features addable on demand:

1. **The Operation Fingerprint on every Run/Attempt.** Keys the version-scoped
   Baseline (ADR-0033). Because code is never stored, a missing fingerprint is
   not recomputable later — an unversioned back-catalogue is permanently
   unbaselineable, and the v2 detector is born cold across the whole user base.
   v1 stamps a signature + body hash; a richer effect-surface fingerprint in v2
   applies only going forward (acceptable — baselines rebuild over time).
2. **The Effect's column-set as *names*, not a count.** `column_count: 3` is
   useless to a column-drift detector, which must know *which* column appeared.

## Considered options

- **Stay at L1, lean entirely on count-anomalies** — rejected: leaves
  wrong-column and missing-leg uncatchable, so the answer to objection #4 stays
  thin.
- **L3/L4 in v1** — rejected for v1: L4 breaks the "we never read your business
  values" promise the effect log's trust story rests on; L3 carries PK-sensitivity
  and storage cost without a detector to justify it yet.
- **Defer the column-set capture to v2 with the detector (YAGNI)** — rejected:
  retroactivity defeats YAGNI here; a detector shipped without a pre-existing
  corpus is blind for a full release.

## Consequences

- `effects` gains a column-name set per row; `runs`/`attempts` gain an Operation
  Fingerprint column, written by v1 and read by v2.
- The L2/L3 line is documented as the privacy ceiling, so a future "why don't you
  just record the values?" is answered by design, not re-litigated.
- The recorder (`worker/effects.py`) extends its per-`(table, kind)` aggregation
  to also union the touched column names; the SAVEPOINT-isolated, best-effort
  write discipline is unchanged.
