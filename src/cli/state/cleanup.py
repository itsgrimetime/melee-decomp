"""Cleanup-related state commands: cleanup, rebuild, export."""

import asyncio
import json
import os
import time
from pathlib import Path
from typing import Annotated, Any

import typer

from .._common import (
    AGENT_ID,
    DECOMP_CONFIG_DIR,
    PRODUCTION_DECOMP_ME,
    console,
    detect_local_api_url,
    load_completed_functions,
    load_slug_map,
)
from src.db import get_db


def cleanup_command(
    remove_recovered: Annotated[
        bool, typer.Option("--remove-recovered", help="Remove bulk-imported entries (from scratches.txt)")
    ] = False,
    remove_no_scratch: Annotated[
        bool, typer.Option("--remove-no-scratch", help="Remove all entries without a scratch slug")
    ] = False,
    verify_server: Annotated[
        bool, typer.Option("--verify-server", help="Verify scratches exist on server (requires API access)")
    ] = False,
    limit: Annotated[
        int, typer.Option("--limit", "-n", help="Limit entries to check for --verify-server")
    ] = 100,
    dry_run: Annotated[
        bool, typer.Option("--dry-run/--no-dry-run", help="Show what would be removed without actually removing")
    ] = True,
    output_json: Annotated[
        bool, typer.Option("--json", help="Output as JSON")
    ] = False,
):
    """Identify and clean up orphaned tracking entries.

    Shows entries that appear to be bulk-imported or have no actual scratch:
    - "recovered" entries: bulk-imported from some recovery process
    - No scratch slug: tracked but never worked on

    Use --remove-recovered or --remove-no-scratch with --no-dry-run to clean up.
    """
    db = get_db()

    # Analyze the database
    with db.connection() as conn:
        # Count by notes type
        cursor = conn.execute("""
            SELECT
                CASE
                    WHEN notes LIKE 'Recovered from scratches%' THEN 'recovered_scratches'
                    WHEN notes LIKE 'Recovered from slug_map%' THEN 'recovered_slug_map'
                    WHEN notes LIKE 'committed%' THEN 'committed'
                    WHEN notes IS NULL OR notes = '' THEN 'no_notes'
                    ELSE 'has_notes'
                END as note_type,
                COUNT(*) as cnt
            FROM functions
            GROUP BY note_type
        """)
        by_notes = {row['note_type']: row['cnt'] for row in cursor.fetchall()}

        # Count with/without scratches
        cursor = conn.execute("""
            SELECT
                CASE
                    WHEN local_scratch_slug IS NOT NULL AND local_scratch_slug != '' THEN 'has_scratch'
                    ELSE 'no_scratch'
                END as scratch_status,
                COUNT(*) as cnt
            FROM functions
            GROUP BY scratch_status
        """)
        by_scratch = {row['scratch_status']: row['cnt'] for row in cursor.fetchall()}

        # Get recovered entries with no scratch (from scratches.txt)
        cursor = conn.execute("""
            SELECT function_name, notes, match_percent, is_committed, pr_state
            FROM functions
            WHERE notes LIKE 'Recovered from scratches%'
            AND (local_scratch_slug IS NULL OR local_scratch_slug = '')
        """)
        recovered_no_scratch = [dict(row) for row in cursor.fetchall()]

        # Get recovered from scratches.txt (all, even with slugs - for analysis)
        cursor = conn.execute("""
            SELECT function_name, notes, match_percent, local_scratch_slug
            FROM functions
            WHERE notes LIKE 'Recovered from scratches%'
        """)
        all_recovered_scratches = [dict(row) for row in cursor.fetchall()]

        # Get all entries with no scratch
        cursor = conn.execute("""
            SELECT function_name, notes, match_percent, is_committed, pr_state
            FROM functions
            WHERE (local_scratch_slug IS NULL OR local_scratch_slug = '')
        """)
        all_no_scratch = [dict(row) for row in cursor.fetchall()]

        # Get total
        cursor = conn.execute("SELECT COUNT(*) as cnt FROM functions")
        total = cursor.fetchone()['cnt']

    if output_json:
        print(json.dumps({
            'total': total,
            'by_notes': by_notes,
            'by_scratch': by_scratch,
            'recovered_no_scratch_count': len(recovered_no_scratch),
            'all_no_scratch_count': len(all_no_scratch),
            'recovered_scratches_total': len(all_recovered_scratches),
        }, indent=2))
        return

    console.print("[bold]Tracking Entry Analysis[/bold]\n")
    console.print(f"Total entries: {total}\n")

    console.print("[bold]By source (notes type):[/bold]")
    for note_type, cnt in sorted(by_notes.items(), key=lambda x: -x[1]):
        pct = cnt / total * 100 if total > 0 else 0
        if note_type == 'recovered_scratches':
            console.print(f"  [yellow]{note_type}[/yellow]: {cnt} ({pct:.1f}%) - bulk import from scratches.txt")
        elif note_type == 'recovered_slug_map':
            console.print(f"  [green]{note_type}[/green]: {cnt} ({pct:.1f}%) - synced to production")
        elif note_type == 'committed':
            console.print(f"  [green]{note_type}[/green]: {cnt} ({pct:.1f}%) - committed to git")
        else:
            console.print(f"  {note_type}: {cnt} ({pct:.1f}%)")

    console.print(f"\n[bold]By scratch status:[/bold]")
    for status, cnt in sorted(by_scratch.items(), key=lambda x: -x[1]):
        pct = cnt / total * 100 if total > 0 else 0
        console.print(f"  {status}: {cnt} ({pct:.1f}%)")

    console.print(f"\n[bold]Bulk-imported entries (from scratches.txt):[/bold]")
    console.print(f"  Total: {len(all_recovered_scratches)}")
    console.print(f"  Without scratch slug: {len(recovered_no_scratch)}")
    console.print(f"  With scratch slug: {len(all_recovered_scratches) - len(recovered_no_scratch)}")

    console.print(f"\n[bold]Orphaned entries (no scratch slug):[/bold]")
    console.print(f"  Total: {len(all_no_scratch)}")

    # Verify scratches on server if requested
    if verify_server:
        import httpx

        api_base = detect_local_api_url()
        if not api_base:
            console.print("\n[red]Could not detect local decomp.me server - cannot verify[/red]")
        else:
            console.print(f"\n[bold]Verifying scratches on server ({api_base})...[/bold]")

            # Get entries to check (recovered_scratches entries with slugs)
            entries_to_check = all_recovered_scratches[:limit]

            async def check_scratches():
                missing = []
                found = []
                errors = 0

                async with httpx.AsyncClient(base_url=api_base, timeout=10.0) as client:
                    for i, entry in enumerate(entries_to_check):
                        slug = entry.get('local_scratch_slug')
                        if not slug:
                            continue

                        console.print(f"[dim]Checking {entry['function_name']} ({i+1}/{len(entries_to_check)})...[/dim]", end="")

                        try:
                            resp = await asyncio.wait_for(
                                client.get(f'/scratch/{slug}'),
                                timeout=5.0
                            )
                            if resp.status_code == 200:
                                console.print(" [green]exists[/green]")
                                found.append(entry)
                            elif resp.status_code == 404:
                                console.print(" [red]NOT FOUND[/red]")
                                missing.append(entry)
                            else:
                                console.print(f" [yellow]{resp.status_code}[/yellow]")
                                errors += 1
                        except asyncio.TimeoutError:
                            console.print(" [yellow]timeout[/yellow]")
                            errors += 1
                        except Exception as e:
                            console.print(f" [red]error[/red]")
                            errors += 1

                return {'missing': missing, 'found': found, 'errors': errors}

            verify_results = asyncio.run(check_scratches())

            console.print(f"\n[bold]Server verification results:[/bold]")
            console.print(f"  Found on server: {len(verify_results['found'])}")
            console.print(f"  [red]Missing on server: {len(verify_results['missing'])}[/red]")
            console.print(f"  Errors: {verify_results['errors']}")

            if verify_results['missing'] and not output_json:
                console.print("\n[yellow]Missing scratches (first 10):[/yellow]")
                for entry in verify_results['missing'][:10]:
                    console.print(f"  {entry['function_name']} (slug: {entry.get('local_scratch_slug')})")

    # Handle cleanup
    to_remove = []
    if remove_recovered:
        # Remove all bulk-imported entries (from scratches.txt)
        to_remove = all_recovered_scratches
        console.print(f"\n[yellow]Will remove {len(to_remove)} bulk-imported entries (from scratches.txt)[/yellow]")
    elif remove_no_scratch:
        to_remove = all_no_scratch
        console.print(f"\n[yellow]Will remove {len(to_remove)} entries without scratches[/yellow]")

    if to_remove:
        if dry_run:
            console.print("\n[dim]Dry run - showing first 10 entries that would be removed:[/dim]")
            for entry in to_remove[:10]:
                console.print(f"  {entry['function_name']} (notes={entry.get('notes')})")
            if len(to_remove) > 10:
                console.print(f"  [dim]... and {len(to_remove) - 10} more[/dim]")
            console.print("\n[dim]Run with --no-dry-run to actually remove[/dim]")
        else:
            removed = 0
            with db.connection() as conn:
                for entry in to_remove:
                    conn.execute(
                        "DELETE FROM functions WHERE function_name = ?",
                        (entry['function_name'],)
                    )
                    removed += 1
            console.print(f"\n[green]Removed {removed} entries from database[/green]")
    else:
        console.print("\n[dim]Use --remove-recovered or --remove-no-scratch to clean up[/dim]")


