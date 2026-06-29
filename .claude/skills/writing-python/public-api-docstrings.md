# Public API surface

Conventions for thrum's *user-facing* API — the framework's public decorators,
parameter builders, and classes a consumer imports and reads in an IDE tooltip or
generated docs. Distilled from FastAPI, which is the model thrum follows here.

This is the one place the no-markdown rule in [SKILL.md](SKILL.md) does **not** apply.
These docstrings render to documentation, so they use markdown deliberately. Everything
internal stays under SKILL.md's default discipline. The split is sharp — decide by
audience, not by size.

## The public / internal split

- **Internal helpers get no docstring.** The code plus a sparse comment carry it.
  FastAPI's `utils.py` and the internals of `dependencies/utils.py` run hundreds of
  lines with zero docstrings.
- **Public symbols get a rich docstring _and_ per-parameter documentation.**

A 200-line internal function stays bare; a one-line public re-export gets documented.

## Document parameters inline with `Doc()`

Attach docs to each parameter at its definition via `Annotated[T, Doc(...)]`, not in a
docstring `Args:` block. The doc lives next to the type and default and survives
refactors. (FastAPI uses this ~780 times; it is the dominant documentation mechanism,
which is why those huge files carry zero `#` comments.)

```python
from typing import Annotated
from annotated_doc import Doc

def get(
    path: Annotated[
        str,
        Doc("The URL path for this operation, e.g. `/items/{item_id}`."),
    ],
    *,
    deprecated: Annotated[
        bool | None,
        Doc(
            """
            Mark this operation as deprecated.

            It will appear struck through in the generated docs, but keep working.
            """
        ),
    ] = None,
) -> Callable: ...
```

- One short sentence → inline `Doc("...")`.
- More than a sentence → triple-quoted block, indented, opening quotes on their own line.
- Prose is capitalized and ends with a period. Describe behavior and **why a parameter
  exists** ("available only for compatibility"), never its type or default — those are
  already in the signature.

## Docstring anatomy

A public class or method docstring, in order:

1. A one-line summary sentence.
2. Optionally, a paragraph of prose.
3. A `Read more` pointer: `Read more in the [thrum docs for X](url).`
4. An `## Example` heading followed by a fenced ` ```python ` block.

```python
class FastAPI(Starlette):
    """
    `FastAPI` app class, the main entrypoint to use FastAPI.

    Read more in the
    [FastAPI docs for First Steps](https://fastapi.tiangolo.com/tutorial/first-steps/).

    ## Example

    ```python
    from fastapi import FastAPI

    app = FastAPI()
    ```
    """
```

Markdown is expected: backticks for code, `[text](url)` links, `##` headings, fenced
code blocks. Keep the example minimal and runnable — imports through to the one call
that demonstrates the symbol.

## Module docstrings: almost never on the public surface

FastAPI ships exactly one module docstring across its whole package — a one-line
tagline in `__init__.py` — and none elsewhere. Don't open a public module with a
"what this file does" string.

This is a deliberate departure from SKILL.md, which endorses a responsibility-stating
module docstring for *internal* modules. Internal modules keep that guidance; the
public package surface follows FastAPI and omits it.
