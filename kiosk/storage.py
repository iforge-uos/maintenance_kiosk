"""Atomic, fsynced local files; no database daemon or database file.

Each journal entry is a complete transaction. Its rename commits the record and
delivery jobs together. A process dying before rename leaves only an ignored
temporary file. The journal, rather than a cached projection, is authoritative.
"""

from contextlib import contextmanager
import fcntl
import json
import os
from pathlib import Path
import tempfile
import threading


def atomic_json(path, value):
    path = Path(path)
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    descriptor, temporary = tempfile.mkstemp(prefix=".pending-", dir=path.parent)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            json.dump(value, handle, ensure_ascii=False, allow_nan=False, separators=(",", ":"))
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
        directory = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY)
        try:
            os.fsync(directory)
        finally:
            os.close(directory)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


class Journal:
    def __init__(self, data_dir):
        self.root = Path(data_dir)
        self.root.mkdir(mode=0o700, parents=True, exist_ok=True)
        self.entries = self.root / "journal"
        self.entries.mkdir(mode=0o700, exist_ok=True)
        self._thread_lock = threading.RLock()

    @contextmanager
    def locked(self):
        with self._thread_lock:
            descriptor = os.open(self.root / ".lock", os.O_RDWR | os.O_CREAT, 0o600)
            try:
                fcntl.flock(descriptor, fcntl.LOCK_EX)
                yield
            finally:
                fcntl.flock(descriptor, fcntl.LOCK_UN)
                os.close(descriptor)

    def read(self):
        result = []
        for path in sorted(self.entries.glob("[0-9]*.json")):
            try:
                entry = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError) as exc:
                raise RuntimeError(f"Journal entry {path.name} is unreadable; restore a verified backup.") from exc
            if not isinstance(entry, dict) or entry.get("sequence") != len(result) + 1:
                raise RuntimeError(f"Journal sequence is damaged at {path.name}; restore a verified backup.")
            result.append(entry)
        return result

    def append(self, entry, sequence):
        """Caller must hold locked() and supply the next sequence."""
        path = self.entries / f"{sequence:020d}.json"
        if path.exists():
            raise RuntimeError("Journal sequence already exists")
        atomic_json(path, {**entry, "sequence": sequence})
