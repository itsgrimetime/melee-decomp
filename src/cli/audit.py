"""Audit commands - audit and recover tracked work."""

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Annotated

import typer
from rich.table import Table

from ._common import (
    console,
    DEFAULT_MELEE_ROOT,
    load_all_tracking_data,
    categorize_functions,
)

audit_app = typer.Typer(help="Audit and recover tracked work")


@audit_app.command("status")
def audit_status(
    melee_root: Annotated[
        Path, typer.Option("--melee-root", "-m", help="Path to melee submodule")
    ] = DEFAULT_MELEE_ROOT,
    verbose: Annotated[
        bool, typer.Option("--verbose", "-v", help="Show all entries")
    ] = False,
    output_json: Annotated[
        bool, typer.Option("--json", help="Output as JSON")
    ] = False,
):
    """Show unified status of all tracked work.

    Categories:
    - Complete: Synced to production AND in scratches.txt
    - Synced but missing: Synced to production but not in scratches.txt (needs re-add)
    - Lost: 95%+ match but not synced or tracked (needs recovery)
    - Work in progress: <95% match, still being worked on
    """
    data = load_all_tracking_data(melee_root)
    categories = categorize_functions(data)

    if output_json:
        # Convert sets to lists for JSON serialization
        serializable = {}
        for key, value in categories.items():
            serializable[key] = value
        print(json.dumps(serializable, indent=2))
        return

    console.print("[bold]Tracking Audit Summary[/bold]\n")

    table = Table(title="Status Overview")
    table.add_column("Status", style="bold")
    table.add_column("Count", justify="right")
    table.add_column("Action Needed")

    table.add_row(
        "[green]✅ Complete[/green]",
        str(len(categories["complete"])),
        "None - ready for PR"
    )
    table.add_row(
        "[yellow]⚠️ Synced but not in file[/yellow]",
        str(len(categories["synced_not_in_file"])),
        "Run: audit recover --add-to-file"
    )
    table.add_row(
        "[yellow]⚠️ In file, not synced[/yellow]",
        str(len(categories["in_file_not_synced"])),
        "Run: sync production"
    )
    table.add_row(
        "[red]❌ Lost (95%+)[/red]",
        str(len(categories["lost_high_match"])),
        "Run: audit recover --sync-lost"
    )
    table.add_row(
        "[dim]📝 Work in progress[/dim]",
        str(len(categories["work_in_progress"])),
        "Continue matching"
    )

    console.print(table)

    if verbose or len(categories["lost_high_match"]) > 0:
        if categories["lost_high_match"]:
            console.print("\n[red bold]Lost matches needing recovery:[/red bold]")
            for entry in categories["lost_high_match"][:10]:
                console.print(f"  {entry['function']}: {entry['match_percent']}% (local:{entry['local_slug']})")
            if len(categories["lost_high_match"]) > 10:
                console.print(f"  [dim]... and {len(categories['lost_high_match']) - 10} more[/dim]")

    if verbose and categories["synced_not_in_file"]:
        console.print("\n[yellow bold]Synced but missing from scratches.txt:[/yellow bold]")
        for entry in categories["synced_not_in_file"][:10]:
            console.print(f"  {entry['function']}: {entry['match_percent']}% (prod:{entry['production_slug']})")
        if len(categories["synced_not_in_file"]) > 10:
            console.print(f"  [dim]... and {len(categories['synced_not_in_file']) - 10} more[/dim]")


