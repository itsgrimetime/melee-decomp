"""Check PR command."""

import json
from typing import Annotated

import typer

from .._common import console, load_slug_map, extract_pr_info
from ._helpers import (
    get_extended_pr_info,
    extract_functions_from_commits,
    validate_pr_description,
)


def check_command(
    pr_refs: Annotated[
        list[str], typer.Argument(help="PR number(s) or URL(s) to check (defaults to doldecomp/melee)")
    ],
    validate: Annotated[
        bool, typer.Option("--validate", "-v", help="Validate PR description")
    ] = True,
    output_json: Annotated[
        bool, typer.Option("--json", help="Output as JSON")
    ] = False,
):
    """Check PR status and validate description.

    Accepts PR numbers (defaults to doldecomp/melee) or full URLs:
        melee-agent pr check 2049
        melee-agent pr check 2049 2051 2052
        melee-agent pr check https://github.com/doldecomp/melee/pull/2049

    Shows:
    - PR state, review status, mergeability
    - Base and head branches
    - Functions mentioned in commits
    - Warnings if description has issues (local URLs, missing function names, etc.)
    """
    all_results = []
    for pr_ref in pr_refs:
        repo, pr_number = extract_pr_info(pr_ref)
        if not pr_number:
            console.print(f"[red]Invalid PR reference: {pr_ref}[/red]")
            console.print("[dim]Expected: PR number (e.g., 2049) or URL (e.g., https://github.com/owner/repo/pull/123)[/dim]")
            raise typer.Exit(1)

        result = _check_single_pr(repo, pr_number, pr_ref, validate, output_json)
        if result:
            all_results.append(result)

        # Add separator between multiple PRs (except for JSON output)
        if not output_json and len(pr_refs) > 1 and pr_ref != pr_refs[-1]:
            console.print("\n" + "─" * 60 + "\n")

    if output_json and len(all_results) > 1:
        print(json.dumps(all_results, indent=2))


def _check_single_pr(repo: str, pr_number: int, pr_ref: str, validate: bool, output_json: bool) -> dict | None:
    """Check a single PR and display/return results."""

    pr_info = get_extended_pr_info(repo, pr_number)
    if not pr_info:
        console.print("[red]Could not fetch PR info[/red]")
        console.print("[dim]Make sure 'gh' CLI is installed and authenticated[/dim]")
        raise typer.Exit(1)

    # Extract data
    state = pr_info.get("state", "unknown")
    is_draft = pr_info.get("isDraft", False)
    title = pr_info.get("title", "Unknown")
    body = pr_info.get("body", "")
    review = pr_info.get("reviewDecision", "PENDING")
    mergeable = pr_info.get("mergeable", "UNKNOWN")
    merge_state_status = pr_info.get("mergeStateStatus", "UNKNOWN")
    base_branch = pr_info.get("baseRefName", "?")
    head_branch = pr_info.get("headRefName", "?")
    commits = pr_info.get("commits", [])
    has_conflicts = mergeable == "CONFLICTING"

    # Extract functions from commits
    commit_functions = extract_functions_from_commits(commits)
    func_names = [f["function"] for f in commit_functions]

    # Validate description if requested
    slug_map = load_slug_map() if validate else {}
    warnings = validate_pr_description(body, func_names, slug_map) if validate else []

    output = {
        "pr_number": pr_number,
        "repo": repo,
        "url": f"https://github.com/{repo}/pull/{pr_number}",
        "title": title,
        "state": state,
        "is_draft": is_draft,
        "review": review,
        "mergeable": mergeable,
        "merge_state_status": merge_state_status,
        "has_conflicts": has_conflicts,
        "base_branch": base_branch,
        "head_branch": head_branch,
        "commit_count": len(commits),
        "functions": commit_functions,
        "warnings": warnings,
    }

    if output_json:
        print(json.dumps(output, indent=2))
        return output

    # Display PR info
    console.print(f"[bold]PR #{pr_number}[/bold]: {title}\n")

    # State
    if state == "MERGED":
        console.print("[green]Status: MERGED[/green]")
    elif state == "CLOSED":
        console.print("[red]Status: CLOSED[/red]")
    elif is_draft:
        console.print("[dim]Status: DRAFT[/dim]")
    else:
        console.print("[cyan]Status: OPEN[/cyan]")

    console.print(f"Review: {review or 'PENDING'}")
    if has_conflicts:
        console.print(f"[bold red]Mergeable: CONFLICTING - needs rebase/merge from {base_branch}[/bold red]")
    elif mergeable == "MERGEABLE":
        console.print(f"[green]Mergeable: {mergeable}[/green]")
    else:
        console.print(f"Mergeable: {mergeable}")

    # Branches
    console.print(f"\n[bold]Branches:[/bold]")
    console.print(f"  Base: {base_branch}")
    console.print(f"  Head: {head_branch}")

    # Commits and functions
    console.print(f"\n[bold]Commits:[/bold] {len(commits)}")
    if commit_functions:
        console.print(f"[bold]Functions matched:[/bold] {len(commit_functions)}")
        for func_info in commit_functions[:10]:
            console.print(f"  - {func_info['function']} [dim]({func_info['commit']})[/dim]")
        if len(commit_functions) > 10:
            console.print(f"  [dim]... and {len(commit_functions) - 10} more[/dim]")
    else:
        console.print("[dim]No Match commits found[/dim]")

    # Warnings
    if warnings:
        console.print(f"\n[bold yellow]⚠ Warnings ({len(warnings)}):[/bold yellow]")
        for warning in warnings:
            console.print(f"  [yellow]• {warning}[/yellow]")
    elif validate:
        console.print(f"\n[green]✓ Description looks good[/green]")

    return None
