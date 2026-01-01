"""API URL detection and management utilities.

Provides auto-detection of decomp.me server URLs with caching
to avoid repeated probing.
"""

import json
import os
import time
from pathlib import Path

import httpx
import typer
from rich.console import Console

# Config directory for caching
DECOMP_CONFIG_DIR = Path.home() / ".config" / "decomp-me"
DECOMP_CONFIG_DIR.mkdir(parents=True, exist_ok=True)

LOCAL_API_CACHE_FILE = DECOMP_CONFIG_DIR / "local_api_cache.json"

# decomp.me instances
PRODUCTION_DECOMP_ME = "https://decomp.me"

# Candidate local decomp.me URLs to try (in order of preference)
# Can be overridden via .env file with LOCAL_DECOMP_CANDIDATES (comma-separated)
DEFAULT_LOCAL_CANDIDATES = [
    "http://nzxt-discord.local",  # Home network hostname
    "http://10.200.0.1",          # WireGuard VPN
    "http://localhost:8000",      # Local dev server
]

_env_candidates = os.environ.get("LOCAL_DECOMP_CANDIDATES", "")
LOCAL_DECOMP_CANDIDATES = (
    [url.strip() for url in _env_candidates.split(",") if url.strip()]
    if _env_candidates else DEFAULT_LOCAL_CANDIDATES
)

# Cache for detected local URL (valid for 1 hour)
LOCAL_API_CACHE_TTL = 3600

console = Console()


def _probe_url(url: str, timeout: float = 2.0) -> bool:
    """Check if a decomp.me URL is reachable."""
    try:
        # Try to hit the API root
        resp = httpx.get(f"{url}/api/", timeout=timeout)
        return resp.status_code == 200
    except Exception:
        return False


def detect_local_api_url(force_probe: bool = False) -> str | None:
    """Auto-detect the local decomp.me API URL.

    Tries candidate URLs in order and returns the first one that responds.
    Results are cached for 1 hour to avoid repeated probing.

    Args:
        force_probe: If True, ignore cache and probe all candidates

    Returns:
        Working URL or None if none found
    """
    # Check environment variables first (explicit config takes precedence)
    env_url = os.environ.get("DECOMP_API_BASE") or os.environ.get("DECOMP_ME_URL")
    if env_url:
        # Strip /api suffix if present
        return env_url[:-4] if env_url.endswith("/api") else env_url

    # Check cache
    if not force_probe and LOCAL_API_CACHE_FILE.exists():
        try:
            with open(LOCAL_API_CACHE_FILE) as f:
                cache = json.load(f)
            cached_url = cache.get("url")
            cached_at = cache.get("cached_at", 0)
            if cached_url and (time.time() - cached_at) < LOCAL_API_CACHE_TTL:
                # Verify cached URL still works
                if _probe_url(cached_url, timeout=1.0):
                    return cached_url
        except (json.JSONDecodeError, IOError):
            pass

    # Probe candidates
    for url in LOCAL_DECOMP_CANDIDATES:
        if _probe_url(url):
            # Cache the result
            try:
                with open(LOCAL_API_CACHE_FILE, "w") as f:
                    json.dump({"url": url, "cached_at": time.time()}, f)
            except IOError:
                pass
            return url

    return None


def get_local_api_url() -> str:
    """Get the local decomp.me API URL, auto-detecting if needed.

    Returns:
        URL string

    Raises:
        typer.Exit if no local server found
    """
    url = detect_local_api_url()
    if not url:
        console.print("[red]Error: Could not find local decomp.me server[/red]")
        console.print(f"[dim]Tried: {', '.join(LOCAL_DECOMP_CANDIDATES)}[/dim]")
        console.print("[dim]Set DECOMP_API_BASE or DECOMP_ME_URL environment variable,[/dim]")
        console.print("[dim]or add LOCAL_DECOMP_CANDIDATES to your .env file[/dim]")
        raise typer.Exit(1)
    return url
