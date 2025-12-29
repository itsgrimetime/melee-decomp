"""Common utilities and constants for CLI commands."""

import fcntl
import json
import os
import re
import subprocess
import time
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

# Get agent ID for worktree AND session isolation
# Each agent gets its own worktree (for git) and its own session (for decomp.me)
# This prevents conflicts when parallel agents each claim their own scratches
AGENT_ID = _get_agent_id()
PRODUCTION_COOKIES_FILE = DECOMP_CONFIG_DIR / "production_cookies.json"
LOCAL_API_CACHE_FILE = DECOMP_CONFIG_DIR / "local_api_cache.json"

# Project paths
PROJECT_ROOT = Path(__file__).parent.parent.parent
DEFAULT_MELEE_ROOT = PROJECT_ROOT / "melee"
MELEE_WORKTREES_DIR = PROJECT_ROOT / "melee-worktrees"

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


def _probe_url(url: str, timeout: float = 2.0) -> bool:
    """Check if a decomp.me URL is reachable."""
    import httpx
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
                with open(LOCAL_API_CACHE_FILE, 'w') as f:
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


# Legacy compatibility - these now use auto-detection
LOCAL_DECOMP_ME = os.environ.get("DECOMP_ME_URL", "http://nzxt-discord.local")  # Fallback
_api_base = os.environ.get("DECOMP_API_BASE", "")
DEFAULT_API_URL = _api_base[:-4] if _api_base.endswith("/api") else (_api_base or LOCAL_DECOMP_ME)


def require_api_url(api_url: str) -> None:
    """Validate that API URL is configured and show helpful error if not."""
    if not api_url:
        console.print("[red]Error: DECOMP_API_BASE environment variable is required[/red]")
        console.print("[dim]Set it to your decomp.me instance URL, e.g.:[/dim]")
        console.print(f"[dim]  export DECOMP_API_BASE={LOCAL_DECOMP_ME}[/dim]")
        raise SystemExit(1)


def _validate_worktree_build(worktree_path: Path, max_age_minutes: int = 30) -> bool:
    """Check if a worktree builds successfully with --require-protos.

    Uses a marker file to cache validation results. Only re-validates if:
    - No marker file exists
    - Marker file is older than max_age_minutes
    - Any source files are newer than the marker

    Returns True if build passes, False if it fails.
    """
    import subprocess
    import time

    marker_file = worktree_path / ".build_validated"

    # Check if we have a recent validation marker
    if marker_file.exists():
        marker_age = time.time() - marker_file.stat().st_mtime
        if marker_age < max_age_minutes * 60:
            # Check if any source files changed since validation
            src_dir = worktree_path / "src"
            marker_mtime = marker_file.stat().st_mtime
            needs_revalidation = False

            # Quick check: just look at a few key directories
            for check_dir in [src_dir / "melee" / "lb", src_dir / "melee" / "ft"]:
                if check_dir.exists():
                    for f in check_dir.rglob("*.c"):
                        if f.stat().st_mtime > marker_mtime:
                            needs_revalidation = True
                            break
                    if needs_revalidation:
                        break

            if not needs_revalidation:
                console.print(f"[dim]Build validated {int(marker_age / 60)}m ago[/dim]")
                return True

    console.print(f"[dim]Running build validation (this may take a minute)...[/dim]")

    try:
        # Run configure with --require-protos
        result = subprocess.run(
            ["python", "configure.py", "--require-protos"],
            cwd=worktree_path,
            capture_output=True, text=True,
            timeout=30
        )
        if result.returncode != 0:
            return False

        # Run ninja build (with timeout to avoid hanging)
        result = subprocess.run(
            ["ninja"],
            cwd=worktree_path,
            capture_output=True, text=True,
            timeout=300  # 5 minute timeout
        )

        if result.returncode == 0:
            # Create/update marker file
            marker_file.touch()
            return True
        else:
            # Remove marker if build fails
            if marker_file.exists():
                marker_file.unlink()
            return False

    except (subprocess.TimeoutExpired, subprocess.CalledProcessError, FileNotFoundError):
        return False


def _archive_broken_worktree(worktree_path: Path, agent_id: str) -> None:
    """Archive a broken worktree by renaming it with a -broken suffix."""
    import subprocess
    import time

    timestamp = int(time.time())
    broken_name = f"{agent_id}-broken-{timestamp}"
    broken_path = MELEE_WORKTREES_DIR / broken_name

    console.print(f"[yellow]Archiving broken worktree to: {broken_path}[/yellow]")

    # Remove the worktree from git's tracking first
    try:
        subprocess.run(
            ["git", "worktree", "remove", "--force", str(worktree_path)],
            cwd=DEFAULT_MELEE_ROOT,
            capture_output=True, text=True
        )
    except Exception:
        pass  # May fail if already removed

    # Rename the directory
    if worktree_path.exists():
        worktree_path.rename(broken_path)


