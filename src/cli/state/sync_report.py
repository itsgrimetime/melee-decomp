"""Sync report commands - sync state from report.json and symbols.txt."""

import time
from pathlib import Path
from typing import Annotated, Optional

import typer
from rich.table import Table

from .._common import console
from src.db import get_db
from src.extractor.report import ReportParser
from src.extractor.symbols import SymbolParser


def populate_addresses_command(
    melee_root: Annotated[
        Optional[Path], typer.Option("--melee-root", "-r", help="Path to melee repo")
    ] = None,
    source: Annotated[
        str, typer.Option("--source", "-s", help="Source: 'symbols' or 'report'")
    ] = "symbols",
    dry_run: Annotated[
        bool, typer.Option("--dry-run/--no-dry-run", help="Preview without making changes")
    ] = True,
    verbose: Annotated[
        bool, typer.Option("--verbose", "-v", help="Show detailed output")
    ] = False,
):
    """Populate canonical_address for existing functions.

    This command reads addresses from symbols.txt or report.json and updates
    the database with canonical_address values for all known functions.

    Run this after migrating to schema version 7 to backfill addresses.

    Sources:
    - symbols: Parse melee/config/GALE01/symbols.txt (recommended)
    - report: Parse melee/build/GALE01/report.json
    """
    # Find melee root
    if melee_root is None:
        melee_root = Path.cwd() / "melee"
        if not melee_root.exists():
            melee_root = Path.cwd()

    db = get_db()
    address_map: dict[str, str] = {}

    if source == "symbols":
        symbols_path = melee_root / "config" / "GALE01" / "symbols.txt"
        if not symbols_path.exists():
            console.print(f"[red]symbols.txt not found at {symbols_path}[/red]")
            raise typer.Exit(1)

        console.print(f"[dim]Parsing {symbols_path}...[/dim]")
        parser = SymbolParser(melee_root)
        symbols = parser.parse_symbols()

        for name, symbol in symbols.items():
            if symbol.address:
                address_map[name] = symbol.address

        console.print(f"[green]Found {len(address_map)} functions with addresses[/green]")

    elif source == "report":
        report_path = melee_root / "build" / "GALE01" / "report.json"
        if not report_path.exists():
            console.print(f"[red]report.json not found at {report_path}[/red]")
            console.print("[dim]Run 'ninja build/GALE01/report.json' first[/dim]")
            raise typer.Exit(1)

        console.print(f"[dim]Parsing {report_path}...[/dim]")
        parser = ReportParser(melee_root)
        matches = parser.get_function_matches()

        for name, match in matches.items():
            if match.address:
                address_map[name] = match.address

        console.print(f"[green]Found {len(address_map)} functions with addresses[/green]")

    else:
        console.print(f"[red]Unknown source: {source}[/red]")
        console.print("[dim]Use --source symbols or --source report[/dim]")
        raise typer.Exit(1)

    # Get current functions in database
    with db.connection() as conn:
        cursor = conn.execute(
            "SELECT function_name, canonical_address FROM functions"
        )
        db_functions = {row['function_name']: row['canonical_address'] for row in cursor.fetchall()}

    # Find functions to update
    updates_needed = {}
    for func_name, address in address_map.items():
        if func_name in db_functions:
            current_addr = db_functions[func_name]
            # Normalize for comparison
            normalized = db._normalize_address(address)
            if current_addr != normalized:
                updates_needed[func_name] = address

    if not updates_needed:
        console.print("[green]All tracked functions already have addresses[/green]")
        return

    console.print(f"\n[bold]Functions to update: {len(updates_needed)}[/bold]")

    if verbose:
        table = Table(title="Address Updates")
        table.add_column("Function", style="cyan")
        table.add_column("Current", style="yellow")
        table.add_column("New Address", style="green")

        for func_name in sorted(updates_needed.keys())[:50]:
            current = db_functions.get(func_name, "")
            new_addr = db._normalize_address(updates_needed[func_name])
            table.add_row(func_name, current or "(none)", new_addr or "")

        if len(updates_needed) > 50:
            table.add_row("...", f"({len(updates_needed) - 50} more)", "...")

        console.print(table)

    if dry_run:
        console.print("\n[yellow]Dry run - no changes made[/yellow]")
        console.print("[dim]Use --no-dry-run to apply changes[/dim]")
        return

    # Apply updates
    updated = db.bulk_update_addresses(updates_needed)
    console.print(f"\n[green]Updated {updated} functions with addresses[/green]")


