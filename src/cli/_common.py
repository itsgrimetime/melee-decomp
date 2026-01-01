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

# Compiler version mapping: GC SDK version -> decomp.me compiler ID
# From melee/build/compilers/info.txt
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

# Default compiler if detection fails
DEFAULT_DECOMP_COMPILER = "mwcc_247_92"


def get_compiler_for_source(source_file: str, melee_root: Path) -> str:
    """Get the decomp.me compiler ID for a source file by parsing build.ninja.

    Args:
        source_file: Relative path to source file (e.g., "src/melee/lb/lbcollision.c")
        melee_root: Path to melee repo root

    Returns:
        decomp.me compiler ID (e.g., "mwcc_233_163n")
    """
    build_ninja = melee_root / "build.ninja"
    if not build_ninja.exists():
        console.print(f"[yellow]build.ninja not found, using default compiler[/yellow]")
        return DEFAULT_DECOMP_COMPILER

    # Normalize source path
    if source_file.startswith("src/"):
        source_rel = source_file
    else:
        source_rel = f"src/{source_file}"

    try:
        content = build_ninja.read_text()
        lines = content.split('\n')

        # Convert source_rel to the format used in comments (without src/ prefix)
        comment_path = source_rel
        if comment_path.startswith("src/"):
            comment_path = comment_path[4:]

        in_target = False
        for i, line in enumerate(lines):
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


# =============================================================================
# PR Helpers
# =============================================================================

DEFAULT_PR_REPO = "doldecomp/melee"


def extract_pr_info(pr_input: str, default_repo: str = DEFAULT_PR_REPO) -> tuple[str, int]:
    """Extract repo and PR number from URL or PR number.

    Accepts:
    - Full URL: https://github.com/doldecomp/melee/pull/123
    - Just PR number: 123 or 2049 (uses default_repo)
    - Repo#number: doldecomp/melee#123

    Returns: (repo, pr_number) e.g. ("doldecomp/melee", 123)
    """
    pr_input = pr_input.strip()

    # Full URL
    match = re.match(r'https?://github\.com/([^/]+/[^/]+)/pull/(\d+)', pr_input)
    if match:
        return match.group(1), int(match.group(2))

    # Repo#number format (e.g., doldecomp/melee#123)
    match = re.match(r'([^/]+/[^#]+)#(\d+)', pr_input)
    if match:
        return match.group(1), int(match.group(2))

    # Just a PR number
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
        "merged": [],
        "in_review": [],
        "committed": [],
        "ready": [],
        "lost_high_match": [],
        "work_in_progress": [],
    }

    # Build indexes
    prod_funcs = {v.get("function"): k for k, v in data["slug_map"].items()}
    synced_local_slugs = set(data["synced"].keys())

    # Cache PR statuses if checking
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
    """Get the state database instance.

    Returns None if database module is not available or fails to initialize.
    """
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
    except Exception as e:
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


def db_record_match_score(scratch_slug: str, score: int, max_score: int) -> bool:
    """Record match score in state database (non-blocking)."""
    db = get_state_db()
    if db is None:
        return False

    try:
        db.record_match_score(scratch_slug, score, max_score)
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


# =============================================================================
# Subdirectory-Based Worktree System
# =============================================================================


def get_subdirectory_key(file_path: str) -> str:
    """Map a file path to its subdirectory worktree key.

    Args:
        file_path: Path in any of these formats:
                   - "ft/chara/ftFox/ftFx_SpecialHi.c" (relative to src/melee/)
                   - "melee/ft/chara/ftFox/ftFx_SpecialHi.c" (relative to melee repo)
                   - "src/melee/ft/chara/ftFox/ftFx_SpecialHi.c" (relative to melee repo)
                   - "melee/src/melee/ft/chara/ftFox/ftFx_SpecialHi.c" (project-relative)

    Returns:
        Subdirectory key like "ft-chara-ftFox"
    """
    path = Path(file_path)
    parts = list(path.parent.parts)

    # Strip common prefixes in order they might appear
    if len(parts) >= 3 and parts[0] == "melee" and parts[1] == "src" and parts[2] == "melee":
        parts = parts[3:]
    else:
        if parts and parts[0] == "melee":
            parts = parts[1:]
        if parts and parts[0] == "src":
            parts = parts[1:]
        if parts and parts[0] == "melee":
            parts = parts[1:]

    if not parts:
        return "root"

    # Special handling for ft/chara - use character subdirectory
    if len(parts) >= 3 and parts[0] == "ft" and parts[1] == "chara":
        return f"ft-chara-{parts[2]}"

    # Special handling for it/items
    if len(parts) >= 2 and parts[0] == "it" and parts[1] == "items":
        return "it-items"

    return parts[0]


