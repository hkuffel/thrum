# Thrum is greenfield-first; the adoption unit is the project, not the function

Thrum will **not** chase incremental adoption — the "drop one operation into an
existing FastAPI/Celery app" path — because Thrum's value is **consolidation**,
and consolidation does not fractionalize. The wedge (effect observability, atomic
enqueue, one primitive across transports) exists only because Thrum owns the
session and the transaction; a single operation living inside someone else's app
owns one transaction while the surrounding app still owns the rest, so the dual-
write problem and the silent-failure blind spots persist at every boundary. That
delivers not a fraction of the value but a *confusing* version of it — a worse
first impression than not being tried. The adoption unit is therefore the
**project**, the way Rails, Django, and Phoenix were adopted: greenfield plus
delight, riding the steady stream of net-new apps. With coding agents, starting a
net-new Thrum app is cheaper than ever — which is the tailwind we lean on. (We do
*not* lean on agent-driven migration of mature production apps; that is exactly
where the cheap-rewrite argument is weakest.)

This is a deliberate scope decision — the explicit "no" matters as much as the
yes. The most likely future "fix" to this, made under adoption anxiety, is a
Celery/FastAPI compatibility shim that quietly dilutes the wedge; this ADR exists
to make that a conscious reversal rather than a drift.

Loosely held corollary (principle, not mandate): rejecting incremental adoption
is not the same as ignoring the on-ramp. The energy that a compat shim would
absorb is better spent making the *greenfield* on-ramp delightful — fast project
scaffolding, a visceral first-run demo, and agent-consumable templates/docs so
"write my app in Thrum" yields good Thrum. A narrow, non-diluting coexistence
(Thrum mounted alongside an existing app while a team grows greenfield surface
over time) is fine to allow but is not the thing to design *for*.

## Considered and rejected

- **Incremental / hybrid adoption** (Thrum operations sharing job load inside an
  existing Celery/FastAPI app) — rejected: co-ownership dilutes the consolidation
  that is the entire point.
- **A migration story for mature apps** — not claimed: faithful migration of
  accrued production correctness is the one thing agents do *not* make cheap, and
  it points the rewrite argument at the population where it is weakest.
