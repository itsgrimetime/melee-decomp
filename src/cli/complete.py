"""Complete commands - track completed/attempted functions."""

import json
import os
import subprocess
import time
from pathlib import Path
from typing import Annotated, Any, Optional

import typer
from rich.table import Table

from ._common import console, db_upsert_function, db_release_claim
from .utils import file_lock, load_json_with_expiry, save_json_atomic

# File paths
DECOMP_CLAIMS_FILE = os.environ.get("DECOMP_CLAIMS_FILE", "/tmp/decomp_claims.json")
DECOMP_CLAIM_TIMEOUT = int(os.environ.get("DECOMP_CLAIM_TIMEOUT", "10800"))  # 3 hours

complete_app = typer.Typer(help="Track completed/attempted functions")


def _load_claims() -> dict[str, Any]:
    """Load claims from file, removing stale entries."""
    return load_json_with_expiry(
        Path(DECOMP_CLAIMS_FILE),
        timeout_seconds=DECOMP_CLAIM_TIMEOUT,
        timestamp_field="timestamp",
    )


def _save_claims(claims: dict[str, Any]) -> None:
    """Save claims to file."""
    save_json_atomic(Path(DECOMP_CLAIMS_FILE), claims)


def _load_completed() -> dict[str, Any]:
    """Load completed functions from database."""
    from ._common import load_completed_functions
    return load_completed_functions()


def _save_completed(completed: dict[str, Any]) -> None:
    """Save completed functions to database."""
    from ._common import save_completed_functions
    save_completed_functions(completed)


def _get_current_branch(repo_path: Path | None = None) -> str | None:
    """Get the current git branch name."""
    try:
        cmd = ["git", "rev-parse", "--abbrev-ref", "HEAD"]
        result = subprocess.run(
            cmd,
            cwd=repo_path,
            capture_output=True,
            text=True,
            timeout=5,
        )
        if result.returncode == 0:
            return result.stdout.strip()
    except Exception:
        pass
    return None


@complete_app.command("mark")
def complete_mark(
    function_name: Annotated[str, typer.Argument(help="Function name")],
    scratch_slug: Annotated[str, typer.Argument(help="Decomp.me scratch slug")],
    match_percent: Annotated[float, typer.Argument(help="Match percentage achieved")],
    committed: Annotated[
        bool, typer.Option("--committed", help="Mark as committed to repo")
    ] = False,
    branch: Annotated[
        Optional[str], typer.Option("--branch", "-b", help="Git branch (auto-detected if not specified)")
    ] = None,
    notes: Annotated[
        Optional[str], typer.Option("--notes", help="Additional notes")
    ] = None,
    output_json: Annotated[
        bool, typer.Option("--json", help="Output as JSON")
    ] = False,
):
    """Mark a function as completed/attempted."""
    # Auto-detect branch if not specified
    if branch is None:
        branch = _get_current_branch()

    completed = _load_completed()
    completed[function_name] = {
        "match_percent": match_percent,
        "scratch_slug": scratch_slug,
        "committed": committed,
        "branch": branch,
        "notes": notes or "",
        "timestamp": time.time(),
    }
    _save_completed(completed)

    # Also write to state database (non-blocking)
    status = 'committed' if committed else ('matched' if match_percent >= 95 else 'in_progress')
    db_upsert_function(
        function_name,
        match_percent=match_percent,
        local_scratch_slug=scratch_slug,
        is_committed=committed,
        status=status,
        branch=branch,
        notes=notes or "",
    )

    # Also release any claim
    claims_path = Path(DECOMP_CLAIMS_FILE)
    if claims_path.exists():
        lock_path = claims_path.with_suffix(".json.lock")
        with file_lock(lock_path, exclusive=True):
            claims = _load_claims()
            if function_name in claims:
                del claims[function_name]
                _save_claims(claims)

    # Also release from state database (non-blocking)
    db_release_claim(function_name)

    if output_json:
        print(json.dumps({"success": True, "function": function_name, "match_percent": match_percent, "branch": branch}))
    else:
        status = "committed" if committed else "recorded"
        branch_info = f" on {branch}" if branch else ""
        console.print(f"[green]Completed ({status}):[/green] {function_name} at {match_percent:.1f}%{branch_info}")

        # CRITICAL WARNING: Remind users that non-committed work is NOT saved
        if not committed and match_percent >= 95.0:
            console.print()
            console.print("[bold red]" + "=" * 60 + "[/bold red]")
            console.print("[bold red]WARNING: This function is NOT committed to the repository![/bold red]")
            console.print("[bold red]" + "=" * 60 + "[/bold red]")
            console.print()
            console.print("[yellow]Your work will be LOST unless you run:[/yellow]")
            console.print(f"  [cyan]melee-agent workflow finish {function_name} <scratch_slug>[/cyan]")
            console.print()
            console.print("[dim]Or use the two-step process:[/dim]")
            console.print(f"  [dim]melee-agent commit apply {function_name} <scratch_slug>[/dim]")
            console.print(f"  [dim]melee-agent complete mark {function_name} <slug> {match_percent:.1f} --committed[/dim]")
            console.print()


