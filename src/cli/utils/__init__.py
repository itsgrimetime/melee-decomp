"""Shared CLI utilities.

This package contains common utilities used across CLI modules:
- locking: File locking with fcntl for multi-agent coordination
- json_storage: JSON file storage with expiry and atomic writes
"""

from .locking import locked_file, file_lock
from .json_storage import (
    load_json_with_expiry,
    save_json_atomic,
    load_json_safe,
)

__all__ = [
    "locked_file",
    "file_lock",
    "load_json_with_expiry",
    "save_json_atomic",
    "load_json_safe",
]
