# Capability attenuation is declared on the Operation and narrowed by the Caller

## Context

"Authorization is capability attenuation" (VISION): a read-only caller gets a
write-blocked session. Two pressures pulled in opposite directions. Expressing
attenuation as distinct capability *types* (`ReadOnly Session`, `TenantScoped
Session`, `AmountCapped PaymentGateway`) would explode the type space as axes
multiply and would stop an Operation being authored once and run under different
authorities. But expressing it *only* as a runtime Caller fact leaves the
Operation's signature silent about whether it can write — a readability loss, and
it removes the static handle the **Zero-Effect** read-only exemption needs.

## Decision

Attenuation is **declared on the Operation** as a least-authority *ceiling* via an
annotation marker on the capability param — `db: ReadOnly[Session]` — and
**narrowed further by the Caller** at construction. The granted authority is the
`min` of the two.

- The marker is `Annotated[Session, ReadOnly]`: the **registered capability type
  is still `Session`** (one Provider, no type explosion), and the capability
  discriminator (keyword-only + registered type) is unaffected. The marker is
  read separately, only to attenuate.
- The **unmarked default is full (write-capable) authority**; read-only is the
  marked, intentful case. This keeps onus off the common (writing) path and lands
  the marker exactly on the observability surface, where it is most
  self-documenting.
- "This Operation is read-only" (declares no write-capable Capability) is read
  **statically from the declaration** — that is what the Zero-Effect exemption
  keys on, never a guess.

## Consequences

- The read-only Control Plane is the birthplace of attenuation: its built-in
  operations declare `ReadOnly[Session]`, so the first attenuated capability (a
  write-blocking session) ships with this slice.
- The classifier learns to unwrap `Annotated` to find the registered type and to
  read attenuation markers from the metadata.
- A write the Operation declared but the Caller forbade **raises**, so a
  write-declared Operation cannot silently no-op past the exemption — the static
  exemption rule stays sound under per-Caller attenuation.

## Considered and rejected

- **Distinct capability types per attenuation** — explodes the type space; breaks
  author-once-run-under-many-authorities.
- **Caller-only attenuation, nothing on the signature** — silent signatures, and
  no static basis for the Zero-Effect exemption.
- **Write is the marked case (`Write[Session]`)** — taxes the 80% writing path to
  discipline the 20%; we put the marker on the rare read-only case instead.