def get_agent_melee_root(agent_id: str | None = None, create_if_missing: bool = True, validate_build: bool = True) -> Path:
    """Get the melee worktree path for the current agent.

    Each agent gets its own worktree to avoid conflicts when working in parallel.
    Worktrees are created on-demand at melee-worktrees/{agent_id}/.

    Args:
        agent_id: Optional agent ID override. Uses AGENT_ID if not provided.
        create_if_missing: If True (default), create worktree if it doesn't exist.
        validate_build: If True (default), validate build passes before reusing worktree.

    Returns:
        Path to the agent's melee worktree (or main melee if no agent ID).
    """
    aid = agent_id or AGENT_ID
    if not aid:
        return DEFAULT_MELEE_ROOT

    worktree_path = MELEE_WORKTREES_DIR / aid

    # Check if worktree already exists
    if worktree_path.exists() and (worktree_path / "src").exists():
        # Validate build passes before reusing
        if validate_build:
            console.print(f"[dim]Validating worktree build: {worktree_path}[/dim]")
            if _validate_worktree_build(worktree_path):
                console.print(f"[green]Worktree build OK[/green]")
                return worktree_path
            else:
                console.print(f"[red]Worktree build FAILED - creating fresh worktree[/red]")
                _archive_broken_worktree(worktree_path, aid)
                # Fall through to create new worktree
        else:
            console.print(f"[dim]Using worktree: {worktree_path}[/dim]")
            return worktree_path

    if not create_if_missing:
        return DEFAULT_MELEE_ROOT

    # Create worktree on first use
    return _create_agent_worktree(aid, worktree_path)


def resolve_melee_root(melee_root: Path | None) -> Path:
    """Resolve melee root, using agent worktree if not explicitly specified.

    This function should be called at the start of CLI commands to ensure
    agents work in their assigned worktree by default.

    Args:
        melee_root: Explicitly provided path, or None to auto-detect.

    Returns:
        Path to use for melee operations.
    """
    if melee_root is not None:
        return melee_root
    return get_agent_melee_root()


def get_agent_context_file(agent_id: str | None = None, source_file: str | None = None) -> Path:
    """Get the context file path for the current agent.

    Uses agent's worktree context if available, otherwise falls back to main melee.
    This allows agents to work without requiring a full build in their worktree.

    The build system creates per-file .ctx files (e.g., build/GALE01/src/melee/ft/ftcoll.ctx).
    If source_file is provided, we look for the corresponding .ctx file.
    Otherwise we look for a consolidated build/ctx.c (legacy).

    Args:
        agent_id: Optional agent ID override. Uses AGENT_ID if not provided.
        source_file: Optional source file path (e.g., "melee/ft/ftcoll.c") to find per-file context.

    Returns:
        Path to the context file.
    """
    agent_root = get_agent_melee_root(agent_id)

    # If source_file provided, look for per-file .ctx
    if source_file:
        # Convert source file path to .ctx path
        # e.g., "melee/ft/ftcoll.c" -> "build/GALE01/src/melee/ft/ftcoll.ctx"
        ctx_relative = source_file.replace(".c", ".ctx").replace(".cpp", ".ctx")
        if not ctx_relative.startswith("src/"):
            ctx_relative = f"src/{ctx_relative}"

        # Try agent's worktree first
        agent_ctx = agent_root / "build" / "GALE01" / ctx_relative
        if agent_ctx.exists():
            return agent_ctx

        # Fall back to main melee
        main_ctx = DEFAULT_MELEE_ROOT / "build" / "GALE01" / ctx_relative
        if main_ctx.exists():
            return main_ctx

    # Fall back to consolidated ctx.c (legacy behavior)
    agent_ctx = agent_root / "build" / "ctx.c"
    if agent_ctx.exists():
        return agent_ctx

    main_ctx = DEFAULT_MELEE_ROOT / "build" / "ctx.c"
    if main_ctx.exists():
        return main_ctx

    # If source_file was provided but not found, return the expected path
    # so error message is helpful
    if source_file:
        ctx_relative = source_file.replace(".c", ".ctx").replace(".cpp", ".ctx")
        if not ctx_relative.startswith("src/"):
            ctx_relative = f"src/{ctx_relative}"
        return agent_root / "build" / "GALE01" / ctx_relative

    # Return agent path so error message shows what needs to be built
    return agent_ctx