@audit_app.command("recover")
def audit_recover(
    melee_root: Annotated[
        Path, typer.Option("--melee-root", "-m", help="Path to melee submodule")
    ] = DEFAULT_MELEE_ROOT,
    add_to_file: Annotated[
        bool, typer.Option("--add-to-file", help="Add synced functions to scratches.txt")
    ] = False,
    sync_lost: Annotated[
        bool, typer.Option("--sync-lost", help="Sync lost scratches to production")
    ] = False,
    dry_run: Annotated[
        bool, typer.Option("--dry-run", help="Show what would be done")
    ] = False,
    limit: Annotated[
        int, typer.Option("--limit", "-n", help="Maximum entries to process")
    ] = 20,
):
    """Recover lost or missing tracking entries.

    --add-to-file: Add entries for synced functions that are missing from scratches.txt
    --sync-lost: Sync 95%+ local scratches to production decomp.me
    """
    data = load_all_tracking_data(melee_root)
    categories = categorize_functions(data)

    if add_to_file:
        entries = categories["synced_not_in_file"][:limit]
        if not entries:
            console.print("[green]No synced functions missing from scratches.txt[/green]")
            return

        console.print(f"[bold]Adding {len(entries)} entries to scratches.txt[/bold]\n")

        scratches_file = melee_root / "config" / "GALE01" / "scratches.txt"
        now = datetime.now(timezone.utc).isoformat().replace('+00:00', 'Z')

        lines_to_add = []
        for entry in entries:
            prod_slug = entry["production_slug"]
            func = entry["function"]
            pct = entry["match_percent"]

            pct_str = "100%" if pct == 100 else f"{pct:.1f}%"
            line = f"{func} = {pct_str}:MATCHED; // author:agent id:{prod_slug} updated:{now} created:{now}"
            lines_to_add.append(line)

            if dry_run:
                console.print(f"  [dim]Would add:[/dim] {func} (id:{prod_slug})")
            else:
                console.print(f"  [green]Adding:[/green] {func} (id:{prod_slug})")

        if not dry_run:
            with open(scratches_file, 'a') as f:
                f.write("\n" + "\n".join(lines_to_add) + "\n")
            console.print(f"\n[green]Added {len(lines_to_add)} entries to scratches.txt[/green]")
        else:
            console.print(f"\n[cyan]Would add {len(lines_to_add)} entries (dry run)[/cyan]")

    if sync_lost:
        entries = categories["lost_high_match"][:limit]
        if not entries:
            console.print("[green]No lost scratches to sync[/green]")
            return

        console.print(f"[bold]Found {len(entries)} lost scratches to sync[/bold]\n")

        if dry_run:
            for entry in entries:
                console.print(f"  [dim]Would sync:[/dim] {entry['function']} ({entry['match_percent']}%) local:{entry['local_slug']}")
            console.print(f"\n[cyan]Would sync {len(entries)} scratches (dry run)[/cyan]")
            console.print("[dim]Run 'melee-agent sync production' after recovery to push to production[/dim]")
        else:
            console.print("[yellow]Lost scratch recovery requires manual steps:[/yellow]")
            console.print("1. Verify scratches exist on local instance")
            console.print("2. Run: melee-agent sync production --author agent")
            console.print("3. Run: melee-agent audit recover --add-to-file")

    if not add_to_file and not sync_lost:
        console.print("[yellow]Specify --add-to-file or --sync-lost[/yellow]")
        console.print("\nRun 'melee-agent audit status' to see what needs recovery")


@audit_app.command("list")
def audit_list(
    category: Annotated[
        str, typer.Argument(help="Category: complete, synced, lost, wip, all")
    ] = "all",
    melee_root: Annotated[
        Path, typer.Option("--melee-root", "-m", help="Path to melee submodule")
    ] = DEFAULT_MELEE_ROOT,
    min_match: Annotated[
        float, typer.Option("--min-match", help="Minimum match percentage")
    ] = 0.0,
    output_json: Annotated[
        bool, typer.Option("--json", help="Output as JSON")
    ] = False,
):
    """List tracked functions by category.

    Categories: complete, synced, lost, wip (work in progress), all
    """
    data = load_all_tracking_data(melee_root)
    categories = categorize_functions(data)

    cat_map = {
        "complete": "complete",
        "synced": "synced_not_in_file",
        "lost": "lost_high_match",
        "wip": "work_in_progress",
    }

    if category == "all":
        entries = []
        for cat_entries in categories.values():
            entries.extend(cat_entries)
    elif category in cat_map:
        entries = categories[cat_map[category]]
    else:
        console.print(f"[red]Unknown category: {category}[/red]")
        console.print("Valid: complete, synced, lost, wip, all")
        raise typer.Exit(1)

    entries = [e for e in entries if e["match_percent"] >= min_match]
    entries.sort(key=lambda x: -x["match_percent"])

    if output_json:
        print(json.dumps(entries, indent=2))
        return

    table = Table(title=f"Functions: {category}")
    table.add_column("Function", style="cyan")
    table.add_column("Match %", justify="right")
    table.add_column("Local Slug")
    table.add_column("Prod Slug")
    table.add_column("Notes", style="dim")

    for entry in entries[:50]:
        table.add_row(
            entry["function"],
            f"{entry['match_percent']:.1f}%",
            entry["local_slug"] or "-",
            entry["production_slug"] or "-",
            entry["notes"][:30] if entry["notes"] else ""
        )

    console.print(table)
    if len(entries) > 50:
        console.print(f"[dim]... and {len(entries) - 50} more[/dim]")
