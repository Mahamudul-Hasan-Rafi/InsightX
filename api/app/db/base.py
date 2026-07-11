# api/app/db/base.py
#
# PURPOSE:
#   Declares the SQLAlchemy ORM base class that all models inherit from.
#   Kept in its own file to prevent circular imports:
#     - models import Base from here
#     - session imports engine/Base from here
#     - if both were in the same file, importing one would pull in the other

from sqlalchemy.orm import DeclarativeBase


class Base(DeclarativeBase):
    """
    SQLAlchemy 2.0 declarative base.
    Uses the class-based API (not the legacy declarative_base() function).
    All ORM models (Datasource and future models) subclass this.
    """
    pass
