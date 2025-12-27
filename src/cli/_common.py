"""Common utilities and constants for CLI commands."""

import fcntl
import json
import os
import re
import subprocess
from pathlib import Path
from typing import Annotated, Optional

import typer
from rich.console import Console
from rich.table import Table

from src.client.api import _get_agent_id

# Console for rich output
console = Console()

# Paths
DECOMP_CONFIG_DIR = Path.home() / ".config" / "decomp-me"
DECOMP_CONFIG_DIR.mkdir(parents=True, exist_ok=True)

# Get agent ID for session isolation - use same logic as api.py
AGENT_ID = _get_agent_id()
_cookies_suffix = f"_{AGENT_ID}" if AGENT_ID else ""

DECOMP_COOKIES_FILE = os.environ.get(
    "DECOMP_COOKIES_FILE",
    str(DECOMP_CONFIG_DIR / f"cookies{_cookies_suffix}.json")
)
DECOMP_COMPLETED_FILE = os.environ.get(
    "DECOMP_COMPLETED_FILE",
    str(DECOMP_CONFIG_DIR / "completed_functions.json")
)
PRODUCTION_COOKIES_FILE = DECOMP_CONFIG_DIR / "production_cookies.json"

# Project paths
PROJECT_ROOT = Path(__file__).parent.parent.parent
DEFAULT_MELEE_ROOT = PROJECT_ROOT / "melee"
MELEE_WORKTREES_DIR = PROJECT_ROOT / "melee-worktrees"
SLUG_MAP_FILE = PROJECT_ROOT / "config" / "scratches_slug_map.json"

# decomp.me instances
# Local: http://nzxt-discord.local (home network)
# Remote: http://10.200.0.1 (via WireGuard VPN)
LOCAL_DECOMP_ME = os.environ.get("DECOMP_ME_URL", "http://nzxt-discord.local")
PRODUCTION_DECOMP_ME = "https://decomp.me"

# API URL - derived from LOCAL_DECOMP_ME or DECOMP_API_BASE env var
_api_base = os.environ.get("DECOMP_API_BASE", "")
DEFAULT_API_URL = _api_base[:-4] if _api_base.endswith("/api") else (_api_base or LOCAL_DECOMP_ME)


def require_api_url(api_url: str) -> None:
    """Validate that API URL is configured and show helpful error if not."""
    if not api_url:
        console.print("[red]Error: DECOMP_API_BASE environment variable is required[/red]")
        console.print("[dim]Set it to your decomp.me instance URL, e.g.:[/dim]")
        console.print(f"[dim]  export DECOMP_API_BASE={LOCAL_DECOMP_ME}[/dim]")
        raise SystemExit(1)


def get_agent_melee_root(agent_id: str | None = None) -> Path:
    """Get the melee worktree path for the current agent.

    Each agent gets its own worktree to avoid conflicts when working in parallel.
    Worktrees are created on-demand at melee-worktrees/{agent_id}/.

    Args:
        agent_id: Optional agent ID override. Uses AGENT_ID if not provided.

    Returns:
        Path to the agent's melee worktree (or main melee if no agent ID).
    """
    aid = agent_id or AGENT_ID
    if not aid:
        return DEFAULT_MELEE_ROOT

    worktree_path = MELEE_WORKTREES_DIR / aid

    # Check if worktree already exists
    if worktree_path.exists() and (worktree_path / "src").exists():
        console.print(f"[dim]Using worktree: {worktree_path}[/dim]")
        return worktree_path

    # Create worktree on first use
    return _create_agent_worktree(aid, worktree_path)


def get_agent_context_file(agent_id: str | None = None) -> Path:
    """Get the context file path for the current agent.

    Uses agent's worktree context if available, otherwise falls back to main melee.
    This allows agents to work without requiring a full build in their worktree.

    Args:
        agent_id: Optional agent ID override. Uses AGENT_ID if not provided.

    Returns:
        Path to the context file (build/ctx.c).
    """
    agent_root = get_agent_melee_root(agent_id)
    agent_ctx = agent_root / "build" / "ctx.c"

    # If agent's worktree has a built context, use it
    if agent_ctx.exists():
        return agent_ctx

    # Fall back to main melee's context file
    main_ctx = DEFAULT_MELEE_ROOT / "build" / "ctx.c"
    if main_ctx.exists():
        return main_ctx

    # Return agent path so error message shows what needs to be built
    return agent_ctx