def sync_report_command(
    melee_root: Annotated[
        Optional[Path], typer.Option("--melee-root", "-r", help="Path to melee repo")
    ] = None,
    source: Annotated[
        str, typer.Option("--source", "-s", help="Source: 'local' or 'upstream'")
    ] = "local",
    detect_renames: Annotated[
        bool, typer.Option("--detect-renames/--no-detect-renames", help="Detect renamed functions via address")
    ] = True,
    update_status: Annotated[
        bool, typer.Option("--update-status/--no-update-status", help="Update function status based on match %")
    ] = True,
    dry_run: Annotated[
        bool, typer.Option("--dry-run/--no-dry-run", help="Preview without making changes")
    ] = True,
    verbose: Annotated[
        bool, typer.Option("--verbose", "-v", help="Show detailed output")
    ] = False,
    output_json: Annotated[
        bool, typer.Option("--json", help="Output as JSON")
    ] = False,
):
    """Sync function match percentages from report.json.

    This command updates the state database with current match percentages
    from report.json. It can also detect renamed functions by matching
    on canonical_address.

    Sources:
    - local: Use current build/GALE01/report.json
    - upstream: Checkout upstream/master and build report (slow)

    The sync process:
    1. Parse report.json for function match percentages
    2. For each function, look up by name first
    3. If not found by name but address exists, look up by address (rename detection)
    4. Update match_percent in database
    5. Optionally update status based on match %
    """
    import json as json_module

    # Find melee root
    if melee_root is None:
        melee_root = Path.cwd() / "melee"
        if not melee_root.exists():
            melee_root = Path.cwd()

    db = get_db()

    # Parse report.json
    if source == "local":
        report_path = melee_root / "build" / "GALE01" / "report.json"
        if not report_path.exists():
            console.print(f"[red]report.json not found at {report_path}[/red]")
            console.print("[dim]Run 'ninja build/GALE01/report.json' first[/dim]")
            raise typer.Exit(1)

        console.print(f"[dim]Parsing {report_path}...[/dim]")
        parser = ReportParser(melee_root)

    elif source == "upstream":
        console.print("[yellow]Upstream sync not yet implemented[/yellow]")
        console.print("[dim]Use --source local for now[/dim]")
        raise typer.Exit(1)

    else:
        console.print(f"[red]Unknown source: {source}[/red]")
        raise typer.Exit(1)

    # Get function matches from report
    report_matches = parser.get_function_matches()
    console.print(f"[green]Found {len(report_matches)} functions in report.json[/green]")

    # Get current database state
    with db.connection() as conn:
        cursor = conn.execute(
            """
            SELECT function_name, canonical_address, match_percent, status
            FROM functions
            """
        )
        db_functions = {row['function_name']: dict(row) for row in cursor.fetchall()}

    # Build address-to-name lookup from database
    addr_to_name: dict[str, str] = {}
    for func_name, func_data in db_functions.items():
        addr = func_data.get('canonical_address')
        if addr:
            addr_to_name[addr] = func_name

    # Track changes
    changes = {
        'match_updates': [],       # (name, old_pct, new_pct)
        'status_updates': [],      # (name, old_status, new_status)
        'renames_detected': [],    # (old_name, new_name, address)
        'new_functions': [],       # names not in DB
        'missing_in_report': [],   # in DB but not in report
    }

    # Process each function in report
    for report_name, match in report_matches.items():
        report_pct = match.fuzzy_match_percent
        report_addr = match.address

        # Try to find in database
        db_func = db_functions.get(report_name)

        if db_func:
            # Found by name - check for match % update
            db_pct = db_func.get('match_percent', 0.0) or 0.0
            if abs(db_pct - report_pct) > 0.01:  # Significant difference
                changes['match_updates'].append((report_name, db_pct, report_pct))

            # Check status update
            if update_status:
                current_status = db_func.get('status', 'unclaimed')
                new_status = _determine_status(report_pct, current_status, db_func)
                if new_status and new_status != current_status:
                    changes['status_updates'].append((report_name, current_status, new_status))

        elif detect_renames and report_addr:
            # Not found by name - try address lookup
            normalized_addr = db._normalize_address(report_addr)
            if normalized_addr and normalized_addr in addr_to_name:
                old_name = addr_to_name[normalized_addr]
                changes['renames_detected'].append((old_name, report_name, normalized_addr))
        else:
            # New function not in database
            changes['new_functions'].append(report_name)

    # Find functions in DB but not in report
    report_names = set(report_matches.keys())
    for db_name in db_functions.keys():
        if db_name not in report_names:
            changes['missing_in_report'].append(db_name)

    # Output results
    if output_json:
        print(json_module.dumps(changes, indent=2))
        return

    # Summary
    console.print("\n[bold]Sync Summary[/bold]")
    console.print(f"  Match % updates:    {len(changes['match_updates'])}")
    console.print(f"  Status updates:     {len(changes['status_updates'])}")
    console.print(f"  Renames detected:   {len(changes['renames_detected'])}")
    console.print(f"  New functions:      {len(changes['new_functions'])}")
    console.print(f"  Missing in report:  {len(changes['missing_in_report'])}")

    if verbose:
        # Show match updates
        if changes['match_updates']:
            console.print("\n[bold]Match % Updates:[/bold]")
            table = Table()
            table.add_column("Function", style="cyan")
            table.add_column("Old %", style="yellow", justify="right")
            table.add_column("New %", style="green", justify="right")

            for name, old_pct, new_pct in sorted(changes['match_updates'], key=lambda x: x[2] - x[1], reverse=True)[:20]:
                table.add_row(name, f"{old_pct:.1f}", f"{new_pct:.1f}")

            if len(changes['match_updates']) > 20:
                table.add_row("...", f"({len(changes['match_updates']) - 20} more)", "")

            console.print(table)

        # Show renames
        if changes['renames_detected']:
            console.print("\n[bold]Renames Detected:[/bold]")
            table = Table()
            table.add_column("Old Name", style="yellow")
            table.add_column("New Name", style="green")
            table.add_column("Address", style="dim")

            for old_name, new_name, addr in changes['renames_detected'][:20]:
                table.add_row(old_name, new_name, addr)

            console.print(table)

    if dry_run:
        console.print("\n[yellow]Dry run - no changes made[/yellow]")
        console.print("[dim]Use --no-dry-run to apply changes[/dim]")
        return

    # Apply changes
    now = time.time()
    applied = {'match': 0, 'status': 0, 'renames': 0}

    with db.transaction() as conn:
        # Apply match % updates
        for name, old_pct, new_pct in changes['match_updates']:
            conn.execute(
                """
                UPDATE functions
                SET match_percent = ?, updated_at = ?
                WHERE function_name = ?
                """,
                (new_pct, now, name)
            )
            applied['match'] += 1

        # Apply status updates
        for name, old_status, new_status in changes['status_updates']:
            conn.execute(
                """
                UPDATE functions
                SET status = ?, updated_at = ?
                WHERE function_name = ?
                """,
                (new_status, now, name)
            )
            applied['status'] += 1

    # Handle renames (needs merge logic)
    for old_name, new_name, addr in changes['renames_detected']:
        if db.merge_function_records(old_name, new_name, addr):
            applied['renames'] += 1

    console.print(f"\n[green]Applied changes:[/green]")
    console.print(f"  Match % updates:  {applied['match']}")
    console.print(f"  Status updates:   {applied['status']}")
    console.print(f"  Renames merged:   {applied['renames']}")


def _determine_status(
    match_pct: float,
    current_status: str,
    func_data: dict,
) -> Optional[str]:
    """Determine the appropriate status based on match percentage.

    Args:
        match_pct: Current match percentage from report
        current_status: Current status in database
        func_data: Full function record

    Returns:
        New status or None if no change needed
    """
    # Don't downgrade merged/committed/in_review
    protected_statuses = {'merged', 'committed', 'committed_needs_fix', 'in_review'}
    if current_status in protected_statuses:
        # But if merged and match dropped significantly, something is wrong
        if current_status == 'merged' and match_pct < 95:
            return None  # Don't auto-change, needs manual review
        return None

    # Update based on match %
    if match_pct >= 100.0:
        # 100% match - might be merged
        if func_data.get('pr_state') == 'MERGED':
            return 'merged'
        elif func_data.get('is_committed'):
            return 'committed'
        else:
            return 'matched'
    elif match_pct >= 95.0:
        return 'matched'
    elif match_pct > 0:
        return 'in_progress'
    else:
        return 'unclaimed'
