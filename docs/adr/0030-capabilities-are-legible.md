# Capabilities are legible: a syntactic discriminator and attribution-preserving failures

The capability/injection model is Thrum's biggest cultural risk: Python has
repeatedly rejected heavy "magic," and FastAPI's `Depends` is near the ceiling of
implicitness the community tolerates. Thrum has more mechanisms than that
(Providers, Codecs, Caller freeze/thaw, attenuation, instrumented sessions), so
the objection is real *independent of soundness*. This ADR fixes the stance that
neutralizes it: **Thrum's implicitness must be legible — every implicit thing is
either caught at Compile or attributed at failure, and never silently magic.**
Legibility has two faces, addressed below: *at rest* (reading a signature) and
*in motion* (reading a failure). The defensible claim is then that Thrum has
*less effective magic* than the `imports` + globals + `contextvars` it replaces —
not because it has fewer mechanisms, but because none of its mechanisms fail
silently.

This builds on the authoring contract (ADR-0023), the execution-injected session
(ADR-0024), and the addressability/Codec model (ADR-0029). Vocabulary lives in
[CONTEXT.md](../../CONTEXT.md) (**Capability**, **Data**, **Provider**).

## Legibility at rest: a purely syntactic discriminator

The earlier discriminator — *a parameter is a Capability iff it is keyword-only
**and** its annotated type is registered as a capability type* — leaked the
runtime provider registry into classification. A reader could not tell whether
`*, since: date | None = None` was Data and `*, db: Session` a Capability without
the registry memorized. That is the one piece of genuinely unreadable magic, and
it is a regression from VISION's clean rule ("positional are data, keyword-only
are capabilities").

The decision: **classification is purely syntactic.** Positional params are Data;
keyword-only params are Capabilities; a keyword-only param marked **`Param[T]`**
is opted back into Data. The provider registry is consulted only to *resolve*
(Compile fails fast if an unmarked keyword-only param has no registered provider),
never to *classify*. The mental model is readable with zero framework knowledge:
**before the star is data, after the star is power** — and `Param[T]` is the lone,
visible exception.

`Param[T]` is the escape hatch for a keyword-only *input* — required
(`*, to_account: Param[AccountId]`, forcing a caller to name a footgun-prone
argument) or optional with a default. Marking the **exception** rather than the
rule is deliberate: capabilities are the common post-star case (every operation
has a `db`), so they stay unmarked; keyword-only Data is rare, so it pays the one
marker. This is the inverse of a `Depends`-style marker that taxes every
capability. The marker names a *role* (this is an input), never a *provider*, so
it does not couple authoring to a provider and leaves attenuation untouched
(`Param[T]` is a classifier; `ReadOnly[Session]` is `Annotated` metadata on a
Capability — orthogonal).

This is why a `Param`-style marker, not `Depends`: provenance must stay the
framework's job, because per-Caller **attenuation** (ADR-0027) requires the
framework — not the developer — to choose the (attenuated) Provider. A developer
who wrote `= Depends(get_db)` would be picking the provider and breaking
attenuation. So implicit-by-position is load-bearing for the security model, not
gratuitous cleverness.

*Spelling:* `Param[T]` was chosen over `Data[T]` (overloaded with the glossary
noun and HTTP request-data) and `Input[T]` (purer, less familiar); it pattern-
matches the FastAPI `Query`/`Path`/`Body` family Python web devs already trust.
`Arg` rejected as a terse abbreviation.

## Legibility in motion: collapse mechanism, never collapse attribution

When an execution fails, the developer did not write the wiring, so a raw trace
runs through framework machinery (the `AsyncExitStack`, each Provider's
`__aenter__`, a Codec's `reconstruct`, the Caller thaw) and buries the real fault.
The decision:

- **Framework-framed errors are the floor.** Thrum catches at scope boundaries and
  re-raises a domain-framed error stating *what it was doing* and *whose code
  raised* — e.g. "Provider `db_provider` raised while constructing capability `db`
  for `billing.transfer`" — with the cause chain intact.
- **Durable executions additionally persist structured context** (operation,
  capability, Caller, projection) and the *uncollapsed* trace on the Attempt's
  error, since post-hoc debugging has no flag to re-run with.

**The line — what Thrum must never collapse**, even in service of legibility:

1. **Any frame outside `thrum.*`.** The operation body, its plain-function callees,
   and user-registered extension points the framework merely invoked — Providers,
   Codecs, user attenuation policy. The boundary is *ownership* (which package),
   not *authorship* (who or what typed it), which keeps the rule correct as
   agent-written project code blurs "code I wrote."
2. **The cause chain and the full trace.** Collapsing is a display default, never
   data loss: the original exception is always reachable, a verbose flag shows the
   whole tower live, and durable storage always keeps it uncollapsed.
3. **Blame the framing cannot prove.** The boundary frame states facts (what it was
   doing, whose frame raised), never a diagnosis ("your DB is misconfigured").

**This collapse boundary is the attribution boundary Thrum already draws.** The
Attempt-outcome taxonomy splits Operation-attributable (`failed`/`timed_out`) from
Worker-attributable (`abandoned`/`requeued`), and Effects are attributable by
construction. The traceback's collapse line is that same line surfaced in the
stack — one attribution concept shown three places (outcome, Effect, trace).

## Considered options

- **Keep the registry-coupled discriminator** — rejected: it is the one piece of
  unreadable-by-construction magic, and the direct target of objection #5.
- **A capability marker on every capability** (`Inject[Session]` / `Depends`-style)
  — rejected: taxes the common case and, in the `Depends` form, couples authoring
  to a provider and breaks attenuation.
- **Pure rule (b) with no escape hatch** — rejected: cannot express required
  keyword-only Data (`transfer(*, to_account)`), rejecting legitimate signatures.
- **Raw tracebacks** — rejected: the 2am footgun the skeptic names.
- **Aggressive frame collapsing that can hide user extension points** — rejected:
  it recreates the "the framework swallowed my error" failure; the ownership line
  forbids it.

## Consequences

- `signature.py`'s `classify()` and `app.py`'s phase-two check move from rule (a)
  to syntactic classification + `Param[T]` recognition; an unmarked keyword-only
  param becomes a capability-*resolution* failure, not a structural error.
- The framework owns an error-framing/trace-collapsing layer keyed on the
  `thrum.*` ownership boundary, shared between live display (b) and durable
  persistence (c).
- The marketing-true claim is earned: Thrum's magic is typed, declared in the
  signature, compile-checked, and self-attributing on failure — less *effective*
  magic than the status quo it replaces.
