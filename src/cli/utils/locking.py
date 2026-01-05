"""File locking utilities for multi-agent coordination.

Provides cross-process file locking using fcntl to prevent race conditions
when multiple agents access shared resources like claims or token files.
"""

import fcntl
from contextlib import contextmanager
from pathlib import Path
from typing import Generator, Literal


@contextmanager
def file_lock(
    lock_path: Path,
    exclusive: bool = True,
    timeout: float = 30.0,
    retry_interval: float = 0.1,
) -> Generator[None, None, None]:
    """Context manager for file-based locking.

    Creates a lock file and acquires a lock on it. Use this when you need
    to coordinate access to a resource across multiple processes.

    Args:
        lock_path: Path to the lock file (will be created if needed)
        exclusive: If True, acquire exclusive lock (LOCK_EX).
                   If False, acquire shared lock (LOCK_SH).
        timeout: Maximum time to wait for the lock (default: 30 seconds).
        retry_interval: Time between retry attempts (default: 0.1 seconds).

    Raises:
        TimeoutError: If lock cannot be acquired within timeout.

    Example:
        with file_lock(Path("/tmp/my_resource.lock")):
            # Critical section - only one process can be here at a time
            do_something()
    """
    import time

    lock_path.parent.mkdir(parents=True, exist_ok=True)
    lock_path.touch(exist_ok=True)

    lock_type = fcntl.LOCK_EX if exclusive else fcntl.LOCK_SH

    with open(lock_path, "r") as lock_file:
        start_time = time.monotonic()
        while True:
            try:
                fcntl.flock(lock_file.fileno(), lock_type | fcntl.LOCK_NB)
                break  # Lock acquired
            except BlockingIOError:
                elapsed = time.monotonic() - start_time
                if elapsed >= timeout:
                    raise TimeoutError(
                        f"Could not acquire lock on {lock_path} after {timeout}s. "
                        f"Another process may be holding the lock. "
                        f"Check with: lsof {lock_path}"
                    )
                time.sleep(retry_interval)
        try:
            yield
        finally:
            fcntl.flock(lock_file.fileno(), fcntl.LOCK_UN)


@contextmanager
def locked_file(
    file_path: Path,
    mode: Literal["r", "w", "a"] = "r",
    exclusive: bool | None = None,
) -> Generator:
    """Context manager that opens a file with locking.

    Automatically chooses lock type based on mode if exclusive is not specified:
    - Read mode ('r'): shared lock (multiple readers allowed)
    - Write/append mode ('w', 'a'): exclusive lock (single writer)

    Args:
        file_path: Path to the file to open
        mode: File open mode ('r', 'w', or 'a')
        exclusive: Override automatic lock type selection

    Yields:
        The opened file handle

    Example:
        with locked_file(Path("/tmp/data.json"), "w") as f:
            json.dump(data, f)
    """
    if exclusive is None:
        exclusive = mode in ("w", "a")

    lock_type = fcntl.LOCK_EX if exclusive else fcntl.LOCK_SH

    file_path.parent.mkdir(parents=True, exist_ok=True)

    # For write mode, we need to create the file if it doesn't exist
    if mode in ("w", "a") and not file_path.exists():
        file_path.touch()

    with open(file_path, mode) as f:
        try:
            fcntl.flock(f.fileno(), lock_type)
            yield f
        finally:
            fcntl.flock(f.fileno(), fcntl.LOCK_UN)
