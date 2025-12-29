"""Claim commands - manage function claims for parallel agents."""

import fcntl
import json
import os
import time
from pathlib import Path
from typing import Annotated, Any

import typer
from rich.table import Table

from ._common import (
    AGENT_ID,
    console,
    db_add_claim,
    db_release_claim,
    db_lock_subdirectory,
    db_unlock_subdirectory,
    db_get_subdirectory_lock,
    get_subdirectory_key,
    get_worktree_for_file,
    DEFAULT_MELEE_ROOT,
)


def _lookup_source_file(function_name: str) -> str | None:
    """Look up the source file for a function from the extractor.

    This allows auto-detection of the source file without requiring
    the --source-file flag.

    Args:
        function_name: Name of the function to look up

    Returns:
        Source file path (e.g., "melee/lb/lbcollision.c") or None if not found.
    """
    try:
        from src.extractor import FunctionExtractor
        extractor = FunctionExtractor(DEFAULT_MELEE_ROOT)
        func_info = extractor.extract_function(function_name)
        if func_info and func_info.file_path:
            return func_info.file_path
    except Exception:
        pass  # Silently fail - auto-detection is optional
    return None

# Claims are SHARED and ephemeral (1-hour expiry) - ok in /tmp
DECOMP_CLAIMS_FILE = os.environ.get("DECOMP_CLAIMS_FILE", "/tmp/decomp_claims.json")
DECOMP_CLAIM_TIMEOUT = int(os.environ.get("DECOMP_CLAIM_TIMEOUT", "3600"))  # 1 hour


claim_app = typer.Typer(help="Manage function claims for parallel agents")


def _load_claims() -> dict[str, Any]:
    """Load claims from file, removing stale entries."""
    claims_path = Path(DECOMP_CLAIMS_FILE)
    if not claims_path.exists():
        return {}

    try:
        with open(claims_path, 'r') as f:
            claims = json.load(f)
    except (json.JSONDecodeError, IOError):
        return {}

    # Remove stale claims
    now = time.time()
    return {
        name: info for name, info in claims.items()
        if now - info.get("timestamp", 0) < DECOMP_CLAIM_TIMEOUT
    }


def _save_claims(claims: dict[str, Any]) -> None:
    """Save claims to file."""
    claims_path = Path(DECOMP_CLAIMS_FILE)
    claims_path.parent.mkdir(parents=True, exist_ok=True)
    with open(claims_path, 'w') as f:
        json.dump(claims, f, indent=2)


def _load_completed() -> dict[str, Any]:
    """Load completed functions from database."""
    from ._common import load_completed_functions
    return load_completed_functions()


def _check_subdirectory_availability(source_file: str, agent_id: str) -> tuple[bool, str | None, str | None]:
    """Check if subdirectory is available for claiming.

    Args:
        source_file: Path to source file (e.g., "melee/ft/chara/ftFox/ftFx_SpecialHi.c")
        agent_id: Agent trying to claim

    Returns:
        (available, error_message, subdirectory_key) tuple
    """
    subdir_key = get_subdirectory_key(source_file)
    lock_info = db_get_subdirectory_lock(subdir_key)

    if lock_info and lock_info.get("locked_by_agent"):
        locked_by = lock_info["locked_by_agent"]
        if locked_by != agent_id:
            # Check if lock has expired
            if not lock_info.get("lock_expired"):
                return False, f"Subdirectory '{subdir_key}' is locked by {locked_by}", subdir_key

    return True, None, subdir_key


@claim_app.command("add")
def claim_add(
    function_name: Annotated[str, typer.Argument(help="Function name to claim")],
    agent_id: Annotated[
        str, typer.Option("--agent-id", help="Agent identifier")
    ] = AGENT_ID,
    source_file: Annotated[
        str | None, typer.Option("--source-file", "-f", help="Source file path (auto-detected if not provided)")
    ] = None,
    output_json: Annotated[
        bool, typer.Option("--json", help="Output as JSON")
    ] = False,
):
    """Claim a function to prevent other agents from working on it.

    The source file is auto-detected from the function name, which enables
    the subdirectory worktree system for isolated commits. Use --source-file
    to override if auto-detection fails.
    """
    # Auto-detect source file if not provided
    if not source_file:
        source_file = _lookup_source_file(function_name)
        if source_file and not output_json:
            console.print(f"[dim]Auto-detected source file: {source_file}[/dim]")

    # Check if already completed
    completed = _load_completed()
    if function_name in completed:
        info = completed[function_name]
        if output_json:
            print(json.dumps({"success": False, "error": "already_completed", "info": info}))
        else:
            console.print(f"[red]Function already completed:[/red] {info.get('match_percent', 0):.1f}% match")
        raise typer.Exit(1)

    # Check subdirectory availability if source file provided
    subdir_key = None
    if source_file:
        available, error, subdir_key = _check_subdirectory_availability(source_file, agent_id)
        if not available:
            if output_json:
                print(json.dumps({"success": False, "error": "subdirectory_locked", "message": error, "subdirectory": subdir_key}))
            else:
                console.print(f"[red]{error}[/red]")
                console.print(f"[yellow]Pick a function in a different subdirectory, or wait for the lock to expire.[/yellow]")
            raise typer.Exit(1)

    claims_path = Path(DECOMP_CLAIMS_FILE)
    claims_path.parent.mkdir(parents=True, exist_ok=True)
    lock_path = Path(str(claims_path) + ".lock")
    lock_path.touch(exist_ok=True)

    with open(lock_path, 'r') as lock_file:
        try:
            fcntl.flock(lock_file.fileno(), fcntl.LOCK_EX)
            claims = _load_claims()

            if function_name in claims:
                existing = claims[function_name]
                existing_agent = existing.get("agent_id", "unknown")
                age_mins = (time.time() - existing["timestamp"]) / 60
                is_self = existing_agent == agent_id
                if output_json:
                    print(json.dumps({"success": False, "error": "already_claimed", "by": existing_agent, "age_mins": age_mins, "is_self": is_self}))
                else:
                    if is_self:
                        console.print(f"[yellow]Already claimed by you ({agent_id}) {age_mins:.0f}m ago - claim still active[/yellow]")
                    else:
                        console.print(f"[red]CLAIMED BY ANOTHER AGENT: {existing_agent} ({age_mins:.0f}m ago)[/red]")
                        console.print(f"[red]DO NOT WORK ON THIS FUNCTION - pick a different one[/red]")
                raise typer.Exit(1)

            claims[function_name] = {
                "agent_id": agent_id,
                "timestamp": time.time(),
                "source_file": source_file,
                "subdirectory": subdir_key,
            }
            _save_claims(claims)

            # Also write to state database (non-blocking)
            db_add_claim(function_name, agent_id)

            # Lock subdirectory if source file provided
            worktree_path = None
            if source_file and subdir_key:
                db_lock_subdirectory(subdir_key, agent_id)
                # Get or create the worktree (don't create yet, just get path)
                from ._common import get_subdirectory_worktree_path
                worktree_path = str(get_subdirectory_worktree_path(subdir_key))

            if output_json:
                result = {"success": True, "function": function_name}
                if subdir_key:
                    result["subdirectory"] = subdir_key
                if worktree_path:
                    result["worktree"] = worktree_path
                print(json.dumps(result))
            else:
                console.print(f"[green]Claimed:[/green] {function_name}")
                if subdir_key:
                    console.print(f"[dim]Subdirectory:[/dim] {subdir_key}")
                    console.print(f"[dim]Worktree will be at:[/dim] melee-worktrees/dir-{subdir_key}/")
        finally:
            fcntl.flock(lock_file.fileno(), fcntl.LOCK_UN)


