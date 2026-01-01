"""Link-related PR commands: link, link-batch, unlink."""

from pathlib import Path
from typing import Annotated

import typer

from .._common import (
    console,
    DEFAULT_MELEE_ROOT,
    load_completed_functions,
    save_completed_functions,
    load_all_tracking_data,
    categorize_functions,
    extract_pr_info,
    db_upsert_function,
)


def link_command(
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
        # Also update state database
        for func in linked:
            db_upsert_function(func, pr_url=pr_url, pr_number=pr_number, status='in_review')
        console.print(f"[green]Linked {len(linked)} functions to PR #{pr_number}[/green]")
        for func in linked:
            console.print(f"  {func}")

    if not_found:
        console.print(f"\n[yellow]Not found in tracking ({len(not_found)}):[/yellow]")
        for func in not_found:
            console.print(f"  {func}")


def link_batch_command(
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

    linked_funcs = []
    for entry in entries:
        func = entry["function"]
        if func in completed:
            completed[func]["pr_url"] = pr_url
            completed[func]["pr_number"] = pr_number
            completed[func]["pr_repo"] = repo
            linked_funcs.append(func)
            linked += 1

    save_completed_functions(completed)
    # Also update state database
    for func in linked_funcs:
        db_upsert_function(func, pr_url=pr_url, pr_number=pr_number, status='in_review')
    console.print(f"[green]Linked {linked} functions to PR #{pr_number}[/green]")


def unlink_command(
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
        # Also update state database - clear PR fields
        for func in unlinked:
            db_upsert_function(func, pr_url=None, pr_number=None, pr_state=None)
        console.print(f"[green]Unlinked {len(unlinked)} functions[/green]")
