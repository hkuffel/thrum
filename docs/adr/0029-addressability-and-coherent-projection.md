# Addressability and coherent projection supersede universal serialization

ADR-0006 made JSON-serializability a property of the **Operation**, owed
*universally* — every Operation, so that every Operation could reach every
transport. That contract is a straitjacket: it taxes the synchronous, rich-I/O
transports (HTTP, MCP) with a constraint only the durable queue actually needs,
choking the most ordinary HTTP use cases — file uploads, streaming responses,
SSE. This ADR replaces the universal-serialization contract with two ideas that
*preserve* the "author once, project anywhere" pitch instead of abandoning it:
**addressability** (what crosses a transport boundary) and **coherent
projection** (which transports an Operation can be projected onto at all).

This supersedes the central claim of [ADR-0006](0006-serialization-contract-ids-not-objects.md);
it builds on the projection model (ADR-0023), the execution-injected session
(ADR-0024), the thin-shell composition rule (ADR-0025), and "durability is a
property of the projection" (ADR-0028). The vocabulary it fixes — **Codec**,
**Projection eligibility**, **Actor** — lives in [CONTEXT.md](../../CONTEXT.md).

## The reframe: addressability, not serialization

The durable transport (queue/timer/workflow) is the only one that crosses a
*time and process gap* — it freezes inputs into a `Run` row now and thaws them in
a Worker later. JSON-serialization was only ever the *mechanism* for shepherding
values across that gap; "must be JSON" mistook the mechanism for the contract.

The real contract is **addressability**: a value is shepherdable iff it can be
**reduced to a durable reference** on one side of the gap and **reconstructed**
on the other, via a registered **Codec**. JSON-of-scalars is just the identity
codec. This generalizes ADR-0006's own "IDs not objects" (ORM row → ID →
re-fetch) into the universal form: file → `BlobRef`, array → blob, value object →
its structural form. The `Run` row still stores only the reference, so the
durable substrate stays exactly as boring as before — pure JSON — while the front
door widens to anything a codec can address.

Crucially, the addressability tax is owed **only by durable projections**.
A synchronous HTTP call has no time gap and no row; taxing it for one was
incoherent. This is the consistent application of ADR-0028.

## One reconstruction rule, three applications

Projectability is not universal — it is **graded by coherence**. A projection is
available for an Operation iff that transport's execution context can
**reconstruct everything the Operation needs**. Where it cannot, the need is
**sorted, never forced**: re-typed as a different primitive, or rejected at
Compile with a diagnostic that names the fix. The same rule applies in three
places:

1. **Input.** Data must be addressable. An input that resists addressing is
   sorted: it is actually a **Capability** (a live handle, constructed fresh on
   the far side — never shipped) or it is genuinely incoherent across the gap
   (a captured closure, a process-local path) and rejected. References are
   reconstructed **lazily** — the body receives the reference and pulls the live
   value on demand (`db.get(id)`, `blob.open()`); a dangling referent surfaces as
   a typed "referent no longer exists" failure at the pull, not a cryptic decode
   error. (The accepted cost: that failure lands mid-body, not at a pre-flight
   gate.)

2. **Output.** Streaming is an **observation mode of the projection, not a return
   type.** The Operation authors a value-producer once (e.g. an async iterator);
   the HTTP projection observes it *live* (each yield → an SSE frame, inline, no
   Run), while the queue projection observes it *collected* (drain, then store the
   collected value as `Run.output` via a codec). A never-completing *stateless*
   stream has no collected form and is therefore queue-incoherent — HTTP-only by
   design, rejected from the queue like a closure is rejected from enqueue.

3. **Host.** **Actors** host Operations and add identity + owned state + serial
   processing (ADR forthcoming if the actor runtime needs its own). An
   actor-operation's eligibility tracks the actor's *durability*: a local
   in-memory actor (OSS) is reachable only synchronously on its host process;
   a durable actor whose state is persisted/reconstructable (the runtime) is
   additionally queue-eligible. Same coherence rule, applied to actor state
   instead of a Data referent.

## Staging external bytes without a dual write

A codec that stages bytes (file → `BlobRef`) performs an external write, which
cannot enlist in the Postgres transaction. We do **not** need distributed
atomicity; we need a weaker, achievable invariant: **a Run may never reference a
blob that isn't durably present.** Enforce it by *ordering* inside enqueue —
stage bytes, **confirm** durability, *then* write the reference into the Run row.
Failure modes: upload fails → enqueue raises, no Run; upload succeeds then the
txn rolls back → an **orphan blob**, reclaimed by GC (benign, self-healing,
especially with content-addressed refs); Run-references-missing-blob →
**impossible** by construction. The asymmetry with output is principled: **input**
blobs must precede the Run (stage-and-confirm); **output** blobs follow it (the
existing outbox, post-commit). Data-flow direction dictates the ordering.

## Where codec infrastructure lives

A codec's staging target (the blob store) is **boundary infrastructure
registered on the App**, not an Operation Capability — so accepting a file never
appears in an Operation's signature, and a pass-through enqueuer needn't declare
it. Staging is therefore **transport mechanics** (ADR-0025's second category),
governed by transport-level limits (size/rate) rather than `db` attenuation: a
read-only Caller *may* upload bytes (uploading is not a domain write) but is
bounded at the door. And staging is **boundary telemetry**, never a domain
**Effect** — keeping it out of the effect log and the Zero-Effect wedge, which
would otherwise be polluted by every upload.

## Considered options

- **Keep universal serialization (ADR-0006 as written)** — rejected: it is the
  straitjacket this ADR exists to remove; it makes HTTP worse than a hand-written
  endpoint for the sake of a guarantee only the queue needs.
- **Graded projectability** (an Operation declares the transport subset it
  supports; serialization required only for the queue subset) — rejected *in
  favor of coherence-sorting*. Grading concedes the skeptic's point ("some
  Operations just aren't projectable here"); coherence-sorting defends it
  ("incoherence is a different primitive or a diagnostic, never a silent hole").
  The two reach the same eligibility set, but sorting keeps the boundary sharp and
  compile-checked rather than a per-Operation opt-out.
- **Per-projection escape hatches / configuration knobs** for staging semantics
  (attenuation, effect-visibility, store-as-capability) — rejected: each "I can
  see both sides" decomposed into a sharper split with a single answer plus, at
  most, one deployment choice (which blob backend). A knob on security or effect
  semantics turns "authorization *is* capability attenuation" into "…unless a flag
  is flipped," which kills the guarantee.
- **Allow ORM objects / arbitrary pickling across the gap** — rejected for the
  same reasons as ADR-0006: stale snapshots, identity ambiguity, and a refusal to
  make "the referent vanished" legible.

## Consequences

- ADR-0006 is **superseded**: its "IDs not objects" convention survives as the
  identity codec, but "serializability binds the Operation, universally" is
  replaced by "addressability binds it, and only for durable projections."
- The serialization module generalizes into a **Codec** registry; the Compile
  lint upgrades from "is this annotation JSON?" to the three-door diagnostic
  ("no codec, not a Capability, not recomputable → here are your options").
- Synchronous projections may now carry shapes (streams, uploads) the durable
  substrate never sees, with no change to the `Run` schema.
- A residual, deliberate non-universality is documented: a never-completing
  stateless stream is HTTP-only. This is semantic honesty, not a leak.
- The defensible pitch is fixed: **one authored unit, projected onto every
  transport for which projection is coherent**, with a symmetric, compile-checked
  boundary — not "one primitive projects everywhere."
