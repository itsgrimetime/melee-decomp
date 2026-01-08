"""Common utilities and constants for CLI commands."""

import json
import os
import re
import subprocess
import time
from pathlib import Path

import typer
from rich.console import Console

from src.client.api import _get_agent_id

# Console for rich output
console = Console()

# Paths
DECOMP_CONFIG_DIR = Path.home() / ".config" / "decomp-me"
DECOMP_CONFIG_DIR.mkdir(parents=True, exist_ok=True)

# Central location for the base DOL file (gitignored, must be provided by user)
BASE_DOL_PATH = DECOMP_CONFIG_DIR / "orig" / "GALE01" / "main.dol"


def get_base_dol_path() -> Path | None:
    """Get the path to the base DOL file, or None if not configured."""
    if BASE_DOL_PATH.exists():
        return BASE_DOL_PATH
    return None


def ensure_dol_in_worktree(worktree_path: Path) -> bool:
    """Ensure the base DOL exists in a worktree. Returns True if successful."""
    import shutil

    dol_dst = worktree_path / "orig" / "GALE01" / "sys" / "main.dol"
    if dol_dst.exists():
        return True

    # Try central location first
    if BASE_DOL_PATH.exists():
        dol_dst.parent.mkdir(parents=True, exist_ok=True)
        dol_dst.symlink_to(BASE_DOL_PATH)
        return True

    # Fallback: search existing worktrees
    if MELEE_WORKTREES_DIR.exists():
        for wt in MELEE_WORKTREES_DIR.iterdir():
            dol_src = wt / "orig" / "GALE01" / "sys" / "main.dol"
            if dol_src.exists() and not dol_src.is_symlink():
                dol_dst.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(dol_src, dol_dst)
                return True

    return False


# Get agent ID for worktree AND session isolation
AGENT_ID = _get_agent_id()
PRODUCTION_COOKIES_FILE = DECOMP_CONFIG_DIR / "production_cookies.json"
LOCAL_API_CACHE_FILE = DECOMP_CONFIG_DIR / "local_api_cache.json"

# Project paths
PROJECT_ROOT = Path(__file__).parent.parent.parent
DEFAULT_MELEE_ROOT = PROJECT_ROOT / "melee"
MELEE_WORKTREES_DIR = PROJECT_ROOT / "melee-worktrees"

# decomp.me instances
PRODUCTION_DECOMP_ME = "https://decomp.me"

# Compiler version mapping: GC SDK version -> decomp.me compiler ID
GC_TO_DECOMP_COMPILER = {
    "GC/1.0": "mwcc_233_144",
    "GC/1.1": "mwcc_233_159",
    "GC/1.1p1": "mwcc_233_159p1",
    "GC/1.2.5": "mwcc_233_163",
    "GC/1.2.5n": "mwcc_233_163n",
    "GC/1.3": "mwcc_242_53",
    "GC/1.3.2": "mwcc_242_81",
    "GC/1.3.2r": "mwcc_242_81r",
    "GC/2.0": "mwcc_247_92",
    "GC/2.0p1": "mwcc_247_92p1",
    "GC/2.5": "mwcc_247_105",
    "GC/2.6": "mwcc_247_107",
    "GC/2.7": "mwcc_247_108",
}

DEFAULT_DECOMP_COMPILER = "mwcc_247_92"


