# Python standards

General code standards for the thrum repo. Comment discipline lives in
[SKILL.md](SKILL.md); this file covers everything else. Target runtime is
`requires-python >=3.11`.

## Typing

Use modern syntax everywhere. No `typing.List`, `typing.Optional`, `typing.Union`.

```python
# CORRECT
def f(items: list[str], cfg: dict[str, int] | None = None) -> str | None: ...

# WRONG
from typing import List, Optional
def f(items: List[str], cfg: Optional[dict] = None) -> Optional[str]: ...
```

Annotate public functions and dataclass fields. Let local variables infer unless an
annotation prevents a real ambiguity. Import names used only in annotations under
`if TYPE_CHECKING:` to avoid import cost and cycles.

## Control flow: prefer explicit preconditions

Lean LBYL when a cheap, precise check keeps intent clearer than a `try/except`.
Exceptions are not control flow.

```python
# CORRECT
if key in mapping:
    process(mapping[key])
else:
    handle_missing()

# CORRECT: .get() with default
process(mapping.get(key, default))

# WRONG: exception as a branch
try:
    process(mapping[key])
except KeyError:
    handle_missing()
```

Exceptions are the right tool at error boundaries (CLI/API edges), when the
operation itself is the authoritative test, and when adding context before
re-raising. Otherwise let them bubble. When re-raising with context, chain
explicitly: `raise NewError(...) from e`.

## Path operations

Use `pathlib`, never `os.path`. Always specify encoding.

```python
# CORRECT
from pathlib import Path

cfg = Path.home() / ".config" / "thrum.yml"
if cfg.exists():
    content = cfg.read_text(encoding="utf-8")

# WRONG
import os.path
cfg = os.path.join(os.path.expanduser("~"), ".config", "thrum.yml")
content = open(cfg).read()  # platform-dependent encoding
```

Call `.exists()` only when filesystem presence is part of the requirement, not as a
blanket guard before `.resolve()` or `.is_relative_to()`. Use `resolve(strict=True)`
when absence is itself an error.

## Imports

1. Module-level by default.
2. Absolute imports only — no relative (`from .x import y`).
3. Inline imports only for `TYPE_CHECKING`, circular-dependency breaks, or genuinely
   optional features. An inline import without one of these reasons is wrong.

```python
# CORRECT
import json
from pathlib import Path
from thrum.jobs.models import Run

# WRONG
from .models import Run          # relative
def handler():
    import json                  # inline without justification
```

## Module hygiene

- No import-time side effects beyond simple constants and definitions. No I/O, no
  network, no filesystem work at module top level.
- `__init__.py` stays empty. No re-exports: every symbol has exactly one canonical
  import path. The one exception is a required plugin entry point, written as an
  explicit `from x import y as y`.

## Performance

Properties and magic methods (`__len__`, `__eq__`, …) must be O(1). If a value needs
I/O or iteration to produce, give it an explicit method name (`fetch_size()`), not a
property.

```python
# WRONG
@property
def size(self) -> int:
    return self._fetch_from_db()

# CORRECT
def fetch_size(self) -> int:
    return self._fetch_from_db()
```

## Anti-patterns

### No backwards-compat shims by default

Break and migrate immediately. Keep a legacy path only for a documented public API,
an explicit user request, or a prohibitively expensive migration.

```python
# WRONG
def process(data: dict, legacy_format: bool = False) -> Result: ...
# CORRECT
def process(data: dict) -> Result: ...
```

### Declare variables close to use

Don't compute a value far above where it's consumed, and don't destructure an object
into single-use locals.

```python
# WRONG
user = fetch_user(uid)
name = user.name      # used once
email = user.email    # used once
notify(name, email, user.role)

# CORRECT
user = fetch_user(uid)
notify(user.name, user.email, user.role)
```

### Cap nesting at 4 levels

Deeper than that, extract a helper function.

### Keep context managers in the `with` statement

```python
# CORRECT
with (lock if thread_safe else nullcontext()):
    process(data)

# WRONG: extracting obscures the __enter__/__exit__ lifecycle
cm = lock if thread_safe else nullcontext()
with cm:
    process(data)
```
