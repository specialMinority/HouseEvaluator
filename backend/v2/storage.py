"""Storage selection for the free, single-instance deployment profile."""

import os

from .models import ValidationError
from .store import SnapshotStore


def open_store(target):
    """Open a persistent local SQLite database; never silently downgrade a URL."""
    try:
        value = os.fspath(target)
    except TypeError as exc:
        raise ValidationError("storage target must be a local SQLite file") from exc
    if not isinstance(value, str) or value == ":memory:":
        raise ValidationError("operational storage requires a persistent local SQLite file")
    if value.lower().startswith(("postgres:", "postgresql:", "postgresql+")):
        raise ValidationError("PostgreSQL storage is not implemented; use one local SQLite instance")
    return SnapshotStore(value)
