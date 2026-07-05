---
name: writing-python
description: Conventions for writing good Python in the thrum repo — comment discipline, docstrings, public-API documentation, typing, control flow, imports, and module hygiene. Use when writing, reviewing, or refactoring Python in this repo, when documenting a public API surface, or when the user asks whether code is clean, idiomatic, or well-commented.
---

# Writing Python in thrum

House style for Python in this repo. Two parts: comment discipline and general code standards (see
[python-standards.md](python-standards.md)).

## Comments

A comment exists to explain non-intuitive code or a decision the reader cannot
recover from the code itself. If the code already says it, delete the comment.

This applies to every comment form: inline (`# ...`), function and class
docstrings, and module-level docstrings at the top of a file.

### Be concise

Get to the point. One claim per comment, stated plainly.

- No parentheticals or asides. Cut the digression or promote it to its own sentence.
- No restating the signature. Types live in annotations; the docstring covers intent
  and contract, not the parameter list.
- Don't explain something in a module docstring that you're going to re-explain
  in the relevant function docstring. Don't explain something in a function
  docstring that you're going to explain in a helper function docstring. Explain
  logic at the closest point to the actual code you're explaining, once.

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

Never reference ephemeral artifacts from an authoring or chat session: `Option 2`,
`Grill question 8`, `core-loop Q5`, `Q2`. These mean nothing to a future reader and
cannot be looked up. If the reasoning matters, state the reasoning; if it lives in a
durable doc, cite the doc, ONLY IF IT IS VERY NON-INTUITIVE. YOU SHOULD ALMOST
NEVER NEED TO DO THIS.

```python
# WRONG (see thrum/jobs/worker/claim.py): cites a session artifact
# Postgres is the clock authority (core-loop Q5): derive the lease window from
# the DB clock, not the Worker's wall clock.

# CORRECT: state the decision
# Derive the lease window from the DB clock, not the Worker's wall clock
```

### When a comment earns its place

- A decision with more intuitive alternatives: why this approach over the obvious one.
- A non-local invariant or ordering constraint the reader can't see from here.
- A correctness-critical subtlety (concurrency, clock authority, transaction boundary).

## Docstring standards

standards on docstring formatting are in
[google-docstring-standards.md](google-docstring-standards.md). Standards on
docstring content are in [PEP-257.md](PEP-257.md). Examples are in
[example-google-docstrings.md](example-google-style-docstrings.md).

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