def _create_agent_worktree(agent_id: str, worktree_path: Path) -> Path:
    """Create a new worktree for an agent.

    Creates a new branch and worktree for the agent to work in isolation.
    Also symlinks orig/ and runs configure.py + ninja to set up the build.
    """
    import subprocess

    MELEE_WORKTREES_DIR.mkdir(parents=True, exist_ok=True)

    # Get current branch from main melee
    try:
        result = subprocess.run(
            ["git", "rev-parse", "--abbrev-ref", "HEAD"],
            cwd=DEFAULT_MELEE_ROOT,
            capture_output=True, text=True, check=True
        )
        base_branch = result.stdout.strip()
    except subprocess.CalledProcessError:
        base_branch = "master"

    # Create branch name for this agent
    branch_name = f"agent/{agent_id}"

    # Check if branch already exists
    result = subprocess.run(
        ["git", "branch", "--list", branch_name],
        cwd=DEFAULT_MELEE_ROOT,
        capture_output=True, text=True
    )
    branch_exists = bool(result.stdout.strip())

    try:
        if branch_exists:
            # Worktree with existing branch
            subprocess.run(
                ["git", "worktree", "add", str(worktree_path), branch_name],
                cwd=DEFAULT_MELEE_ROOT,
                capture_output=True, text=True, check=True
            )
        else:
            # Create new branch from current HEAD
            subprocess.run(
                ["git", "worktree", "add", "-b", branch_name, str(worktree_path)],
                cwd=DEFAULT_MELEE_ROOT,
                capture_output=True, text=True, check=True
            )

        # Symlink orig/ directory (contains original game files needed for build)
        # Remove the git-checked-out orig/ (just has .gitkeep) and replace with symlink
        orig_src = DEFAULT_MELEE_ROOT / "orig"
        orig_dst = worktree_path / "orig"
        if orig_src.exists():
            import shutil
            if orig_dst.exists() and not orig_dst.is_symlink():
                shutil.rmtree(orig_dst)
            if not orig_dst.exists():
                orig_dst.symlink_to(orig_src.resolve())

        # Copy ctx.c from main melee (it's the same - just preprocessed headers)
        main_ctx = DEFAULT_MELEE_ROOT / "build" / "ctx.c"
        if main_ctx.exists():
            (worktree_path / "build").mkdir(exist_ok=True)
            worktree_ctx = worktree_path / "build" / "ctx.c"
            import shutil
            shutil.copy2(main_ctx, worktree_ctx)

        # Print clear instructions about the worktree
        console.print(f"[bold cyan]WORKTREE CREATED:[/bold cyan] {worktree_path}")
        console.print(f"[bold cyan]BRANCH:[/bold cyan] {branch_name}")
        console.print(f"[yellow]Run all git commands in the worktree, not in melee/[/yellow]")

        return worktree_path

    except subprocess.CalledProcessError as e:
        console.print(f"[yellow]Warning: Could not create worktree: {e.stderr}[/yellow]")
        console.print(f"[yellow]Falling back to shared melee directory[/yellow]")
        return DEFAULT_MELEE_ROOT


def load_completed_functions() -> dict:
    """Load completed functions tracking file with shared lock."""
    completed_path = Path(DECOMP_COMPLETED_FILE)
    if completed_path.exists():
        try:
            with open(completed_path, 'r') as f:
                fcntl.flock(f.fileno(), fcntl.LOCK_SH)
                try:
                    return json.load(f)
                finally:
                    fcntl.flock(f.fileno(), fcntl.LOCK_UN)
        except (json.JSONDecodeError, IOError):
            pass
    return {}


def save_completed_functions(data: dict) -> None:
    """Save completed functions tracking file with exclusive lock.

    Uses atomic write pattern: lock, read current, merge, write.
    This prevents race conditions when multiple agents write simultaneously.
    """
    completed_path = Path(DECOMP_COMPLETED_FILE)
    completed_path.parent.mkdir(parents=True, exist_ok=True)

    # Use a lock file for atomic read-modify-write
    lock_path = completed_path.with_suffix('.lock')

    with open(lock_path, 'w') as lock_file:
        # Acquire exclusive lock (blocks until available)
        fcntl.flock(lock_file.fileno(), fcntl.LOCK_EX)
        try:
            # Read current data (another process may have written)
            current_data = {}
            if completed_path.exists():
                try:
                    with open(completed_path, 'r') as f:
                        current_data = json.load(f)
                except (json.JSONDecodeError, IOError):
                    pass

            # Merge: incoming data takes precedence, but preserve entries not in incoming
            merged_data = {**current_data, **data}

            # Write atomically using temp file + rename
            temp_path = completed_path.with_suffix('.tmp')
            with open(temp_path, 'w') as f:
                json.dump(merged_data, f, indent=2)
            temp_path.rename(completed_path)
        finally:
            fcntl.flock(lock_file.fileno(), fcntl.LOCK_UN)


def load_slug_map() -> dict:
    """Load local->production slug mapping."""
    if SLUG_MAP_FILE.exists():
        try:
            with open(SLUG_MAP_FILE, 'r') as f:
                return json.load(f)
        except (json.JSONDecodeError, IOError):
            pass
    return {}


