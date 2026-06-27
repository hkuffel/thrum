# Coding Standards

The simplify agent loads this file via `@.sandcastle/CODING_STANDARDS.md` so
these conventions are applied without costing tokens during implementation.

## Style

- Follow the ruff config in `pyproject.toml`: line length 100, target py311,
  lint rules `E, F, I, UP, B`. Run `uv run ruff check .` and `uv run ruff format .`.
- Type everything reachable from the public API; thrum ships `py.typed`.
- Prefer explicit, readable code over clever one-liners.

## Testing

- Tests live under `tests/` and run with `uv run pytest` (`asyncio_mode = auto`).
- DB-backed tests use real ephemeral Postgres via `testcontainers` — do not mock
  the database. These require a Docker daemon to run.

## Architecture

- The Worker is async-first; respect the execution model in
  `docs/adr/0005-worker-execution-model.md`.
- Read `CONTEXT.md` for the domain language and the `docs/adr/` set before
  changing a previously-decided area.
- Keep modules focused on a single responsibility.
