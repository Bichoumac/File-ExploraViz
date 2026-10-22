"""Dates ISO 8601 en heure locale (variable TZ du conteneur)."""

from __future__ import annotations

import time
from datetime import date, datetime


def iso(ts: float | None = None) -> str:
    return datetime.fromtimestamp(time.time() if ts is None else ts).astimezone().isoformat(timespec="seconds")


def parse_iso(value: str | None) -> float | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(str(value)).timestamp()
    except ValueError:
        return None


def day_start(d: date) -> int:
    """Epoch du début de journée locale."""
    return int(datetime(d.year, d.month, d.day).timestamp())


def day_end(d: date) -> int:
    """Epoch de la dernière seconde de la journée locale."""
    return day_start(d) + 86399
