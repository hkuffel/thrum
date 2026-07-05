"""Scaffold for the control plane's HTTP projection.

Unimplemented: importing gates on the optional server extra, and ``create_app``
is a placeholder. The HTTP transport is a co-equal projection of the control
plane's Operations, not a separate API layer.
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
    """Build the ASGI app for the HTTP projection. Not yet implemented."""
    raise NotImplementedError("scaffold")