def rebuild_command(
    source: Annotated[
        str, typer.Option("--source", "-s", help="Source to rebuild from")
    ] = "json",
    dry_run: Annotated[
        bool, typer.Option("--dry-run", help="Preview changes without applying")
    ] = False,
    verbose: Annotated[
        bool, typer.Option("--verbose", "-v", help="Show detailed output")
    ] = False,
):
    """Rebuild database from underlying sources.

    Sources:
    - json: Re-import current database entries (no-op, legacy option)
    - git: Scan git history for Match commits
    - all: All of the above
    """
    db = get_db()
    stats = {
        "functions_migrated": 0,
        "scratches_migrated": 0,
        "claims_migrated": 0,
        "syncs_migrated": 0,
        "errors": [],
    }

    if source in ("json", "all"):
        console.print("[bold]Re-importing from database (legacy migration path)...[/bold]")

        # Note: load_completed_functions() now reads from SQLite, so this is
        # effectively a no-op that re-upserts existing data
        completed = load_completed_functions()
        for func_name, info in completed.items():
            if verbose:
                console.print(f"  Function: {func_name}")

            if not dry_run:
                status = 'merged' if info.get('pr_state') == 'MERGED' else (
                    'committed' if info.get('committed') else (
                        'matched' if info.get('match_percent', 0) >= 95 else 'unclaimed'
                    )
                )
                db.upsert_function(
                    func_name,
                    agent_id=AGENT_ID,
                    match_percent=info.get('match_percent', 0),
                    local_scratch_slug=info.get('scratch_slug'),
                    production_scratch_slug=info.get('production_slug'),
                    is_committed=info.get('committed', False),
                    status=status,
                    branch=info.get('branch'),
                    pr_url=info.get('pr_url'),
                    pr_number=info.get('pr_number'),
                    pr_state=info.get('pr_state'),
                    notes=info.get('notes'),
                )
            stats["functions_migrated"] += 1

        console.print(f"  Migrated {stats['functions_migrated']} functions")

        # Note: load_slug_map() now reads from SQLite sync_state table
        slug_map = load_slug_map()
        for prod_slug, info in slug_map.items():
            if verbose:
                console.print(f"  Scratch: {prod_slug}")

            if not dry_run:
                # Insert production scratch
                db.upsert_scratch(
                    prod_slug,
                    instance='production',
                    base_url=PRODUCTION_DECOMP_ME,
                    function_name=info.get('function'),
                    match_percent=info.get('match_percent'),
                    created_at=info.get('synced_at'),
                )

                # Insert local scratch if present
                local_slug = info.get('local_slug')
                if local_slug:
                    local_base = detect_local_api_url() or "http://localhost:8000"
                    db.upsert_scratch(
                        local_slug,
                        instance='local',
                        base_url=local_base,
                        function_name=info.get('function'),
                        match_percent=info.get('match_percent'),
                    )
                    db.record_sync(local_slug, prod_slug, info.get('function'))
                    stats["syncs_migrated"] += 1

            stats["scratches_migrated"] += 1

        console.print(f"  Migrated {stats['scratches_migrated']} scratches")
        console.print(f"  Migrated {stats['syncs_migrated']} sync mappings")

        # 3. scratch_tokens.json
        tokens_file = DECOMP_CONFIG_DIR / "scratch_tokens.json"
        if tokens_file.exists():
            try:
                with open(tokens_file) as f:
                    tokens = json.load(f)
                for slug, token in tokens.items():
                    if not dry_run:
                        with db.connection() as conn:
                            conn.execute(
                                "UPDATE scratches SET claim_token = ? WHERE slug = ?",
                                (token, slug)
                            )
                console.print(f"  Migrated {len(tokens)} scratch tokens")
            except (json.JSONDecodeError, IOError) as e:
                stats["errors"].append(f"scratch_tokens.json: {e}")

        # 4. Current claims
        claims_file = Path(os.environ.get("DECOMP_CLAIMS_FILE", "/tmp/decomp_claims.json"))
        if claims_file.exists():
            try:
                with open(claims_file) as f:
                    claims = json.load(f)
                now = time.time()
                for func_name, info in claims.items():
                    if now - info.get('timestamp', 0) < 3600:  # Not expired
                        if verbose:
                            console.print(f"  Claim: {func_name}")
                        if not dry_run:
                            db.add_claim(func_name, info.get('agent_id', 'unknown'))
                        stats["claims_migrated"] += 1
                console.print(f"  Migrated {stats['claims_migrated']} active claims")
            except (json.JSONDecodeError, IOError) as e:
                stats["errors"].append(f"decomp_claims.json: {e}")

        # Update metadata
        if not dry_run:
            db.set_meta('last_full_rebuild', str(time.time()))

    if source in ("git", "all"):
        console.print("\n[bold]Scanning git history...[/bold]")
        console.print("[dim]Git scan not yet implemented[/dim]")
        # TODO: Scan git commits for "Match <function>" patterns

    # Summary
    console.print("\n[bold]Rebuild Summary[/bold]")
    console.print(f"  Functions: {stats['functions_migrated']}")
    console.print(f"  Scratches: {stats['scratches_migrated']}")
    console.print(f"  Claims: {stats['claims_migrated']}")
    console.print(f"  Syncs: {stats['syncs_migrated']}")

    if stats["errors"]:
        console.print(f"\n[red]Errors:[/red]")
        for err in stats["errors"]:
            console.print(f"  {err}")

    if dry_run:
        console.print("\n[yellow]Dry run - no changes made[/yellow]")