def get_compiler_for_source(source_file: str, melee_root: Path) -> str:
    """Get the decomp.me compiler ID for a source file by parsing build.ninja."""
    build_ninja = melee_root / "build.ninja"
    if not build_ninja.exists():
        console.print(f"[yellow]build.ninja not found, using default compiler[/yellow]")
        return DEFAULT_DECOMP_COMPILER

    if source_file.startswith("src/"):
        source_rel = source_file
    else:
        source_rel = f"src/{source_file}"

    try:
        content = build_ninja.read_text()
        lines = content.split('\n')

        comment_path = source_rel
        if comment_path.startswith("src/"):
            comment_path = comment_path[4:]

        in_target = False
        for line in lines:
            if line.startswith(f'# {comment_path}:'):
                in_target = True
                continue

            if in_target:
                if line.startswith('  mw_version = '):
                    mw_version = line.split('=', 1)[1].strip()
                    if mw_version in GC_TO_DECOMP_COMPILER:
                        return GC_TO_DECOMP_COMPILER[mw_version]
                    else:
                        console.print(f"[yellow]Unknown compiler version {mw_version}, using default[/yellow]")
                        return DEFAULT_DECOMP_COMPILER
                elif line.startswith('# ') and ':' in line:
                    break

    except Exception as e:
        console.print(f"[yellow]Error parsing build.ninja: {e}[/yellow]")

    return DEFAULT_DECOMP_COMPILER


# =============================================================================
# Re-exports from extracted modules (for backward compatibility)
# =============================================================================

# From api_helpers.py
from .api_helpers import (
    _probe_url,
    detect_local_api_url,
    get_local_api_url,
    LOCAL_DECOMP_CANDIDATES,
    LOCAL_API_CACHE_TTL,
)

# From storage.py
from .storage import (
    load_completed_functions,
    save_completed_functions,
    load_slug_map,
    save_slug_map,
    load_all_tracking_data,
    get_context_file,
)

# From tracking.py
from .tracking import (
    load_match_history,
    save_match_history,
    record_match_score,
    get_match_history,
    format_match_history,
)

# From worktree_utils.py
from .worktree_utils import (
    get_subdirectory_key,
    get_worktree_name_for_subdirectory,
    get_subdirectory_worktree_path,
    get_subdirectory_worktree,
    get_worktree_for_file,
    get_agent_melee_root,
    get_agent_context_file,
    resolve_melee_root,
    get_source_file_from_claim,
    db_upsert_subdirectory,
    db_lock_subdirectory,
    db_unlock_subdirectory,
    db_get_subdirectory_lock,
)


# =============================================================================
# PR Helpers
# =============================================================================

DEFAULT_PR_REPO = "doldecomp/melee"


def extract_pr_info(pr_input: str, default_repo: str = DEFAULT_PR_REPO) -> tuple[str, int]:
    """Extract repo and PR number from URL or PR number."""
    pr_input = pr_input.strip()

    match = re.match(r'https?://github\.com/([^/]+/[^/]+)/pull/(\d+)', pr_input)
    if match:
        return match.group(1), int(match.group(2))

    match = re.match(r'([^/]+/[^#]+)#(\d+)', pr_input)
    if match:
        return match.group(1), int(match.group(2))

    if pr_input.isdigit():
        return default_repo, int(pr_input)

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


def categorize_functions(data: dict, check_pr_status: bool = False) -> dict:
    """Categorize all tracked functions by their status."""
    categories = {
        "merged": [],
        "in_review": [],
        "committed": [],
        "ready": [],
        "lost_high_match": [],
        "work_in_progress": [],
    }

    prod_funcs = {v.get("function"): k for k, v in data["slug_map"].items()}
    synced_local_slugs = set(data["synced"].keys())
    pr_status_cache = {}

    for func, info in data["completed"].items():
        if info.get("already_in_upstream"):
            continue

        pct = info.get("match_percent", 0)
        slug = info.get("scratch_slug", "")
        pr_url = info.get("pr_url", "")
        is_committed = info.get("committed", False)
        branch = info.get("branch", "")

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
                    categories["committed"].append(entry)
                else:
                    categories["in_review"].append(entry)
            elif is_committed:
                categories["committed"].append(entry)
            elif synced_to_prod:
                categories["ready"].append(entry)
            else:
                categories["lost_high_match"].append(entry)
        else:
            categories["work_in_progress"].append(entry)

    for cat in categories:
        categories[cat].sort(key=lambda x: -x["match_percent"])

    return categories


# =============================================================================
# Database Integration (Non-Blocking Wrappers)
# =============================================================================

def get_state_db():
    """Get the state database instance."""
    try:
        from src.db import get_db
        return get_db()
    except Exception as e:
        console.print(f"[dim]Note: State database unavailable: {e}[/dim]")
        return None


