"""Claim commands - manage function claims for parallel agents."""

import fcntl
import json
import os
import time
from pathlib import Path
from typing import Annotated, Any

import typer
from rich.table import Table

from ._common import console

# Claims are SHARED and ephemeral (1-hour expiry) - ok in /tmp
DECOMP_CLAIMS_FILE = os.environ.get("DECOMP_CLAIMS_FILE", "/tmp/decomp_claims.json")
DECOMP_CLAIM_TIMEOUT = int(os.environ.get("DECOMP_CLAIM_TIMEOUT", "3600"))  # 1 hour

# Completed functions file path
DECOMP_COMPLETED_FILE = os.environ.get(
    "DECOMP_COMPLETED_FILE",
    str(Path.home() / ".config" / "decomp-me" / "completed_functions.json")
)

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
    """Load completed functions from file."""
    completed_path = Path(DECOMP_COMPLETED_FILE)
    if not completed_path.exists():
        return {}

    try:
        with open(completed_path, 'r') as f:
            return json.load(f)
    except (json.JSONDecodeError, IOError):
        return {}


@claim_app.command("add")
def claim_add(
    function_name: Annotated[str, typer.Argument(help="Function name to claim")],
    agent_id: Annotated[
        str, typer.Option("--agent-id", help="Agent identifier")
    ] = "cli",
    output_json: Annotated[
        bool, typer.Option("--json", help="Output as JSON")
    ] = False,
):
    """Claim a function to prevent other agents from working on it."""
    # Check if already completed
    completed = _load_completed()
    if function_name in completed:
        info = completed[function_name]
        if output_json:
            print(json.dumps({"success": False, "error": "already_completed", "info": info}))
        else:
            console.print(f"[red]Function already completed:[/red] {info.get('match_percent', 0):.1f}% match")
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
                age_mins = (time.time() - existing["timestamp"]) / 60
                if output_json:
                    print(json.dumps({"success": False, "error": "already_claimed", "by": existing.get("agent_id"), "age_mins": age_mins}))
                else:
                    console.print(f"[red]Already claimed by {existing.get('agent_id')} ({age_mins:.0f}m ago)[/red]")
                raise typer.Exit(1)

            claims[function_name] = {"agent_id": agent_id, "timestamp": time.time()}
            _save_claims(claims)

            if output_json:
                print(json.dumps({"success": True, "function": function_name}))
            else:
                console.print(f"[green]Claimed:[/green] {function_name}")
        finally:
            fcntl.flock(lock_file.fileno(), fcntl.LOCK_UN)


@claim_app.command("release")
def claim_release(
    function_name: Annotated[str, typer.Argument(help="Function name to release")],
    output_json: Annotated[
        bool, typer.Option("--json", help="Output as JSON")
    ] = False,
):
    """Release a claimed function."""
    claims_path = Path(DECOMP_CLAIMS_FILE)
    if not claims_path.exists():
        if output_json:
            print(json.dumps({"success": False, "error": "not_claimed"}))
        else:
            console.print(f"[yellow]Function was not claimed[/yellow]")
        return

    lock_path = Path(str(claims_path) + ".lock")
    lock_path.touch(exist_ok=True)

    with open(lock_path, 'r') as lock_file:
        try:
            fcntl.flock(lock_file.fileno(), fcntl.LOCK_EX)
            claims = _load_claims()

            if function_name not in claims:
                if output_json:
                    print(json.dumps({"success": False, "error": "not_claimed"}))
                else:
                    console.print(f"[yellow]Function was not claimed[/yellow]")
                return

            del claims[function_name]
            _save_claims(claims)

            if output_json:
                print(json.dumps({"success": True, "function": function_name}))
            else:
                console.print(f"[green]Released:[/green] {function_name}")
        finally:
            fcntl.flock(lock_file.fileno(), fcntl.LOCK_UN)


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
        table.add_column("Age", justify="right")
        table.add_column("Remaining", justify="right")

        now = time.time()
        for name, info in sorted(claims.items()):
            age_mins = (now - info["timestamp"]) / 60
            remaining_mins = (DECOMP_CLAIM_TIMEOUT / 60) - age_mins
            table.add_row(name, info.get("agent_id", "?"), f"{age_mins:.0f}m", f"{remaining_mins:.0f}m")

        console.print(table)
