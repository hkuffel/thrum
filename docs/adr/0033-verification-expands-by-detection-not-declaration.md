# Verification expands by detection, not declaration; corpus anomalies are observational-only

Zero-Effect is a narrow detector — a green Run that touched nothing — and the
sharpest objection to the whole wedge (objection #4) is that the *common*
production-killing silent failure is not zero rows but *wrong rows / wrong values
/ right-count-wrong-data*, all of which sail past a green checkmark. This ADR
fixes how verification expands to catch more of them **without** becoming a worse
version of `assert`, and fixes the hard limit on what a probabilistic signal is
allowed to *do*.

Vocabulary lives in [CONTEXT.md](../../CONTEXT.md) (**Effect Anomaly**,
**Baseline**, **Operation Fingerprint**, **Postcondition**, **Zero-Effect**). The
substrate this design consumes — L2 column capture and the v1-stamped
fingerprint — is ADR-0032.

## Detection, not declaration

The obvious way to catch more is to let developers declare richer expectations
(`expect_rows(1)`, value predicates). **Rejected as the expansion strategy:** the
instant a detector needs a hand-written expectation, it is competing with
`assert` and with the test suite, and it competes *worse* — more coupled, more
magic, a vitamin you must fill the bottle for yourself. The governing filter:

> **Thrum only adds a verifier that exploits an asset a plain in-body `assert`
> does not have.** There are three: (1) *by-construction completeness* — the owned
> session sees every write, you cannot forget to instrument it; (2) *the durable
> corpus* — every prior Run of this Operation; (3) *the declared shape* —
> capabilities, attenuation, postconditions the framework already knows.

So the expansion is **corpus-powered anomaly detection** — *detection without
declaration*: the developer declares nothing, Thrum learns the Operation's normal
effect shape and flags divergence.

## Version-keyed baselines defeat alert fatigue

A baseline keyed to `operation_name` is stale the instant the code changes, and
that alert storm is how anomaly detection dies (someone mutes it; it is off when
the real bug ships). The fix uses an asset a generic anomaly system lacks — Thrum
fingerprints the Operation:

> **Key the Baseline to a *version* of the Operation (its Operation Fingerprint),
> not its name.** An intentional change does not fire anomalies; it *resets the
> baseline*. The load-bearing signal becomes *"your code did not change, but its
> behavior did"* — an Operation byte-identical to last week that wrote 3 rows
> instead of 1,000 is almost certainly a real bug (a filter gone stale as the
> world moved beneath it), and the signal is structurally immune to deploy noise.

v1 stamps a signature + body hash fingerprint (fails safe: a needless reset only
costs detection sensitivity for a warm-up window, never a false alarm); a richer
effect-surface fingerprint follows as runtime lineage matures.

## Structural is the backbone; volume self-gates on variance

A single per-version baseline is defeated by *input* variance —
`archive_orders(tenant_id)` writes 3 rows or 3,000,000 depending on the caller,
so a volume baseline is useless noise. Split the dimensions by their robustness to
input:

- **Structural anomalies** — a column that never appears appears; a table leg that
  is always written goes missing; zero-vs-nonzero — are **input-invariant** (the
  archiver writes the *same columns of the same table* at any volume). They are
  the **backbone**: sharp and broadly applicable, even for heavily parameterized
  Operations.
- **Volume anomalies** are gated on **corpus-measured variance**: Thrum fires a
  volume flag only where history shows the profile is tight enough to be
  predictive. High-variance Operations self-silence on volume while still getting
  every structural check. Nobody declares "this op has variable volume" — *the
  corpus tells you whether the corpus is predictive.*

## Determinism gates action

The hard limit on what any of this may *do*:

> **A probabilistic signal may never change a Run's outcome — no matter how
> confident. Only a deterministic, declared violation may.**

- **Every Effect Anomaly is probabilistic** → it is *always* a derived,
  observational flag, ranked by loudness, and it **never** fails, holds,
  quarantines, or retries a Run. A false positive that fails a legitimate Run is
  itself a new silent-failure mode — the cure becoming the disease. Downstream
  consumers may *read* the flag and decide for themselves; Thrum does not decide
  for them.
- **The declared write-Postcondition is deterministic** — "this Operation *must*
  write," and zero writes is a black-and-white breach of a promise the developer
  made. *That* earns the right to hard-fail, precisely because it cannot
  false-positive. This is why the one declaration surface we kept survives: it is
  not "an assertion," it is the single deterministic bit legible enough to license
  a control-flow change. Everything probabilistic stays observational.

This draws the failing/non-failing line exactly on the determinism boundary, and
preserves the existing doctrine (*observability must not mutate lifecycle state*):
a v2 Baseline is a materialized *aggregate* (a cache), never a lifecycle state, so
the derived-flag rule is untouched.

## Sequencing

M3/v1 ships only the **deterministic** checks — the Zero-Effect gradient wired for
real (read-only exemption from declared capabilities, write-capable no-op →
informational, declared Postcondition → hard fail) plus L2 capture (ADR-0032).
The **probabilistic** corpus detector — version-keyed baselines, structural +
volume anomalies — is v2, and rides the L2 corpus that v1 quietly accumulates so
it is not born cold.

## Considered options

- **A declared-expectation / assertion DSL** — rejected: a worse `assert`,
  and it makes objection #4's "vitamin" framing land harder.
- **`operation_name`-keyed baselines** — rejected: stale on every deploy; the
  alert storm kills the detector.
- **Volume as the headline dimension** — rejected: defeated by input variance;
  structural is the robust backbone, volume the self-gating add-on.
- **A quarantine/hold tier for very loud anomalies** — rejected: it lets a
  probabilistic signal change system behavior; a false-positive hold on a good Run
  is a self-inflicted outage.