def _release_claim(function_name: str, release_subdirectory: bool = False) -> tuple[bool, str | None]:
    """Internal function to release a claim.

    Args:
        function_name: Function to release
        release_subdirectory: If True, also release the subdirectory lock

    Returns:
        (released, subdirectory_key) tuple
    """
    claims_path = Path(DECOMP_CLAIMS_FILE)
    if not claims_path.exists():
        # Also release from DB even if JSON doesn't exist
        db_release_claim(function_name)
        return False, None

    lock_path = Path(str(claims_path) + ".lock")
    lock_path.touch(exist_ok=True)

    with open(lock_path, 'r') as lock_file:
        try:
            fcntl.flock(lock_file.fileno(), fcntl.LOCK_EX)
            claims = _load_claims()

            if function_name not in claims:
                # Also release from DB even if not in JSON
                db_release_claim(function_name)
                return False, None

            # Get subdirectory info before deleting
            claim_info = claims[function_name]
            subdir_key = claim_info.get("subdirectory")

            del claims[function_name]
            _save_claims(claims)

            # Also release from state database (non-blocking)
            db_release_claim(function_name)

            # Release subdirectory lock if requested
            if release_subdirectory and subdir_key:
                db_unlock_subdirectory(subdir_key)

            return True, subdir_key
        finally:
            fcntl.flock(lock_file.fileno(), fcntl.LOCK_UN)


@claim_app.command("release")
def claim_release(
    function_name: Annotated[str, typer.Argument(help="Function name to release")],
    release_subdirectory: Annotated[
        bool, typer.Option("--release-subdir", "-s", help="Also release the subdirectory lock")
    ] = False,
    output_json: Annotated[
        bool, typer.Option("--json", help="Output as JSON")
    ] = False,
):
    """Release a claimed function.

    Use --release-subdir to also release the subdirectory lock,
    allowing other agents to work on that subdirectory.
    """
    released, subdir_key = _release_claim(function_name, release_subdirectory)

    if not released:
        if output_json:
            print(json.dumps({"success": False, "error": "not_claimed"}))
        else:
            console.print(f"[yellow]Function was not claimed[/yellow]")
        return

    if output_json:
        result = {"success": True, "function": function_name}
        if subdir_key:
            result["subdirectory"] = subdir_key
            result["subdirectory_released"] = release_subdirectory
        print(json.dumps(result))
    else:
        console.print(f"[green]Released:[/green] {function_name}")
        if subdir_key:
            if release_subdirectory:
                console.print(f"[dim]Released subdirectory lock:[/dim] {subdir_key}")
            else:
                console.print(f"[dim]Subdirectory still locked:[/dim] {subdir_key}")
                console.print(f"[dim]Use --release-subdir to also release the subdirectory lock[/dim]")


@claim_app.command("list")
def claim_list(
    output_json: Annotated[
        bool, typer.Option("--json", help="Output as JSON")
    ] = False,
):
    """List all currently claimed functions."""
    claims = _load_claims()

    if output_json:
        print(json.dumps(claims, indent=2))
    else:
        if not claims:
            console.print("[dim]No functions currently claimed[/dim]")
            return

        table = Table(title="Claimed Functions")
        table.add_column("Function", style="cyan")
        table.add_column("Agent")
        table.add_column("Subdirectory", style="dim")
        table.add_column("Age", justify="right")
        table.add_column("Remaining", justify="right")

        now = time.time()
        for name, info in sorted(claims.items()):
            age_mins = (now - info["timestamp"]) / 60
            remaining_mins = (DECOMP_CLAIM_TIMEOUT / 60) - age_mins
            subdir = info.get("subdirectory", "")
            table.add_row(
                name,
                info.get("agent_id", "?"),
                subdir or "-",
                f"{age_mins:.0f}m",
                f"{remaining_mins:.0f}m"
            )

        console.print(table)