def export_command(
    output_file: Annotated[
        Path, typer.Argument(help="Output file path")
    ] = Path("state_export.json"),
    include_audit: Annotated[
        bool, typer.Option("--include-audit", help="Include full audit log")
    ] = False,
):
    """Export database state to JSON for backup/debugging."""
    db = get_db()

    export_data: dict[str, Any] = {
        "exported_at": time.time(),
        "exported_by": AGENT_ID,
        "functions": [],
        "scratches": [],
        "claims": [],
        "agents": [],
        "sync_state": [],
    }

    with db.connection() as conn:
        # Functions
        cursor = conn.execute("SELECT * FROM functions")
        export_data["functions"] = [dict(row) for row in cursor.fetchall()]

        # Scratches
        cursor = conn.execute("SELECT * FROM scratches")
        export_data["scratches"] = [dict(row) for row in cursor.fetchall()]

        # Claims
        cursor = conn.execute("SELECT * FROM claims")
        export_data["claims"] = [dict(row) for row in cursor.fetchall()]

        # Agents
        cursor = conn.execute("SELECT * FROM agents")
        export_data["agents"] = [dict(row) for row in cursor.fetchall()]

        # Sync state
        cursor = conn.execute("SELECT * FROM sync_state")
        export_data["sync_state"] = [dict(row) for row in cursor.fetchall()]

        # Audit log (if requested)
        if include_audit:
            cursor = conn.execute("SELECT * FROM audit_log ORDER BY timestamp DESC")
            export_data["audit_log"] = [dict(row) for row in cursor.fetchall()]

    with open(output_file, 'w') as f:
        json.dump(export_data, f, indent=2, default=str)

    console.print(f"[green]Exported to:[/green] {output_file}")
    console.print(f"  Functions: {len(export_data['functions'])}")
    console.print(f"  Scratches: {len(export_data['scratches'])}")
    console.print(f"  Claims: {len(export_data['claims'])}")
    if include_audit:
        console.print(f"  Audit entries: {len(export_data.get('audit_log', []))}")
