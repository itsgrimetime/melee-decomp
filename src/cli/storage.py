"""Storage utilities for function tracking data.

Provides utilities for:
- Loading/saving completed functions from the database
- Loading/saving slug mappings (local to production)
- Context file resolution
"""

import json
from pathlib import Path

from src.client.api import _get_agent_id

# Config directory
DECOMP_CONFIG_DIR = Path.home() / ".config" / "decomp-me"
DECOMP_CONFIG_DIR.mkdir(parents=True, exist_ok=True)

# Project paths
PROJECT_ROOT = Path(__file__).parent.parent.parent
DEFAULT_MELEE_ROOT = PROJECT_ROOT / "melee"

# Production cookies file (used for synced scratches path)
PRODUCTION_COOKIES_FILE = DECOMP_CONFIG_DIR / "production_cookies.json"

# Get agent ID
AGENT_ID = _get_agent_id()


def load_completed_functions() -> dict:
    """Load completed functions from the SQLite database.

    Returns a dict compatible with the old JSON format for backward compatibility.
    """
    from src.db import get_db
    db = get_db()

    result = {}
    with db.connection() as conn:
        cursor = conn.execute("""
            SELECT function_name, match_percent, local_scratch_slug, production_scratch_slug,
                   is_committed, branch, pr_url, pr_number, pr_state, notes
            FROM functions
        """)
        for row in cursor.fetchall():
            result[row["function_name"]] = {
                "match_percent": row["match_percent"] or 0,
                "scratch_slug": row["local_scratch_slug"],
                "production_slug": row["production_scratch_slug"],
                "committed": bool(row["is_committed"]),
                "branch": row["branch"],
                "pr_url": row["pr_url"],
                "pr_number": row["pr_number"],
                "pr_state": row["pr_state"],
                "notes": row["notes"],
            }
    return result


def save_completed_functions(data: dict) -> None:
    """Save completed functions to the SQLite database.

    Accepts a dict in the old JSON format for backward compatibility.
    SQLite handles concurrency natively, so no file locking is needed.
    """
    from src.db import get_db
    db = get_db()

    for func_name, info in data.items():
        db.upsert_function(
            func_name,
            agent_id=AGENT_ID,
            match_percent=info.get("match_percent", 0),
            local_scratch_slug=info.get("scratch_slug"),
            production_scratch_slug=info.get("production_slug"),
            is_committed=info.get("committed", False),
            branch=info.get("branch"),
            pr_url=info.get("pr_url"),
            pr_number=info.get("pr_number"),
            pr_state=info.get("pr_state"),
            notes=info.get("notes"),
        )


def load_slug_map() -> dict:
    """Load local->production slug mapping from the SQLite database.

    Returns a dict keyed by production_slug for backward compatibility:
    {production_slug: {local_slug, function, match_percent, synced_at}}
    """
    from src.db import get_db
    db = get_db()

    result = {}
    with db.connection() as conn:
        cursor = conn.execute("""
            SELECT s.local_slug, s.production_slug, s.function_name, s.synced_at,
                   f.match_percent
            FROM sync_state s
            LEFT JOIN functions f ON s.function_name = f.function_name
        """)
        for row in cursor.fetchall():
            result[row["production_slug"]] = {
                "local_slug": row["local_slug"],
                "function": row["function_name"],
                "match_percent": row["match_percent"] or 0,
                "synced_at": row["synced_at"],
            }
    return result


def save_slug_map(data: dict) -> None:
    """Save local->production slug mapping to the SQLite database.

    Accepts a dict keyed by production_slug for backward compatibility.
    """
    from src.db import get_db
    db = get_db()

    for prod_slug, info in data.items():
        db.record_sync(
            local_slug=info.get("local_slug"),
            production_slug=prod_slug,
            function_name=info.get("function"),
        )


def load_all_tracking_data(melee_root: Path) -> dict:
    """Load all tracking data sources into a unified view."""
    data = {
        "completed": {},
        "slug_map": {},
        "synced": {},
    }

    # Completed functions
    data["completed"] = load_completed_functions()

    # Slug map (production mappings)
    data["slug_map"] = load_slug_map()

    # Synced scratches
    synced_file = PRODUCTION_COOKIES_FILE.parent / "synced_scratches.json"
    if synced_file.exists():
        try:
            with open(synced_file, "r") as f:
                data["synced"] = json.load(f)
        except (json.JSONDecodeError, IOError):
            pass

    return data


def get_context_file(source_file: str | None = None, melee_root: Path | None = None) -> Path:
    """Get the context file path for a source file.

    The build system creates per-file .ctx files (e.g., build/GALE01/src/melee/ft/ftcoll.ctx).
    If source_file is provided, we look for the corresponding .ctx file.
    Otherwise we look for a consolidated build/ctx.c (legacy).

    Args:
        source_file: Optional source file path (e.g., "melee/ft/ftcoll.c") to find per-file context.
        melee_root: Optional melee root path (defaults to DEFAULT_MELEE_ROOT).

    Returns:
        Path to the context file.
    """
    root = melee_root or DEFAULT_MELEE_ROOT

    # If source_file provided, look for per-file .ctx
    if source_file:
        # Convert source file path to .ctx path
        # e.g., "melee/ft/ftcoll.c" -> "build/GALE01/src/melee/ft/ftcoll.ctx"
        ctx_relative = source_file.replace(".c", ".ctx").replace(".cpp", ".ctx")
        if not ctx_relative.startswith("src/"):
            ctx_relative = f"src/{ctx_relative}"

        ctx_path = root / "build" / "GALE01" / ctx_relative
        if ctx_path.exists():
            return ctx_path

        # Fall back to main melee if using a worktree
        if root != DEFAULT_MELEE_ROOT:
            main_ctx = DEFAULT_MELEE_ROOT / "build" / "GALE01" / ctx_relative
            if main_ctx.exists():
                return main_ctx

    # Legacy fallback: consolidated ctx.c
    ctx_path = root / "build" / "ctx.c"
    if ctx_path.exists():
        return ctx_path

    # Try main melee legacy ctx.c if using a worktree
    if root != DEFAULT_MELEE_ROOT:
        main_ctx = DEFAULT_MELEE_ROOT / "build" / "ctx.c"
        if main_ctx.exists():
            return main_ctx

    # If source_file was provided but nothing exists, return the expected .ctx path
    # so error message is helpful
    if source_file:
        ctx_relative = source_file.replace(".c", ".ctx").replace(".cpp", ".ctx")
        if not ctx_relative.startswith("src/"):
            ctx_relative = f"src/{ctx_relative}"
        return root / "build" / "GALE01" / ctx_relative

    # Return expected legacy path (may not exist)
    return ctx_path
