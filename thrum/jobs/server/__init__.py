"""The Control Plane: the single API through which humans and agents observe and
command the system. Dashboard, `--json` CLI, and (v2) MCP server are thin clients
of it.

Importing it without the extra installed gives a clear, actionable error rather
than a bare ModuleNotFoundError.
"""

from __future__ import annotations

try:
    import fastapi as _fastapi  # noqa: F401
except ModuleNotFoundError as exc:  # pragma: no cover
    raise ModuleNotFoundError(
        "The Thrum control plane requires the optional server dependencies. "
        "Install them with:  pip install 'thrum[server]'"
    ) from exc


def create_app():  # noqa: ANN201
    raise NotImplementedError("scaffold")
