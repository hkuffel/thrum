"""The declarative base binding every model to Thrum's dedicated schema."""

from __future__ import annotations

from sqlalchemy import MetaData
from sqlalchemy.orm import DeclarativeBase

SCHEMA = "thrum"


class Base(DeclarativeBase):
    """Declarative base whose tables live in Thrum's own schema.

    A dedicated schema keeps Thrum's tables from colliding with the user's and
    scopes the migrations Thrum owns.
    """

    metadata = MetaData(schema=SCHEMA)