def db_upsert_function(function_name: str, **fields) -> bool:
    """Update function in state database (non-blocking)."""
    db = get_state_db()
    if db is None:
        return False

    try:
        db.upsert_function(function_name, agent_id=AGENT_ID, **fields)
        return True
    except Exception:
        return False


def db_add_claim(function_name: str, agent_id: str | None = None) -> tuple[bool, str | None]:
    """Add claim in state database (non-blocking)."""
    db = get_state_db()
    if db is None:
        return True, None

    try:
        return db.add_claim(function_name, agent_id or AGENT_ID)
    except Exception:
        return True, None


def db_release_claim(function_name: str, agent_id: str | None = None) -> bool:
    """Release claim in state database (non-blocking)."""
    db = get_state_db()
    if db is None:
        return True

    try:
        return db.release_claim(function_name, agent_id)
    except Exception:
        return True


def db_upsert_scratch(slug: str, instance: str, base_url: str, **fields) -> bool:
    """Update scratch in state database (non-blocking)."""
    db = get_state_db()
    if db is None:
        return False

    try:
        db.upsert_scratch(slug, instance, base_url, agent_id=AGENT_ID, **fields)
        return True
    except Exception:
        return False


def db_record_match_score(
    scratch_slug: str,
    score: int,
    max_score: int,
    worktree_path: str | None = None,
    branch: str | None = None,
) -> bool:
    """Record match score in state database (non-blocking).

    Args:
        scratch_slug: The scratch identifier
        score: Current diff score (0 = perfect match)
        max_score: Maximum possible score
        worktree_path: Path to the worktree where work was done
        branch: Git branch name where work was done
    """
    db = get_state_db()
    if db is None:
        return False

    try:
        db.record_match_score(scratch_slug, score, max_score, worktree_path, branch)
        return True
    except Exception:
        return False


def db_record_sync(local_slug: str, production_slug: str, function_name: str | None = None) -> bool:
    """Record sync in state database (non-blocking)."""
    db = get_state_db()
    if db is None:
        return False

    try:
        db.record_sync(local_slug, production_slug, function_name)
        return True
    except Exception:
        return False


# Claim renewal for auto-extending claims on activity
DECOMP_CLAIMS_FILE = os.environ.get("DECOMP_CLAIMS_FILE", "/tmp/decomp_claims.json")
DECOMP_CLAIM_TIMEOUT = int(os.environ.get("DECOMP_CLAIM_TIMEOUT", "10800"))  # 3 hours


def renew_claim_on_activity(function_name: str, agent_id: str | None = None) -> bool:
    """Silently renew a claim if owned by this agent.

    This is called automatically during scratch compile and other activity
    to prevent claims from expiring during long sessions.

    Args:
        function_name: Function to renew claim for
        agent_id: Agent ID (defaults to current agent)

    Returns:
        True if claim was renewed, False if not (not owned or not claimed)
    """
    from .utils import file_lock, load_json_safe

    agent_id = agent_id or AGENT_ID
    claims_path = Path(DECOMP_CLAIMS_FILE)
    lock_path = claims_path.with_suffix(".json.lock")

    if not claims_path.exists():
        return False

    try:
        with file_lock(lock_path, exclusive=True, timeout=5):
            claims = load_json_safe(claims_path)

            if function_name not in claims:
                return False

            claim = claims[function_name]
            if claim.get("agent_id") != agent_id:
                return False

            # Renew the claim by updating timestamp
            claim["timestamp"] = time.time()

            # Save updated claims
            with open(claims_path, "w") as f:
                json.dump(claims, f, indent=2)

            # Also renew subdirectory lock if applicable
            subdir_key = claim.get("subdirectory")
            if subdir_key:
                db_lock_subdirectory(subdir_key, agent_id)

            # Renew in database too
            db_add_claim(function_name, agent_id)

            return True
    except (TimeoutError, Exception):
        # Non-blocking - don't fail if lock unavailable
        return False