def get_worktree_name_for_subdirectory(subdir_key: str) -> str:
    """Get the worktree directory name for a subdirectory key."""
    return f"dir-{subdir_key}"


def get_subdirectory_worktree_path(subdir_key: str) -> Path:
    """Get the full path to a subdirectory worktree."""
    return MELEE_WORKTREES_DIR / get_worktree_name_for_subdirectory(subdir_key)


def db_upsert_subdirectory(
    subdir_key: str,
    worktree_path: str,
    branch_name: str,
    locked_by_agent: str | None = None,
) -> bool:
    """Update subdirectory allocation in state database (non-blocking)."""
    db = get_state_db()
    if db is None:
        return False

    try:
        db.upsert_subdirectory(subdir_key, worktree_path, branch_name, locked_by_agent)
        return True
    except Exception:
        return False


def db_lock_subdirectory(subdir_key: str, agent_id: str | None = None) -> tuple[bool, str | None]:
    """Lock a subdirectory for exclusive access by an agent."""
    db = get_state_db()
    if db is None:
        return True, None

    try:
        return db.lock_subdirectory(subdir_key, agent_id or AGENT_ID)
    except Exception as e:
        return True, None


def db_unlock_subdirectory(subdir_key: str, agent_id: str | None = None) -> bool:
    """Unlock a subdirectory, allowing other agents to use it."""
    db = get_state_db()
    if db is None:
        return True

    try:
        return db.unlock_subdirectory(subdir_key, agent_id)
    except Exception:
        return True


def db_get_subdirectory_lock(subdir_key: str) -> dict | None:
    """Get the current lock status for a subdirectory."""
    db = get_state_db()
    if db is None:
        return None

    try:
        return db.get_subdirectory_lock(subdir_key)
    except Exception:
        return None


def _validate_worktree_build(worktree_path: Path, max_age_minutes: int = 30) -> bool:
    """Check if a worktree builds successfully with --require-protos."""
    marker_file = worktree_path / ".build_validated"

    if marker_file.exists():
        marker_age = time.time() - marker_file.stat().st_mtime
        if marker_age < max_age_minutes * 60:
            src_dir = worktree_path / "src"
            marker_mtime = marker_file.stat().st_mtime
            needs_revalidation = False

            for check_dir in [src_dir / "melee" / "lb", src_dir / "melee" / "ft"]:
                if check_dir.exists():
                    for pattern in ["*.c", "*.h"]:
                        for f in check_dir.rglob(pattern):
                            if f.stat().st_mtime > marker_mtime:
                                needs_revalidation = True
                                break
                        if needs_revalidation:
                            break
                    if needs_revalidation:
                        break

            if not needs_revalidation:
                console.print(f"[dim]Build validated {int(marker_age / 60)}m ago[/dim]")
                return True

    console.print(f"[dim]Running build validation (this may take a minute)...[/dim]")

    try:
        result = subprocess.run(
            ["python", "configure.py", "--require-protos"],
            cwd=worktree_path,
            capture_output=True, text=True,
            timeout=30
        )
        if result.returncode != 0:
            console.print(f"[red]configure.py failed:[/red]")
            if result.stderr:
                console.print(f"[dim]{result.stderr[:500]}[/dim]")
            elif result.stdout:
                console.print(f"[dim]{result.stdout[:500]}[/dim]")
            return False

        result = subprocess.run(
            ["ninja"],
            cwd=worktree_path,
            capture_output=True, text=True,
            timeout=300
        )

        if result.returncode == 0:
            marker_file.touch()
            return True
        else:
            if marker_file.exists():
                marker_file.unlink()
            console.print(f"[red]Build failed:[/red]")
            error_output = result.stderr or result.stdout or "Unknown error"
            lines = error_output.split('\n')
            error_lines = [l for l in lines if 'error:' in l.lower() or 'Error:' in l]
            if error_lines:
                for line in error_lines[:10]:
                    console.print(f"  [dim]{line.strip()}[/dim]")
            else:
                for line in lines[-10:]:
                    if line.strip():
                        console.print(f"  [dim]{line.strip()}[/dim]")
            return False

    except subprocess.TimeoutExpired:
        console.print(f"[red]Build timed out[/red]")
        return False
    except FileNotFoundError as e:
        console.print(f"[red]Build command not found: {e}[/red]")
        return False
    except subprocess.CalledProcessError as e:
        console.print(f"[red]Build process error: {e}[/red]")
        return False