def _create_agent_worktree(agent_id: str, worktree_path: Path) -> Path:
    """Create a new worktree for an agent.

    Creates a new branch and worktree for the agent to work in isolation.
    Also symlinks orig/, copies ctx.c, and runs build to generate report.json.
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

    # Get current commit SHA for reference
    try:
        result = subprocess.run(
            ["git", "rev-parse", "--short", "HEAD"],
            cwd=DEFAULT_MELEE_ROOT,
            capture_output=True, text=True, check=True
        )
        base_commit = result.stdout.strip()
    except subprocess.CalledProcessError:
        base_commit = "unknown"

    # Get remote URL for repo info
    try:
        result = subprocess.run(
            ["git", "remote", "get-url", "origin"],
            cwd=DEFAULT_MELEE_ROOT,
            capture_output=True, text=True, check=True
        )
        remote_url = result.stdout.strip()
    except subprocess.CalledProcessError:
        remote_url = str(DEFAULT_MELEE_ROOT)

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

        # Print worktree creation info with base details
        console.print(f"\n[bold cyan]WORKTREE CREATED[/bold cyan]")
        console.print(f"  [dim]Path:[/dim]   {worktree_path}")
        console.print(f"  [dim]Branch:[/dim] {branch_name}")
        console.print(f"  [dim]Base:[/dim]   {base_branch} @ {base_commit}")
        console.print(f"  [dim]Repo:[/dim]   {remote_url}")

        # Run build to generate report.json (needed for extract list)
        configure_py = worktree_path / "configure.py"
        if configure_py.exists():
            console.print(f"\n[dim]Running initial build to generate report.json...[/dim]")
            try:
                # Run configure.py
                subprocess.run(
                    ["python", "configure.py"],
                    cwd=worktree_path,
                    capture_output=True, text=True, check=True
                )
                # Run ninja to build report.json
                result = subprocess.run(
                    ["ninja", "build/GALE01/report.json"],
                    cwd=worktree_path,
                    capture_output=True, text=True,
                    timeout=300,  # 5 minute timeout
                )
                if result.returncode == 0:
                    console.print(f"[green]Build complete - report.json generated[/green]")
                else:
                    console.print(f"[yellow]Build had issues: {result.stderr[:200]}[/yellow]")
            except subprocess.TimeoutExpired:
                console.print(f"[yellow]Build timed out - run 'ninja' manually in worktree[/yellow]")
            except subprocess.CalledProcessError as e:
                console.print(f"[yellow]Build setup failed: {e.stderr[:200] if e.stderr else str(e)}[/yellow]")
            except FileNotFoundError:
                console.print(f"[yellow]ninja not found - run 'ninja' manually in worktree[/yellow]")

        console.print(f"\n[yellow]Run all git commands in the worktree, not in melee/[/yellow]")

        return worktree_path

    except subprocess.CalledProcessError as e:
        console.print(f"[yellow]Warning: Could not create worktree: {e.stderr}[/yellow]")
        console.print(f"[yellow]Falling back to shared melee directory[/yellow]")
        return DEFAULT_MELEE_ROOT


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
            result[row['function_name']] = {
                'match_percent': row['match_percent'] or 0,
                'scratch_slug': row['local_scratch_slug'],
                'production_slug': row['production_scratch_slug'],
                'committed': bool(row['is_committed']),
                'branch': row['branch'],
                'pr_url': row['pr_url'],
                'pr_number': row['pr_number'],
                'pr_state': row['pr_state'],
                'notes': row['notes'],
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
            match_percent=info.get('match_percent', 0),
            local_scratch_slug=info.get('scratch_slug'),
            production_scratch_slug=info.get('production_slug'),
            is_committed=info.get('committed', False),
            branch=info.get('branch'),
            pr_url=info.get('pr_url'),
            pr_number=info.get('pr_number'),
            pr_state=info.get('pr_state'),
            notes=info.get('notes'),
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
            result[row['production_slug']] = {
                'local_slug': row['local_slug'],
                'function': row['function_name'],
                'match_percent': row['match_percent'] or 0,
                'synced_at': row['synced_at'],
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
            local_slug=info.get('local_slug'),
            production_slug=prod_slug,
            function_name=info.get('function'),
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


# =============================================================================
# Operation Tracking (Duplicate Detection)
# =============================================================================

# Track recent operations to detect duplicates
_RECENT_OPS_FILE = DECOMP_CONFIG_DIR / "recent_operations.json"
_OP_CACHE_TTL = 60  # Seconds before an operation "expires"


def _load_recent_ops() -> dict:
    """Load recent operations cache."""
    if not _RECENT_OPS_FILE.exists():
        return {"operations": []}
    try:
        with open(_RECENT_OPS_FILE, 'r') as f:
            return json.load(f)
    except (json.JSONDecodeError, IOError):
        return {"operations": []}


def _save_recent_ops(data: dict) -> None:
    """Save recent operations cache."""
    _RECENT_OPS_FILE.parent.mkdir(parents=True, exist_ok=True)
    with open(_RECENT_OPS_FILE, 'w') as f:
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
    import time

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


# =============================================================================
# Database Integration (Non-Blocking Dual-Write)
# =============================================================================

def get_state_db():
    """Get the state database instance.

    Returns None if database module is not available or fails to initialize.
    This allows graceful degradation when database is not set up.
    """
    try:
        from src.db import get_db
        return get_db()
    except Exception as e:
        # Log but don't fail - database is optional during transition
        console.print(f"[dim]Note: State database unavailable: {e}[/dim]")
        return None


def db_log_audit(
    entity_type: str,
    entity_id: str,
    action: str,
    agent_id: str | None = None,
    old_value: dict | None = None,
    new_value: dict | None = None,
    metadata: dict | None = None,
) -> bool:
    """Log an audit entry to the state database (non-blocking).

    Returns True if logged successfully, False otherwise.
    Failures are silent to avoid disrupting normal operations.
    """
    db = get_state_db()
    if db is None:
        return False

    try:
        db.log_audit(
            entity_type=entity_type,
            entity_id=entity_id,
            action=action,
            agent_id=agent_id or AGENT_ID,
            old_value=old_value,
            new_value=new_value,
            metadata=metadata,
        )
        return True
    except Exception:
        return False


def db_upsert_function(function_name: str, **fields) -> bool:
    """Update function in state database (non-blocking).

    Returns True if updated successfully, False otherwise.
    """
    db = get_state_db()
    if db is None:
        return False

    try:
        db.upsert_function(function_name, agent_id=AGENT_ID, **fields)
        return True
    except Exception:
        return False


def db_add_claim(function_name: str, agent_id: str | None = None) -> tuple[bool, str | None]:
    """Add claim in state database (non-blocking).

    Returns (success, error_message) tuple.
    """
    db = get_state_db()
    if db is None:
        return True, None  # Pretend success when DB unavailable

    try:
        return db.add_claim(function_name, agent_id or AGENT_ID)
    except Exception as e:
        return True, None  # Don't block on DB errors


def db_release_claim(function_name: str, agent_id: str | None = None) -> bool:
    """Release claim in state database (non-blocking).

    Returns True if released successfully.
    """
    db = get_state_db()
    if db is None:
        return True  # Pretend success when DB unavailable

    try:
        return db.release_claim(function_name, agent_id)
    except Exception:
        return True  # Don't block on DB errors


def db_upsert_scratch(slug: str, instance: str, base_url: str, **fields) -> bool:
    """Update scratch in state database (non-blocking).

    Returns True if updated successfully.
    """
    db = get_state_db()
    if db is None:
        return False

    try:
        db.upsert_scratch(slug, instance, base_url, agent_id=AGENT_ID, **fields)
        return True
    except Exception:
        return False


def db_record_match_score(scratch_slug: str, score: int, max_score: int) -> bool:
    """Record match score in state database (non-blocking).

    Returns True if recorded successfully.
    """
    db = get_state_db()
    if db is None:
        return False

    try:
        db.record_match_score(scratch_slug, score, max_score)
        return True
    except Exception:
        return False


def db_record_sync(local_slug: str, production_slug: str, function_name: str | None = None) -> bool:
    """Record sync in state database (non-blocking).

    Returns True if recorded successfully.
    """
    db = get_state_db()
    if db is None:
        return False

    try:
        db.record_sync(local_slug, production_slug, function_name)
        return True
    except Exception:
        return False


def db_upsert_agent(agent_id: str, worktree_path: str | None = None, branch_name: str | None = None) -> bool:
    """Update agent in state database (non-blocking).

    Returns True if updated successfully.
    """
    db = get_state_db()
    if db is None:
        return False

    try:
        db.upsert_agent(agent_id, worktree_path, branch_name)
        return True
    except Exception:
        return False
