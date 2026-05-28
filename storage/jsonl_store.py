"""JSONL-backed implementation of :class:`storage.base.BaseStore`.

Each collection is stored as a separate ``.jsonl`` file inside a configurable
data directory.  Records are appended one JSON object per line, making the
format append-friendly and easy to inspect with standard CLI tools.

Thread safety is provided via :mod:`asyncio` locks (one per collection) so
concurrent coroutines writing to the same file do not interleave.
"""

from __future__ import annotations

import asyncio
import json
import uuid
from pathlib import Path
from typing import Optional

from storage.base import BaseStore


class JsonlStore(BaseStore):
    """Append-only JSONL file store.

    Args:
        data_dir: Filesystem directory where ``.jsonl`` files are kept.
            Created automatically if it does not exist.
    """

    def __init__(self, data_dir: str | Path = "data") -> None:
        self._data_dir = Path(data_dir)
        self._data_dir.mkdir(parents=True, exist_ok=True)
        # Per-collection asyncio locks for thread/coroutine safety.
        self._locks: dict[str, asyncio.Lock] = {}

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _path_for(self, collection: str) -> Path:
        """Return the ``.jsonl`` file path for *collection*."""
        return self._data_dir / f"{collection}.jsonl"

    def _lock_for(self, collection: str) -> asyncio.Lock:
        """Return (or create) the asyncio lock for *collection*."""
        if collection not in self._locks:
            self._locks[collection] = asyncio.Lock()
        return self._locks[collection]

    # ------------------------------------------------------------------
    # BaseStore interface
    # ------------------------------------------------------------------

    async def save(self, collection: str, data: dict) -> str:
        """Save a record. If it has an ID and already exists, update/overwrite it; otherwise append."""
        record = dict(data)  # shallow copy — don't mutate caller's dict
        if "id" not in record:
            record["id"] = str(uuid.uuid4())
        record_id: str = record["id"]

        async with self._lock_for(collection):
            path = self._path_for(collection)
            records = []
            updated = False
            if path.exists():
                with path.open("r", encoding="utf-8") as fh:
                    for line in fh:
                        line = line.strip()
                        if not line:
                            continue
                        try:
                            r = json.loads(line)
                            if r.get("id") == record_id:
                                records.append(record)
                                updated = True
                            else:
                                records.append(r)
                        except json.JSONDecodeError:
                            pass
            if not updated:
                records.append(record)

            with path.open("w", encoding="utf-8") as fh:
                for r in records:
                    fh.write(json.dumps(r, default=str) + "\n")

        return record_id

    async def get(self, collection: str, id: str) -> Optional[dict]:
        """Scan the JSONL file for a record with matching ``id``."""
        path = self._path_for(collection)
        if not path.exists():
            return None

        async with self._lock_for(collection):
            with path.open("r", encoding="utf-8") as fh:
                for raw_line in fh:
                    raw_line = raw_line.strip()
                    if not raw_line:
                        continue
                    record = json.loads(raw_line)
                    if record.get("id") == id:
                        return record
        return None

    async def list_recent(self, collection: str, limit: int = 50) -> list[dict]:
        """Return the last *limit* records, newest first."""
        path = self._path_for(collection)
        if not path.exists():
            return []

        async with self._lock_for(collection):
            with path.open("r", encoding="utf-8") as fh:
                lines = fh.readlines()

        records: list[dict] = []
        for raw_line in reversed(lines):
            raw_line = raw_line.strip()
            if not raw_line:
                continue
            records.append(json.loads(raw_line))
            if len(records) >= limit:
                break
        return records

    async def query(self, collection: str, filters: dict) -> list[dict]:
        """Return all records whose fields match every key in *filters*."""
        path = self._path_for(collection)
        if not path.exists():
            return []

        results: list[dict] = []
        async with self._lock_for(collection):
            with path.open("r", encoding="utf-8") as fh:
                for raw_line in fh:
                    raw_line = raw_line.strip()
                    if not raw_line:
                        continue
                    record = json.loads(raw_line)
                    if all(record.get(k) == v for k, v in filters.items()):
                        results.append(record)
        return results
