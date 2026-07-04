from __future__ import annotations

from sqlalchemy import MetaData
from sqlalchemy.orm import DeclarativeBase

SCHEMA = "thrum"


class Base(DeclarativeBase):
    metadata = MetaData(schema=SCHEMA)
