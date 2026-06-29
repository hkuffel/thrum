"""thrum — author an `@operation` once, project it onto any surface."""

from __future__ import annotations

from thrum.jobs.app import App as App
from thrum.jobs.app import CompileError as CompileError
from thrum.jobs.registry import Operation as Operation
from thrum.jobs.registry import Registry as Registry
from thrum.jobs.registry import operation as operation

__version__ = "0.0.2"

__all__ = ["App", "CompileError", "Operation", "Registry", "operation", "__version__"]
