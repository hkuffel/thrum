"""Shared declarative base. All Thrum tables live in the dedicated `thrum`
Postgres schema, never in `public` — see docs/adr/0004-dedicated-schema-owned-migrations.md.
These model definitions are the single source of truth the SDK, Worker,
scheduler, and control plane all agree on ("Postgres is the protocol")."""

from __future__ import annotations

from sqlalchemy import MetaData
from sqlalchemy.orm import DeclarativeBase

SCHEMA = "thrum"


class Base(DeclarativeBase):
    metadata = MetaData(schema=SCHEMA)
