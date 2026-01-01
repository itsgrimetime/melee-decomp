"""JSON storage utilities with expiry and atomic writes.

Provides utilities for loading and saving JSON files with:
- Automatic expiry of stale entries
- Atomic writes to prevent corruption
- Safe loading with error handling
"""

import json
import time
from pathlib import Path
from typing import Any

from .locking import file_lock


def load_json_safe(path: Path) -> dict[str, Any]:
    """Load a JSON file safely, returning empty dict on errors.

    Args:
        path: Path to the JSON file

    Returns:
        Parsed JSON as dict, or empty dict if file doesn't exist or is invalid
    """
    if not path.exists():
        return {}

    try:
        with open(path, "r") as f:
            return json.load(f)
    except (json.JSONDecodeError, IOError):
        return {}


def load_json_with_expiry(
    path: Path,
    timeout_seconds: int,
    timestamp_field: str = "timestamp",
) -> dict[str, Any]:
    """Load JSON file and filter out entries older than timeout.

    Each entry in the JSON should have a timestamp field. Entries older
    than timeout_seconds will be filtered out.

    Args:
        path: Path to the JSON file
        timeout_seconds: Maximum age in seconds for entries
        timestamp_field: Name of the timestamp field in each entry

    Returns:
        Dict with only non-expired entries
    """
    data = load_json_safe(path)
    if not data:
        return {}

    now = time.time()
    return {
        key: value
        for key, value in data.items()
        if isinstance(value, dict)
        and now - value.get(timestamp_field, 0) < timeout_seconds
    }


def save_json_atomic(
    path: Path,
    data: dict[str, Any],
    lock_path: Path | None = None,
) -> None:
    """Save JSON data atomically with file locking.

    Uses a lock file to prevent race conditions when multiple processes
    write to the same file.

    Args:
        path: Path to the JSON file
        data: Data to save
        lock_path: Optional separate lock file path. If not provided,
                   uses path.with_suffix('.lock')
    """
    path.parent.mkdir(parents=True, exist_ok=True)

    if lock_path is None:
        lock_path = path.with_suffix(path.suffix + ".lock")

    with file_lock(lock_path, exclusive=True):
        with open(path, "w") as f:
            json.dump(data, f, indent=2)


def update_json_atomic(
    path: Path,
    key: str,
    value: Any,
    lock_path: Path | None = None,
) -> None:
    """Atomically update a single key in a JSON file.

    Reads the file, updates the key, and writes back atomically.

    Args:
        path: Path to the JSON file
        key: Key to update
        value: New value for the key
        lock_path: Optional separate lock file path
    """
    path.parent.mkdir(parents=True, exist_ok=True)

    if lock_path is None:
        lock_path = path.with_suffix(path.suffix + ".lock")

    with file_lock(lock_path, exclusive=True):
        data = load_json_safe(path)
        data[key] = value
        with open(path, "w") as f:
            json.dump(data, f, indent=2)


def delete_json_key_atomic(
    path: Path,
    key: str,
    lock_path: Path | None = None,
) -> bool:
    """Atomically delete a key from a JSON file.

    Args:
        path: Path to the JSON file
        key: Key to delete
        lock_path: Optional separate lock file path

    Returns:
        True if key was deleted, False if it didn't exist
    """
    if not path.exists():
        return False

    if lock_path is None:
        lock_path = path.with_suffix(path.suffix + ".lock")

    with file_lock(lock_path, exclusive=True):
        data = load_json_safe(path)
        if key not in data:
            return False
        del data[key]
        with open(path, "w") as f:
            json.dump(data, f, indent=2)
        return True
