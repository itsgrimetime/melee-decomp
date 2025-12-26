"""PR commands - track functions through PR lifecycle."""

import json
from pathlib import Path
from typing import Annotated, Optional

import typer
from rich.table import Table

from ._common import (
    console,
    DEFAULT_MELEE_ROOT,
    load_completed_functions,
    save_completed_functions,
    load_all_tracking_data,
    categorize_functions,
    extract_pr_info,
    get_pr_status_from_gh,
)

pr_app = typer.Typer(help="Track functions through PR lifecycle")


@pr_app.command("link")
def pr_link(
    pr_url: Annotated[
        str, typer.Argument(help="GitHub PR URL")
    ],
    functions: Annotated[
        list[str], typer.Argument(help="Function names to link")
    ],
):
    """Link functions to a GitHub PR.

    Example: melee-agent pr link https://github.com/doldecomp/melee/pull/123 func1 func2
    """
    repo, pr_number = extract_pr_info(pr_url)
    if not pr_number:
        console.print(f"[red]Invalid PR URL: {pr_url}[/red]")
        console.print("[dim]Expected format: https://github.com/owner/repo/pull/123[/dim]")
        raise typer.Exit(1)

    completed = load_completed_functions()
    linked = []
    not_found = []

    for func in functions:
        if func in completed:
            completed[func]["pr_url"] = pr_url
            completed[func]["pr_number"] = pr_number
            completed[func]["pr_repo"] = repo
            linked.append(func)
        else:
            not_found.append(func)

    if linked:
        save_completed_functions(completed)
        console.print(f"[green]Linked {len(linked)} functions to PR #{pr_number}[/green]")
        for func in linked:
            console.print(f"  {func}")

    if not_found:
        console.print(f"\n[yellow]Not found in tracking ({len(not_found)}):[/yellow]")
        for func in not_found:
            console.print(f"  {func}")


@pr_app.command("link-batch")
def pr_link_batch(
    pr_url: Annotated[
        str, typer.Argument(help="GitHub PR URL")
    ],
    category: Annotated[
        str, typer.Option("--category", "-c", help="Link all functions in category: complete, synced")
    ] = "complete",
    melee_root: Annotated[
        Path, typer.Option("--melee-root", "-m", help="Path to melee submodule")
    ] = DEFAULT_MELEE_ROOT,
):
    """Link all functions in a category to a PR.

    Example: melee-agent pr link-batch https://github.com/doldecomp/melee/pull/123 --category complete
    """
    repo, pr_number = extract_pr_info(pr_url)
    if not pr_number:
        console.print(f"[red]Invalid PR URL: {pr_url}[/red]")
        raise typer.Exit(1)

    data = load_all_tracking_data(melee_root)
    categories = categorize_functions(data)

    cat_map = {"complete": "complete", "synced": "synced_not_in_file"}
    if category not in cat_map:
        console.print(f"[red]Invalid category: {category}[/red]")
        console.print("Valid: complete, synced")
        raise typer.Exit(1)

    entries = categories[cat_map[category]]
    if not entries:
        console.print(f"[yellow]No functions in category '{category}'[/yellow]")
        return

    completed = load_completed_functions()
    linked = 0

    for entry in entries:
        func = entry["function"]
        if func in completed:
            completed[func]["pr_url"] = pr_url
            completed[func]["pr_number"] = pr_number
            completed[func]["pr_repo"] = repo
            linked += 1

    save_completed_functions(completed)
    console.print(f"[green]Linked {linked} functions to PR #{pr_number}[/green]")


@pr_app.command("unlink")
def pr_unlink(
    functions: Annotated[
        list[str], typer.Argument(help="Function names to unlink")
    ],
):
    """Remove PR association from functions."""
    completed = load_completed_functions()
    unlinked = []

    for func in functions:
        if func in completed and "pr_url" in completed[func]:
            del completed[func]["pr_url"]
            if "pr_number" in completed[func]:
                del completed[func]["pr_number"]
            if "pr_repo" in completed[func]:
                del completed[func]["pr_repo"]
            unlinked.append(func)

    if unlinked:
        save_completed_functions(completed)
        console.print(f"[green]Unlinked {len(unlinked)} functions[/green]")


@pr_app.command("status")
def pr_status(
    check_github: Annotated[
        bool, typer.Option("--check", "-c", help="Check actual PR status via gh CLI")
    ] = False,
):
    """Show PR status summary for all tracked functions."""
    completed = load_completed_functions()

    by_pr: dict[str, list[tuple[str, dict]]] = {}
    no_pr = []

    for func, info in completed.items():
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

            console.print(f"[bold]PR #{pr_num}[/bold]{status_str}")
            console.print(f"  [dim]{pr_url}[/dim]")
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


@pr_app.command("list")
def pr_list(
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


@pr_app.command("check")
def pr_check(
    pr_url: Annotated[
        str, typer.Argument(help="GitHub PR URL to check")
    ],
):
    """Check PR status using gh CLI."""
    repo, pr_number = extract_pr_info(pr_url)
    if not pr_number:
        console.print(f"[red]Invalid PR URL: {pr_url}[/red]")
        raise typer.Exit(1)

    status = get_pr_status_from_gh(repo, pr_number)
    if not status:
        console.print("[red]Could not fetch PR status[/red]")
        console.print("[dim]Make sure 'gh' CLI is installed and authenticated[/dim]")
        raise typer.Exit(1)

    console.print(f"[bold]PR #{pr_number}[/bold]: {status.get('title', 'Unknown')}\n")

    state = status.get("state", "unknown")
    is_draft = status.get("isDraft", False)
    review = status.get("reviewDecision", "PENDING")
    mergeable = status.get("mergeable", "UNKNOWN")

    if state == "MERGED":
        console.print("[green]Status: MERGED[/green]")
    elif state == "CLOSED":
        console.print("[red]Status: CLOSED[/red]")
    elif is_draft:
        console.print("[dim]Status: DRAFT[/dim]")
    else:
        console.print("[cyan]Status: OPEN[/cyan]")

    console.print(f"Review: {review}")
    console.print(f"Mergeable: {mergeable}")
