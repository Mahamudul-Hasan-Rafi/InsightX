# api/app/db/models/__init__.py
# Required to make this directory a Python package.
# Import models here so that Base.metadata knows about them at startup.
# This ensures create_all() in main.py creates all tables.

from app.db.models.datasource import Datasource  # noqa: F401 — imported for side effect
