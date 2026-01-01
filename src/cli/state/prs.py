"""PR-related state commands: prs, refresh-prs."""

import json
import time
from typing import Annotated

import typer

from .._common import (
    console,
    extract_pr_info,
    get_pr_status_from_gh,
)
from src.db import get_db


def prs_command(
    check_github: Annotated[
        bool, typer.Option("--check", "-c", help="Query GitHub for live status")
    ] = False,
    output_json: Annotated[
        bool, typer.Option("--json", help="Output as JSON")
    ] = False,
):
    """Show all PRs and their associated functions.

    Groups functions by PR and shows status. Use --check to query GitHub
    for current PR state (slower but accurate).
    """
    db = get_db()

    # Get all functions with PR URLs
    with db.connection() as conn:
        cursor = conn.execute("""
            SELECT function_name, pr_url, pr_number, pr_state, match_percent
            FROM functions
            WHERE pr_url IS NOT NULL
            ORDER BY pr_number DESC, function_name
        """)
        functions = [dict(row) for row in cursor.fetchall()]

    if not functions:
        console.print("[dim]No functions linked to PRs[/dim]")
        console.print("[dim]Run: melee-agent audit discover-prs[/dim]")
        return

    # Group by PR
    by_pr: dict[str, list[dict]] = {}
    for func in functions:
        pr_url = func["pr_url"]
        if pr_url not in by_pr:
            by_pr[pr_url] = []
        by_pr[pr_url].append(func)

    # Sort by PR number descending
    sorted_prs = sorted(by_pr.items(), key=lambda x: x[1][0].get("pr_number", 0), reverse=True)

    if output_json:
        output = []
        for pr_url, funcs in sorted_prs:
            pr_data = {
                "pr_url": pr_url,
                "pr_number": funcs[0].get("pr_number"),
                "pr_state": funcs[0].get("pr_state"),
                "function_count": len(funcs),
                "functions": [f["function_name"] for f in funcs],
            }
            if check_github:
                repo, pr_num = extract_pr_info(pr_url)
                if repo and pr_num:
                    gh_status = get_pr_status_from_gh(repo, pr_num)
                    if gh_status:
                        pr_data["github_state"] = gh_status.get("state")
                        pr_data["github_review"] = gh_status.get("reviewDecision")
            output.append(pr_data)
        print(json.dumps(output, indent=2))
        return

    console.print("[bold]PRs with Linked Functions[/bold]\n")

    for pr_url, funcs in sorted_prs:
        pr_num = funcs[0].get("pr_number", "?")
        db_state = funcs[0].get("pr_state")

        # Check GitHub if requested
        gh_state = None
        gh_review = None
        if check_github:
            repo, pr_number = extract_pr_info(pr_url)
            if repo and pr_number:
                gh_status = get_pr_status_from_gh(repo, pr_number)
                if gh_status:
                    gh_state = gh_status.get("state")
                    gh_review = gh_status.get("reviewDecision")

        # Determine display state
        display_state = gh_state or db_state or "?"
        if display_state == "MERGED":
            state_str = "[green]MERGED[/green]"
        elif display_state == "CLOSED":
            state_str = "[red]CLOSED[/red]"
        elif display_state == "OPEN":
            if gh_review == "APPROVED":
                state_str = "[green]APPROVED[/green]"
            elif gh_review == "CHANGES_REQUESTED":
                state_str = "[yellow]CHANGES REQUESTED[/yellow]"
            else:
                state_str = "[cyan]OPEN[/cyan]"
        else:
            state_str = f"[dim]{display_state}[/dim]"

        # Show stale warning if DB state differs from GitHub
        stale_warning = ""
        if check_github and gh_state and db_state and gh_state != db_state:
            stale_warning = f" [yellow](DB says {db_state})[/yellow]"

        console.print(f"[bold]PR #{pr_num}[/bold] {state_str}{stale_warning}")
        console.print(f"  {pr_url}")
        console.print(f"  Functions: {len(funcs)}")
        for func in funcs[:5]:
            pct = func.get("match_percent", 0)
            console.print(f"    - {func['function_name']} ({pct:.0f}%)")
        if len(funcs) > 5:
            console.print(f"    [dim]... and {len(funcs) - 5} more[/dim]")
        console.print()

    # Summary
    total_prs = len(by_pr)
    total_funcs = len(functions)
    merged = sum(1 for _, funcs in by_pr.items() if funcs[0].get("pr_state") == "MERGED")
    console.print(f"[dim]Total: {total_prs} PRs, {total_funcs} functions, {merged} merged[/dim]")


def refresh_prs_command(
    dry_run: Annotated[
        bool, typer.Option("--dry-run", help="Show what would be updated")
    ] = False,
    verbose: Annotated[
        bool, typer.Option("--verbose", "-v", help="Show detailed output")
    ] = False,
):
    """Refresh PR states from GitHub.

    Queries GitHub for current state of all PRs linked to functions and
    updates the database. Use this to fix stale or missing pr_state values.
    """
    db = get_db()

    # Get unique PRs from database
    with db.connection() as conn:
        cursor = conn.execute("""
            SELECT DISTINCT pr_url, pr_number, pr_state
            FROM functions
            WHERE pr_url IS NOT NULL
        """)
        prs = [dict(row) for row in cursor.fetchall()]

    if not prs:
        console.print("[dim]No PRs to refresh[/dim]")
        return

    console.print(f"[bold]Refreshing {len(prs)} PRs from GitHub...[/bold]\n")

    updated = 0
    errors = 0
    unchanged = 0

    for pr in prs:
        pr_url = pr["pr_url"]
        pr_number = pr.get("pr_number")
        old_state = pr.get("pr_state")

        repo, pr_num = extract_pr_info(pr_url)
        if not repo or not pr_num:
            if verbose:
                console.print(f"  [red]Invalid PR URL: {pr_url}[/red]")
            errors += 1
            continue

        gh_status = get_pr_status_from_gh(repo, pr_num)
        if not gh_status:
            if verbose:
                console.print(f"  [yellow]Could not fetch PR #{pr_num}[/yellow]")
            errors += 1
            continue

        new_state = gh_status.get("state")
        if not new_state:
            errors += 1
            continue

        if new_state == old_state:
            unchanged += 1
            if verbose:
                console.print(f"  PR #{pr_num}: {old_state} (unchanged)")
            continue

        # Update all functions with this PR
        if not dry_run:
            with db.connection() as conn:
                conn.execute("""
                    UPDATE functions
                    SET pr_state = ?, updated_at = ?
                    WHERE pr_url = ?
                """, (new_state, time.time(), pr_url))

        updated += 1
        old_display = old_state or "None"
        if new_state == "MERGED":
            console.print(f"  PR #{pr_num}: {old_display} -> [green]{new_state}[/green]")
        elif new_state == "CLOSED":
            console.print(f"  PR #{pr_num}: {old_display} -> [red]{new_state}[/red]")
        else:
            console.print(f"  PR #{pr_num}: {old_display} -> [cyan]{new_state}[/cyan]")

    console.print(f"\n[bold]Summary:[/bold]")
    console.print(f"  Updated: {updated}")
    console.print(f"  Unchanged: {unchanged}")
    if errors:
        console.print(f"  Errors: {errors}")

    if dry_run:
        console.print("\n[yellow](dry run - no changes made)[/yellow]")
