"""Status-related PR commands: status, list."""

import json
from typing import Annotated, Optional

import typer
from rich.table import Table

from .._common import (
    console,
    load_completed_functions,
    extract_pr_info,
    get_pr_status_from_gh,
)


def status_command(
    check_github: Annotated[
        bool, typer.Option("--check", "-c", help="Check actual PR status via gh CLI")
    ] = False,
):
    """Show PR status summary for all tracked functions."""
    completed = load_completed_functions()

    by_pr: dict[str, list[tuple[str, dict]]] = {}
    no_pr = []

    for func, info in completed.items():
        # Skip functions already in upstream (not our work)
        if info.get("already_in_upstream"):
            continue
        pr_url = info.get("pr_url")
        if pr_url:
            if pr_url not in by_pr:
                by_pr[pr_url] = []
            by_pr[pr_url].append((func, info))
        elif info.get("match_percent", 0) >= 95:
            no_pr.append((func, info))

    console.print("[bold]PR Tracking Status[/bold]\n")

    if by_pr:
        for pr_url, funcs in sorted(by_pr.items()):
            repo, pr_num = extract_pr_info(pr_url)

            status_str = ""
            if check_github and repo and pr_num:
                gh_status = get_pr_status_from_gh(repo, pr_num)
                if gh_status:
                    state = gh_status.get("state", "unknown")
                    is_draft = gh_status.get("isDraft", False)
                    review = gh_status.get("reviewDecision", "")

                    if state == "MERGED":
                        status_str = " [green]MERGED[/green]"
                    elif state == "CLOSED":
                        status_str = " [red]CLOSED[/red]"
                    elif is_draft:
                        status_str = " [dim]DRAFT[/dim]"
                    elif review == "APPROVED":
                        status_str = " [green]APPROVED[/green]"
                    elif review == "CHANGES_REQUESTED":
                        status_str = " [yellow]CHANGES REQUESTED[/yellow]"
                    else:
                        status_str = " [cyan]OPEN[/cyan]"

            # Check if all functions share the same branch
            branches = set(info.get("branch") for _, info in funcs if info.get("branch"))
            branch_str = ""
            if len(branches) == 1:
                branch_str = f" [dim]branch: {list(branches)[0]}[/dim]"

            console.print(f"[bold]PR #{pr_num}[/bold]{status_str}")
            console.print(f"  {pr_url}{branch_str}")
            console.print(f"  Functions: {len(funcs)}")
            for func, info in funcs[:5]:
                pct = info.get("match_percent", 0)
                console.print(f"    - {func} ({pct}%)")
            if len(funcs) > 5:
                console.print(f"    [dim]... and {len(funcs) - 5} more[/dim]")
            console.print()

    if no_pr:
        console.print(f"[yellow]Not linked to any PR ({len(no_pr)} functions at 95%+):[/yellow]")
        for func, info in sorted(no_pr, key=lambda x: -x[1].get("match_percent", 0))[:10]:
            pct = info.get("match_percent", 0)
            console.print(f"  {func}: {pct}%")
        if len(no_pr) > 10:
            console.print(f"  [dim]... and {len(no_pr) - 10} more[/dim]")
        console.print("\n[dim]Link with: melee-agent pr link <pr_url> <function>...[/dim]")

    if not by_pr and not no_pr:
        console.print("[dim]No functions tracked yet[/dim]")


def list_command(
    pr_url: Annotated[
        Optional[str], typer.Argument(help="Filter by PR URL (optional)")
    ] = None,
    no_pr: Annotated[
        bool, typer.Option("--no-pr", help="Show only functions without a PR")
    ] = False,
    output_json: Annotated[
        bool, typer.Option("--json", help="Output as JSON")
    ] = False,
):
    """List functions by PR association."""
    completed = load_completed_functions()

    results = []
    for func, info in completed.items():
        func_pr = info.get("pr_url", "")

        if no_pr and func_pr:
            continue
        if pr_url and func_pr != pr_url:
            continue
        if not no_pr and not pr_url and not func_pr:
            continue

        results.append({
            "function": func,
            "match_percent": info.get("match_percent", 0),
            "pr_url": func_pr,
            "pr_number": info.get("pr_number", 0),
            "scratch_slug": info.get("scratch_slug", ""),
        })

    results.sort(key=lambda x: -x["match_percent"])

    if output_json:
        print(json.dumps(results, indent=2))
        return

    if not results:
        if no_pr:
            console.print("[green]All 95%+ functions are linked to PRs[/green]")
        else:
            console.print("[dim]No matching functions[/dim]")
        return

    table = Table(title="Functions" + (f" for PR" if pr_url else " without PR" if no_pr else ""))
    table.add_column("Function", style="cyan")
    table.add_column("Match %", justify="right")
    table.add_column("PR #", justify="right")
    table.add_column("Slug")

    for r in results[:50]:
        table.add_row(
            r["function"],
            f"{r['match_percent']:.1f}%",
            str(r["pr_number"]) if r["pr_number"] else "-",
            r["scratch_slug"] or "-"
        )

    console.print(table)
    if len(results) > 50:
        console.print(f"[dim]... and {len(results) - 50} more[/dim]")
