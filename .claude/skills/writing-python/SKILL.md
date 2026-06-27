---
name: writing-python
description: Conventions for writing good Python in the thrum repo — comment discipline, typing, control flow, imports, and module hygiene. Use when writing, reviewing, or refactoring Python in this repo, or when the user asks whether code is clean, idiomatic, or well-commented.
---

# Writing Python in thrum

House style for Python in this repo. Two parts: comment discipline (below, the part
people get wrong most often) and general code standards (see
[python-standards.md](python-standards.md)).

## Comments

A comment exists to explain non-intuitive code or a decision the reader cannot
recover from the code itself. If the code already says it, delete the comment.

This applies to every comment form: inline (`# ...`), function and class
docstrings, and module-level docstrings at the top of a file.

### Be concise

Get to the point. One claim per comment, stated plainly.

- No parentheticals or asides. Cut the digression or promote it to its own sentence.
- No markdown — no bold, no backticked headings, no bullet scaffolding inside a
  docstring. Prose and, where it clarifies, a short indented code or predicate sketch.
- No restating the signature. Types live in annotations; the docstring covers intent
  and contract, not the parameter list.

```python
# WRONG: describes the obvious, hedges, digresses
# Increment the counter by one (we do this so that, eventually, when the loop
# finishes, the total reflects how many items we saw — see note below).
count += 1

# CORRECT: omit it; the code is self-evident
count += 1
```

```python
# WRONG: explains what, not why
# Loop over the rows and skip the locked ones.
# CORRECT: explains the non-obvious decision
# SKIP LOCKED so a slow Worker never blocks the dispatch query behind its row locks.
```

### Reference only things that exist in the codebase

A comment may point to a durable artifact: a code object (class or function name),
`CONTEXT.md`, or an ADR by its real identifier (`ADR-0008`, `docs/adr/`). Verify the
target exists before you cite it — a reference to an ADR the repo doesn't have is
worse than no reference.

Do not cite PRDs. They are transient implementation plans that get consumed once the
work lands; a PRD number means nothing to someone reading the code later. If a PRD's
decision is durable, it has a corresponding ADR — cite that instead.

Never reference ephemeral artifacts from an authoring or chat session: `Option 2`,
`Grill question 8`, `core-loop Q5`, `Q2`. These mean nothing to a future reader and
cannot be looked up. If the reasoning matters, state the reasoning; if it lives in a
durable doc, cite the doc.

```python
# WRONG (see src/thrum/jobs/worker/claim.py): cites a session artifact
# Postgres is the clock authority (core-loop Q5): derive the lease window from
# the DB clock, not the Worker's wall clock.

# CORRECT: state the decision; cite the ADR that records it
# Derive the lease window from the DB clock, not the Worker's wall clock, so all
# liveness comparisons share one clock (ADR-0013).
```

### When a comment earns its place

- A decision with live alternatives: why this approach over the obvious one.
- A non-local invariant or ordering constraint the reader can't see from here.
- A correctness-critical subtlety (concurrency, clock authority, transaction boundary).
- A module docstring stating the file's responsibility and the contract it upholds.

`src/thrum/jobs/worker/claim.py`'s module docstring is a good model for scope and
tone — minus its `core-loop Q2/Q5` references, which are exactly the ephemeral
citations to avoid.

## Code standards

The full set — typing, LBYL control flow, pathlib, imports, module hygiene,
anti-patterns — is in [python-standards.md](python-standards.md). Read it before
writing or reviewing non-trivial Python. Highlights:

- Modern type syntax (`list[str]`, `X | None`); target is `requires-python >=3.11`.
- Prefer explicit preconditions over exceptions for routine branching.
- `pathlib` over `os.path`; always pass `encoding="utf-8"`.
- Module-level absolute imports; inline imports only for `TYPE_CHECKING` or cycles.
- No backwards-compat shims by default; no re-exports — one canonical import path.
- Declare variables close to use; cap nesting at 4 levels.
