"""Worktree utilities for subdirectory-based isolation.

Provides functions for managing git worktrees allocated per source subdirectory.
This enables easy merges since commits to different subdirectories rarely conflict.

Mapping examples:
  melee/ft/chara/ftFox/*.c  -> dir-ft-chara-ftFox
  melee/ft/chara/ftCommon/*.c -> dir-ft-chara-ftCommon
  melee/lb/*.c -> dir-lb
  melee/gr/*.c -> dir-gr
"""

import json
import os
import shutil
import subprocess
import time
from pathlib import Path

from rich.console import Console

from src.client.api import _get_agent_id
from src.cli._common import ensure_dol_in_worktree

# Console for rich output
console = Console()

# Paths
DECOMP_CONFIG_DIR = Path.home() / ".config" / "decomp-me"
PROJECT_ROOT = Path(__file__).parent.parent.parent
DEFAULT_MELEE_ROOT = PROJECT_ROOT / "melee"
MELEE_WORKTREES_DIR = PROJECT_ROOT / "melee-worktrees"

# Agent ID for isolation
AGENT_ID = _get_agent_id()

# Claim timeout (3 hours, consistent with claim.py)
DECOMP_CLAIM_TIMEOUT = int(os.environ.get("DECOMP_CLAIM_TIMEOUT", "10800"))


def _get_state_db():
    """Get the state database instance (non-blocking)."""
    try:
        from src.db import get_db
        return get_db()
    except Exception:
        return None


# =============================================================================
# Subdirectory Key Mapping
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


# =============================================================================
# Database Wrappers (Subdirectory-specific)
# =============================================================================


def db_upsert_subdirectory(
    subdir_key: str,
    worktree_path: str,
    branch_name: str,
    locked_by_agent: str | None = None,
) -> bool:
    """Update subdirectory allocation in state database (non-blocking)."""
    db = _get_state_db()
    if db is None:
        return False

    try:
        db.upsert_subdirectory(subdir_key, worktree_path, branch_name, locked_by_agent)
        return True
    except Exception:
        return False


def db_lock_subdirectory(subdir_key: str, agent_id: str | None = None) -> tuple[bool, str | None]:
    """Lock a subdirectory for exclusive access by an agent."""
    db = _get_state_db()
    if db is None:
        return True, None

    try:
        return db.lock_subdirectory(subdir_key, agent_id or AGENT_ID)
    except Exception:
        return True, None


def db_unlock_subdirectory(subdir_key: str, agent_id: str | None = None) -> bool:
    """Unlock a subdirectory, allowing other agents to use it."""
    db = _get_state_db()
    if db is None:
        return True

    try:
        return db.unlock_subdirectory(subdir_key, agent_id)
    except Exception:
        return True


def db_get_subdirectory_lock(subdir_key: str) -> dict | None:
    """Get the current lock status for a subdirectory."""
    db = _get_state_db()
    if db is None:
        return None

    try:
        return db.get_subdirectory_lock(subdir_key)
    except Exception:
        return None


# =============================================================================
# Build Validation
# =============================================================================


def _validate_worktree_build(worktree_path: Path, max_age_minutes: int = 30) -> bool:
    """Check if a worktree builds successfully."""
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
            ["python", "configure.py"],
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


# =============================================================================
# Worktree Creation and Management
# =============================================================================


def _create_subdirectory_worktree(subdir_key: str, worktree_path: Path) -> Path:
    """Create a new worktree for a subdirectory."""
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

        # Ensure base DOL exists (required for builds)
        if not ensure_dol_in_worktree(worktree_path):
            console.print("[yellow]Warning: Base DOL not found. Run 'melee-agent setup dol --auto' to configure.[/yellow]")

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


def get_agent_melee_root(
    agent_id: str | None = None,
    create_if_missing: bool = True,
    validate_build: bool = True,
) -> Path:
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
    from .storage import get_context_file
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
    """Look up the source file from a function's claim.

    Checks in order:
    1. Active claim in claims file
    2. Database function record

    Returns:
        Source file path (e.g., "melee/lb/lbcollision.c") or None if not found.
    """
    # First try the claims file
    claims_file = Path("/tmp/decomp_claims.json")
    if claims_file.exists():
        try:
            with open(claims_file, 'r') as f:
                claims = json.load(f)

            if function_name in claims:
                claim = claims[function_name]
                # Use configured timeout instead of hardcoded value
                if time.time() - claim.get("timestamp", 0) < DECOMP_CLAIM_TIMEOUT:
                    source_file = claim.get("source_file")
                    if source_file:
                        return source_file
        except (json.JSONDecodeError, IOError):
            pass  # Fall through to database check

    # Fallback: check database for source file
    db = _get_state_db()
    if db:
        try:
            func_info = db.get_function(function_name)
            if func_info and func_info.get('source_file'):
                return func_info['source_file']
        except Exception:
            pass  # Non-blocking - don't fail if DB unavailable

    return None
