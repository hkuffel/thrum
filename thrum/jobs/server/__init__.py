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