def save_slug_map(data: dict) -> None:
    """Save local->production slug mapping."""
    SLUG_MAP_FILE.parent.mkdir(parents=True, exist_ok=True)
    with open(SLUG_MAP_FILE, 'w') as f:
        json.dump(data, f, indent=2)


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
            with open(synced_file, 'r') as f:
                data["synced"] = json.load(f)
        except (json.JSONDecodeError, IOError):
            pass

    return data


def categorize_functions(data: dict, check_pr_status: bool = False) -> dict:
    """Categorize all tracked functions by their status.

    Categories (for 95%+ matches):
    - merged: PR merged (done!)
    - in_review: Has PR that's still open
    - committed: Committed locally but no PR
    - ready: Synced to production, ready for PR
    - lost_high_match: 95%+ but not synced

    For <95% matches:
    - work_in_progress: Still being worked on
    """
    categories = {
        "merged": [],             # PR merged
        "in_review": [],          # PR open
        "committed": [],          # Committed but no PR
        "ready": [],              # Synced, ready for PR
        "lost_high_match": [],    # 95%+ but not synced
        "work_in_progress": [],   # <95% match
    }

    # Build indexes
    prod_funcs = {v.get("function"): k for k, v in data["slug_map"].items()}
    synced_local_slugs = set(data["synced"].keys())

    # Cache PR statuses if checking
    pr_status_cache = {}

    for func, info in data["completed"].items():
        # Skip functions already in upstream (not our work)
        if info.get("already_in_upstream"):
            continue

        pct = info.get("match_percent", 0)
        slug = info.get("scratch_slug", "")
        pr_url = info.get("pr_url", "")
        is_committed = info.get("committed", False)
        branch = info.get("branch", "")

        # Determine status
        synced_to_prod = func in prod_funcs or slug in synced_local_slugs

        entry = {
            "function": func,
            "match_percent": pct,
            "local_slug": slug,
            "production_slug": prod_funcs.get(func, ""),
            "committed": is_committed,
            "branch": branch,
            "pr_url": pr_url,
            "notes": info.get("notes", ""),
        }

        if pct >= 95:
            # Check PR status if we have a PR URL
            if pr_url:
                pr_state = None
                if check_pr_status:
                    if pr_url not in pr_status_cache:
                        repo, pr_num = extract_pr_info(pr_url)
                        if repo and pr_num:
                            pr_status_cache[pr_url] = get_pr_status_from_gh(repo, pr_num)
                        else:
                            pr_status_cache[pr_url] = {}
                    pr_state = pr_status_cache.get(pr_url, {}).get("state")

                entry["pr_state"] = pr_state

                if pr_state == "MERGED":
                    categories["merged"].append(entry)
                elif pr_state == "CLOSED":
                    # PR was closed without merge, treat as committed
                    categories["committed"].append(entry)
                else:
                    # PR is open or we didn't check
                    categories["in_review"].append(entry)
            elif is_committed:
                categories["committed"].append(entry)
            elif synced_to_prod:
                categories["ready"].append(entry)
            else:
                categories["lost_high_match"].append(entry)
        else:
            categories["work_in_progress"].append(entry)

    # Sort each category by match percentage
    for cat in categories:
        categories[cat].sort(key=lambda x: -x["match_percent"])

    return categories


def extract_pr_info(pr_url: str) -> tuple[str, int]:
    """Extract repo and PR number from URL.

    Returns: (repo, pr_number) e.g. ("doldecomp/melee", 123)
    """
    match = re.match(r'https?://github\.com/([^/]+/[^/]+)/pull/(\d+)', pr_url)
    if match:
        return match.group(1), int(match.group(2))
    return "", 0


def get_pr_status_from_gh(repo: str, pr_number: int) -> dict:
    """Get PR status using gh CLI."""
    try:
        result = subprocess.run(
            ["gh", "pr", "view", str(pr_number), "--repo", repo, "--json",
             "state,isDraft,title,mergeable,reviewDecision"],
            capture_output=True, text=True, check=True
        )
        return json.loads(result.stdout)
    except (subprocess.CalledProcessError, json.JSONDecodeError, FileNotFoundError):
        return {}


# =============================================================================
# Match History Tracking
# =============================================================================

# Match history file (per-agent for isolation)
_history_suffix = f"_{AGENT_ID}" if AGENT_ID else ""
MATCH_HISTORY_FILE = DECOMP_CONFIG_DIR / f"match_history{_history_suffix}.json"


def load_match_history() -> dict:
    """Load match history for all scratches.

    Returns dict of slug -> list of {score, max_score, match_pct, timestamp}
    """
    if not MATCH_HISTORY_FILE.exists():
        return {}
    try:
        with open(MATCH_HISTORY_FILE, 'r') as f:
            return json.load(f)
    except (json.JSONDecodeError, IOError):
        return {}


def save_match_history(data: dict) -> None:
    """Save match history."""
    MATCH_HISTORY_FILE.parent.mkdir(parents=True, exist_ok=True)
    with open(MATCH_HISTORY_FILE, 'w') as f:
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
    import time

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