def _create_subdirectory_worktree(subdir_key: str, worktree_path: Path) -> Path:
    """Create a new worktree for a subdirectory."""
    import shutil

    MELEE_WORKTREES_DIR.mkdir(parents=True, exist_ok=True)

    try:
        result = subprocess.run(
            ["git", "rev-parse", "--abbrev-ref", "HEAD"],
            cwd=DEFAULT_MELEE_ROOT,
            capture_output=True, text=True, check=True
        )
        base_branch = result.stdout.strip()
    except subprocess.CalledProcessError:
        base_branch = "master"

    branch_name = f"subdirs/{subdir_key}"

    result = subprocess.run(
        ["git", "branch", "--list", branch_name],
        cwd=DEFAULT_MELEE_ROOT,
        capture_output=True, text=True
    )
    branch_exists = bool(result.stdout.strip())

    try:
        if branch_exists:
            subprocess.run(
                ["git", "worktree", "add", str(worktree_path), branch_name],
                cwd=DEFAULT_MELEE_ROOT,
                capture_output=True, text=True, check=True
            )
        else:
            subprocess.run(
                ["git", "worktree", "add", "-b", branch_name, str(worktree_path), "upstream/master"],
                cwd=DEFAULT_MELEE_ROOT,
                capture_output=True, text=True, check=True
            )

        # Symlink orig/ directory
        orig_src = DEFAULT_MELEE_ROOT / "orig"
        orig_dst = worktree_path / "orig"
        if orig_src.exists():
            if orig_dst.exists() and not orig_dst.is_symlink():
                shutil.rmtree(orig_dst)
            if not orig_dst.exists():
                orig_dst.symlink_to(orig_src.resolve())

        # Copy ctx.c from main melee
        main_ctx = DEFAULT_MELEE_ROOT / "build" / "ctx.c"
        if main_ctx.exists():
            (worktree_path / "build").mkdir(exist_ok=True)
            worktree_ctx = worktree_path / "build" / "ctx.c"
            shutil.copy2(main_ctx, worktree_ctx)

        console.print(f"\n[bold cyan]SUBDIRECTORY WORKTREE CREATED[/bold cyan]")
        console.print(f"  [dim]Subdirectory:[/dim] {subdir_key}")
        console.print(f"  [dim]Path:[/dim]   {worktree_path}")
        console.print(f"  [dim]Branch:[/dim] {branch_name}")
        console.print(f"  [dim]Base:[/dim]   upstream/master")

        db_upsert_subdirectory(subdir_key, str(worktree_path), branch_name)

        # Run build to generate report.json
        configure_py = worktree_path / "configure.py"
        if configure_py.exists():
            console.print(f"\n[dim]Running initial build to generate report.json...[/dim]")
            try:
                subprocess.run(
                    ["python", "configure.py"],
                    cwd=worktree_path,
                    capture_output=True, text=True, check=True
                )
                result = subprocess.run(
                    ["ninja", "build/GALE01/report.json"],
                    cwd=worktree_path,
                    capture_output=True, text=True,
                    timeout=300,
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

        return worktree_path

    except subprocess.CalledProcessError as e:
        console.print(f"[yellow]Warning: Could not create worktree: {e.stderr}[/yellow]")
        console.print(f"[yellow]Falling back to shared melee directory[/yellow]")
        return DEFAULT_MELEE_ROOT


def get_subdirectory_worktree(
    subdir_key: str,
    create_if_missing: bool = True,
    validate_build: bool = True,
) -> Path:
    """Get or create a worktree for a subdirectory."""
    worktree_path = get_subdirectory_worktree_path(subdir_key)

    if worktree_path.exists() and (worktree_path / "src").exists():
        if validate_build:
            console.print(f"[dim]Validating worktree build: {worktree_path}[/dim]")
            if _validate_worktree_build(worktree_path):
                console.print(f"[green]Worktree build OK[/green]")
            else:
                console.print(f"[yellow]Worktree build has errors - fix before committing[/yellow]")
                console.print(f"[dim]Run 'cd {worktree_path} && ninja' to see full errors[/dim]")
            return worktree_path
        else:
            console.print(f"[dim]Using worktree: {worktree_path}[/dim]")
            return worktree_path

    if not create_if_missing:
        return DEFAULT_MELEE_ROOT

    return _create_subdirectory_worktree(subdir_key, worktree_path)


def get_worktree_for_file(
    file_path: str,
    create_if_missing: bool = True,
    validate_build: bool = True,
) -> Path:
    """Get or create the appropriate worktree for a source file."""
    subdir_key = get_subdirectory_key(file_path)
    return get_subdirectory_worktree(
        subdir_key,
        create_if_missing=create_if_missing,
        validate_build=validate_build,
    )


def get_agent_melee_root(agent_id: str | None = None, create_if_missing: bool = True, validate_build: bool = True) -> Path:
    """Get the melee worktree path for the current agent.

    Note: This function is maintained for backwards compatibility.
    New code should use get_worktree_for_file() for subdirectory-based worktrees.
    """
    aid = agent_id or AGENT_ID
    if not aid:
        return DEFAULT_MELEE_ROOT

    worktree_path = MELEE_WORKTREES_DIR / aid

    if worktree_path.exists() and (worktree_path / "src").exists():
        if validate_build:
            console.print(f"[dim]Validating worktree build: {worktree_path}[/dim]")
            if _validate_worktree_build(worktree_path):
                console.print(f"[green]Worktree build OK[/green]")
            else:
                console.print(f"[yellow]Worktree build has errors - fix before committing[/yellow]")
                console.print(f"[dim]Run 'cd {worktree_path} && ninja' to see full errors[/dim]")
            return worktree_path
        else:
            console.print(f"[dim]Using worktree: {worktree_path}[/dim]")
            return worktree_path

    if not create_if_missing:
        return DEFAULT_MELEE_ROOT

    return _create_subdirectory_worktree(aid, worktree_path)


def get_agent_context_file(agent_id: str | None = None, source_file: str | None = None) -> Path:
    """Get the context file path for the current agent."""
    melee_root = get_agent_melee_root(agent_id, create_if_missing=False, validate_build=False)
    return get_context_file(source_file=source_file, melee_root=melee_root)


def resolve_melee_root(
    melee_root: Path | None,
    target_file: str | None = None,
) -> Path:
    """Resolve melee root, using subdirectory worktree if target_file is provided."""
    if melee_root is not None:
        return melee_root

    cwd = Path.cwd()
    try:
        cwd.relative_to(MELEE_WORKTREES_DIR)
        for parent in [cwd] + list(cwd.parents):
            if (parent / ".git").exists() and parent.is_relative_to(MELEE_WORKTREES_DIR):
                return parent
    except ValueError:
        pass

    if target_file:
        return get_worktree_for_file(target_file)

    return DEFAULT_MELEE_ROOT


def get_source_file_from_claim(function_name: str) -> str | None:
    """Look up the source file from a function's claim."""
    claims_file = Path("/tmp/decomp_claims.json")
    if not claims_file.exists():
        return None

    try:
        with open(claims_file, 'r') as f:
            claims = json.load(f)

        if function_name not in claims:
            return None

        claim = claims[function_name]
        if time.time() - claim.get("timestamp", 0) >= 3600:
            return None

        return claim.get("source_file")
    except (json.JSONDecodeError, IOError):
        return None