@complete_app.command("document")
def complete_document(
    function_name: Annotated[str, typer.Argument(help="Function name")],
    status: Annotated[
        str, typer.Option("--status", "-s", help="Documentation status: partial or complete")
    ] = "complete",
    notes: Annotated[
        Optional[str], typer.Option("--notes", help="Documentation notes")
    ] = None,
    output_json: Annotated[
        bool, typer.Option("--json", help="Output as JSON")
    ] = False,
):
    """Mark a function as documented (naming, comments, struct fields).

    Use this after completing documentation work on a function:
    - Naming the function and its parameters
    - Adding @brief, @param, @return comments
    - Naming struct fields used by the function

    This is separate from matching (use 'complete mark' for match progress).
    """
    if status not in ("partial", "complete"):
        console.print(f"[red]Invalid status: {status}. Use 'partial' or 'complete'[/red]")
        raise typer.Exit(1)

    # Update state database
    db_upsert_function(
        function_name,
        is_documented=True,
        documentation_status=status,
        documented_at=time.time(),
        notes=notes or "",
    )

    # Release any claim
    db_release_claim(function_name)

    if output_json:
        print(json.dumps({
            "success": True,
            "function": function_name,
            "documentation_status": status,
        }))
    else:
        status_str = "[green]complete[/green]" if status == "complete" else "[yellow]partial[/yellow]"
        console.print(f"[green]Documented:[/green] {function_name} ({status_str})")
        if notes:
            console.print(f"[dim]Notes: {notes}[/dim]")


@complete_app.command("list")
def complete_list(
    min_match: Annotated[
        float, typer.Option("--min-match", help="Minimum match percentage")
    ] = 0.0,
    output_json: Annotated[
        bool, typer.Option("--json", help="Output as JSON")
    ] = False,
):
    """List all completed/attempted functions."""
    completed = _load_completed()

    # Filter by min_match
    filtered = {
        name: info for name, info in completed.items()
        if info.get("match_percent", 0) >= min_match
    }

    if output_json:
        print(json.dumps(filtered, indent=2))
    else:
        if not filtered:
            console.print("[dim]No completed functions found[/dim]")
            return

        table = Table(title="Completed Functions")
        table.add_column("Function", style="cyan")
        table.add_column("Match %", justify="right")
        table.add_column("Scratch")
        table.add_column("Branch", style="dim")
        table.add_column("Status")
        table.add_column("Notes", style="dim")

        sorted_funcs = sorted(filtered.items(), key=lambda x: -x[1].get("match_percent", 0))
        for name, info in sorted_funcs:
            status = "[green]✓[/green]" if info.get("committed") else "[yellow]○[/yellow]"
            table.add_row(
                name,
                f"{info.get('match_percent', 0):.1f}%",
                info.get("scratch_slug", "?"),
                info.get("branch", "-") or "-",
                status,
                info.get("notes", "")[:30],
            )

        console.print(table)
