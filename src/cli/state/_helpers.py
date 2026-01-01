"""Shared helper functions for state commands."""

import asyncio
import time
from datetime import datetime

from rich.console import Console

console = Console()

# Staleness thresholds (in hours)
STALE_THRESHOLD_LOCAL = 1.0
STALE_THRESHOLD_PRODUCTION = 24.0
STALE_THRESHOLD_GIT = 24.0
STALE_THRESHOLD_PR = 1.0


def format_age(timestamp: float | None) -> str:
    """Format a timestamp as human-readable age."""
    if timestamp is None:
        return "never"
    age_seconds = time.time() - timestamp
    if age_seconds < 60:
        return f"{int(age_seconds)}s ago"
    elif age_seconds < 3600:
        return f"{int(age_seconds / 60)}m ago"
    elif age_seconds < 86400:
        return f"{age_seconds / 3600:.1f}h ago"
    else:
        return f"{age_seconds / 86400:.1f}d ago"


def format_datetime(timestamp: float | None) -> str:
    """Format a timestamp as datetime string."""
    if timestamp is None:
        return "-"
    return datetime.fromtimestamp(timestamp).strftime("%Y-%m-%d %H:%M")


def find_best_local_scratch(function_name: str) -> tuple[str | None, float]:
    """Search local decomp.me for scratches matching function name.

    Returns (slug, match_percent) of the best scratch, or (None, 0) if not found.
    """
    from src.client import DecompMeAPIClient
    from .._common import get_local_api_url

    async def search():
        api_url = get_local_api_url()
        async with DecompMeAPIClient(api_url) as client:
            # Search for scratches with this function name, ordered by score (lower = better)
            scratches = await client.list_scratches(
                search=function_name,
                platform="gc_wii",
                ordering="score",  # Lowest diff score first = best match
                page_size=10,
            )
            # Filter to exact name matches and find best
            # Note: score is diff score (lower = better), so match% = (1 - score/max_score) * 100
            best_slug = None
            best_pct = 0.0
            for s in scratches:
                if s.name == function_name:
                    if s.max_score > 0:
                        pct = (1 - s.score / s.max_score) * 100
                    else:
                        pct = 0.0
                    if pct > best_pct:
                        best_pct = pct
                        best_slug = s.slug
            return best_slug, best_pct

    try:
        return asyncio.run(search())
    except Exception:
        return None, 0.0
