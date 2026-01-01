"""List-related sync commands: list, slugs."""

import json
from typing import Annotated

import typer
from rich.table import Table

from .._common import console, load_slug_map


def list_command(
    min_match: Annotated[
        float, typer.Option("--min-match", help="Minimum match percentage to include")
    ] = 0.0,
    limit: Annotated[
        int, typer.Option("--limit", "-n", help="Maximum entries to show")
    ] = 50,
):
    """List functions that can be synced to production.

    Reads from the state database.
    """
    from src.db import get_db

    db = get_db()
    entries = []

    with db.connection() as conn:
        # Filter by min_match, but always exclude 0% (no progress)
        effective_min = max(min_match, 0.01)
        cursor = conn.execute("""
            SELECT function_name, match_percent, local_scratch_slug, production_scratch_slug
            FROM functions
            WHERE match_percent >= ?
            AND local_scratch_slug IS NOT NULL
            ORDER BY match_percent DESC
            LIMIT ?
        """, (effective_min, limit))

        for row in cursor.fetchall():
            entries.append({
                'name': row['function_name'],
                'match_pct': row['match_percent'] or 0,
                'slug': row['local_scratch_slug'],
                'synced': row['production_scratch_slug'] is not None,
            })

    if not entries:
        console.print("[yellow]No matching functions found[/yellow]")
        return

    title = "Functions to Sync" if min_match == 0 else f"Functions to Sync (>= {min_match}% match)"
    table = Table(title=title)
    table.add_column("Function", style="cyan")
    table.add_column("Match %", justify="right")
    table.add_column("Local Slug")
    table.add_column("Status")

    synced_count = 0
    for entry in entries:
        if entry['synced']:
            synced_count += 1
        table.add_row(
            entry['name'],
            f"{entry['match_pct']:.1f}%",
            entry['slug'],
            "[green]synced[/green]" if entry['synced'] else "[yellow]pending[/yellow]",
        )

    console.print(table)
    pending = len(entries) - synced_count
    console.print(f"\n[dim]Found {len(entries)} functions ({pending} pending, {synced_count} already synced)[/dim]")


def slugs_command(
    output_json: Annotated[
        bool, typer.Option("--json", help="Output as JSON")
    ] = False,
):
    """Show the local->production slug mapping."""
    slug_map = load_slug_map()

    if not slug_map:
        console.print("[yellow]No slug mappings found[/yellow]")
        console.print("[dim]Run 'sync production' to sync scratches and create mappings[/dim]")
        return

    if output_json:
        print(json.dumps(slug_map, indent=2))
    else:
        table = Table(title="Local -> Production Slug Mapping")
        table.add_column("Production Slug", style="cyan")
        table.add_column("Local Slug", style="dim")
        table.add_column("Function")
        table.add_column("Match %", justify="right")

        for prod_slug, info in sorted(slug_map.items(), key=lambda x: x[1].get('function', '')):
            table.add_row(
                prod_slug,
                info.get('local_slug', '?'),
                info.get('function', '?'),
                f"{info.get('match_percent', 0):.1f}%",
            )

        console.print(table)
        console.print(f"\n[dim]{len(slug_map)} mappings stored in database[/dim]")
