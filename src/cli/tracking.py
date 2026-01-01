"""Match history and operation tracking utilities.

Provides utilities for:
- Tracking match score progression over time
- Detecting duplicate operations to avoid redundant API calls
"""

import json
import time
from pathlib import Path

from rich.console import Console

from src.client.api import _get_agent_id

# Config directory
DECOMP_CONFIG_DIR = Path.home() / ".config" / "decomp-me"
DECOMP_CONFIG_DIR.mkdir(parents=True, exist_ok=True)

# Get agent ID for file isolation
AGENT_ID = _get_agent_id()

# Match history file (per-agent for isolation)
_history_suffix = f"_{AGENT_ID}" if AGENT_ID else ""
MATCH_HISTORY_FILE = DECOMP_CONFIG_DIR / f"match_history{_history_suffix}.json"

# Operation tracking file
_RECENT_OPS_FILE = DECOMP_CONFIG_DIR / "recent_operations.json"
_OP_CACHE_TTL = 60  # Seconds before an operation "expires"

console = Console()


# =============================================================================
# Match History Tracking
# =============================================================================


def load_match_history() -> dict:
    """Load match history for all scratches.

    Returns dict of slug -> list of {score, max_score, match_pct, timestamp}
    """
    if not MATCH_HISTORY_FILE.exists():
        return {}
    try:
        with open(MATCH_HISTORY_FILE, "r") as f:
            return json.load(f)
    except (json.JSONDecodeError, IOError):
        return {}


def save_match_history(data: dict) -> None:
    """Save match history."""
    MATCH_HISTORY_FILE.parent.mkdir(parents=True, exist_ok=True)
    with open(MATCH_HISTORY_FILE, "w") as f:
        json.dump(data, f, indent=2)


def record_match_score(slug: str, score: int, max_score: int) -> dict:
    """Record a new match score for a scratch.

    Args:
        slug: Scratch slug
        score: Current diff score (0 = perfect match)
        max_score: Maximum possible score

    Returns:
        History entry that was added
    """
    history = load_match_history()
    if slug not in history:
        history[slug] = []

    match_pct = 100.0 if score == 0 else (1.0 - score / max_score) * 100 if max_score > 0 else 0.0

    entry = {
        "score": score,
        "max_score": max_score,
        "match_pct": round(match_pct, 1),
        "timestamp": time.time(),
    }

    # Only record if score changed from last entry
    if history[slug]:
        last = history[slug][-1]
        if last["score"] == score and last["max_score"] == max_score:
            return entry  # No change, don't record duplicate

    history[slug].append(entry)

    # Keep only last 50 entries per scratch
    if len(history[slug]) > 50:
        history[slug] = history[slug][-50:]

    save_match_history(history)
    return entry


def get_match_history(slug: str) -> list:
    """Get match history for a scratch.

    Returns list of {score, max_score, match_pct, timestamp}
    """
    history = load_match_history()
    return history.get(slug, [])


def format_match_history(slug: str, max_entries: int = 10) -> str:
    """Format match history as a compact string for display.

    Shows progression like: "0% → 45% → 71.5% → 100%"
    """
    history = get_match_history(slug)
    if not history:
        return ""

    # Get unique match percentages (dedupe consecutive same values)
    pcts = []
    last_pct = None
    for entry in history[-max_entries:]:
        pct = entry["match_pct"]
        if pct != last_pct:
            pcts.append(pct)
            last_pct = pct

    if len(pcts) <= 1:
        return ""

    return " → ".join(f"{p}%" for p in pcts)


# =============================================================================
# Operation Tracking (Duplicate Detection)
# =============================================================================


def _load_recent_ops() -> dict:
    """Load recent operations cache."""
    if not _RECENT_OPS_FILE.exists():
        return {"operations": []}
    try:
        with open(_RECENT_OPS_FILE, "r") as f:
            return json.load(f)
    except (json.JSONDecodeError, IOError):
        return {"operations": []}


def _save_recent_ops(data: dict) -> None:
    """Save recent operations cache."""
    _RECENT_OPS_FILE.parent.mkdir(parents=True, exist_ok=True)
    with open(_RECENT_OPS_FILE, "w") as f:
        json.dump(data, f, indent=2)


def check_duplicate_operation(op_type: str, key: str, warn: bool = True) -> bool:
    """Check if an operation was recently performed, warn if duplicate.

    Args:
        op_type: Operation type (e.g., "extract_get", "scratch_create")
        key: Unique key for the operation (e.g., function name, slug)
        warn: If True, print a warning for duplicates

    Returns:
        True if this is a duplicate (operation was recently performed)
    """
    data = _load_recent_ops()
    now = time.time()

    # Clean expired entries
    data["operations"] = [
        op for op in data["operations"]
        if now - op.get("timestamp", 0) < _OP_CACHE_TTL
    ]

    # Check for duplicate
    op_key = f"{op_type}:{key}"
    for op in data["operations"]:
        if op.get("key") == op_key:
            age = int(now - op.get("timestamp", 0))
            if warn:
                console.print(
                    f"[yellow]Note:[/yellow] This operation was already run {age}s ago. "
                    f"Skipping redundant API call."
                )
            return True

    # Record this operation
    data["operations"].append({
        "key": op_key,
        "timestamp": now,
    })

    # Keep only last 100 operations
    if len(data["operations"]) > 100:
        data["operations"] = data["operations"][-100:]

    _save_recent_ops(data)
    return False


def clear_operation_cache() -> None:
    """Clear the operation cache (useful for testing)."""
    if _RECENT_OPS_FILE.exists():
        _RECENT_OPS_FILE.unlink()
