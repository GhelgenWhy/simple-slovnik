"""Dataclass representing a vocabulary entry."""
from __future__ import annotations
from dataclasses import dataclass


@dataclass
class Word:
    """A single Czech ↔ uk/ru vocabulary entry.

    All fields are optional except ``cs`` (the headword).

    Sync fields (used by :mod:`slovnik.sync`, see docs/MOBILE_PLAN.md):

    - ``deleted`` — tombstone flag: 1 means the row was deleted on this or
      another device and is kept only for one-way-sync bookkeeping.
    - ``device_id`` — id of the device that last wrote the row.
    - ``dirty`` — 1 while the local change has not been pushed to the
      sync server yet (local clock-skew-proof change tracking).
    - ``revision`` — local edit counter, bumped on every local change
      (including delete/rename). A push records ``(id, revision)`` and
      only confirms rows whose revision is unchanged afterwards, so an
      edit made during the network request is never silently marked as
      pushed.
    """
    id: int | None = None
    cs: str = ""
    uk: str = ""
    ru: str = ""
    pos: str = ""
    note: str = ""
    created_at: str = ""
    updated_at: str = ""
    deleted: int = 0
    device_id: str = ""
    dirty: int = 0
    revision: int = 0
