"""State commands - unified state management and querying."""

import json
import os
import time
from datetime import datetime
from pathlib import Path
from typing import Annotated, Any, Optional

import typer
from rich.table import Table

from ._common import (
    AGENT_ID,
    DECOMP_CONFIG_DIR,
    LOCAL_DECOMP_ME,
    PRODUCTION_DECOMP_ME,
    SLUG_MAP_FILE,
    console,
    extract_pr_info,
    get_pr_status_from_gh,
    load_completed_functions,
    load_slug_map,
)
from src.db import get_db, StateDB

state_app = typer.Typer(help="Query and manage agent state database")

# Staleness thresholds (in hours)
STALE_THRESHOLD_LOCAL = 1.0
STALE_THRESHOLD_PRODUCTION = 24.0
STALE_THRESHOLD_GIT = 24.0
STALE_THRESHOLD_PR = 1.0


def _format_age(timestamp: float | None) -> str:
    """Format a timestamp as human-readable age."""
    if timestamp is None:
        return "never"
    age_seconds = time.time() - timestamp
    if age_seconds < 60:
        return f"{int(age_seconds)}s ago"
    elif age_seconds < 3600:
        return f"{int(age_seconds / 60)}m ago"
    elif age_seconds < 86400:
        return f"{age_seconds / 3600:.1f}h ago"
    else:
        return f"{age_seconds / 86400:.1f}d ago"


def _format_datetime(timestamp: float | None) -> str:
    """Format a timestamp as datetime string."""
    if timestamp is None:
        return "-"
    return datetime.fromtimestamp(timestamp).strftime("%Y-%m-%d %H:%M")


@state_app.command("status")
def state_status(
    function_name: Annotated[
        Optional[str], typer.Argument(help="Function name (optional, shows all if omitted)")
    ] = None,
    category: Annotated[
        str, typer.Option("--category", "-c", help="Filter by category")
    ] = "all",
    agent: Annotated[
        Optional[str], typer.Option("--agent", "-a", help="Filter by agent ID")
    ] = None,
    limit: Annotated[
        int, typer.Option("--limit", "-n", help="Maximum results")
    ] = 50,
    output_json: Annotated[
        bool, typer.Option("--json", help="Output as JSON")
    ] = False,
):
    """Show current state of tracked work.

    Categories:
    - all: Everything
    - claimed: Currently claimed
    - in-progress: Being actively worked on
    - matched: 95%+ match achieved
    - committed: Committed to git
    - merged: PR merged
    - stale: Data needs refresh
    """
    db = get_db()

    if function_name:
        # Show specific function
        func = db.get_function(function_name)
        if not func:
            console.print(f"[yellow]Function not found in database: {function_name}[/yellow]")
            console.print("[dim]Try: melee-agent state rebuild --source json[/dim]")
            return

        if output_json:
            print(json.dumps(func, indent=2, default=str))
            return

        console.print(f"\n[bold cyan]Function:[/bold cyan] {function_name}")
        console.print(f"[bold]Status:[/bold] {func.get('status', 'unknown')}")
        console.print(f"[bold]Match:[/bold] {func.get('match_percent', 0):.1f}%")

        if func.get('local_scratch_slug'):
            url = f"{LOCAL_DECOMP_ME}/scratch/{func['local_scratch_slug']}"
            console.print(f"[bold]Local:[/bold] {url}")

        if func.get('production_scratch_slug'):
            url = f"{PRODUCTION_DECOMP_ME}/scratch/{func['production_scratch_slug']}"
            console.print(f"[bold]Prod:[/bold] {url}")

        if func.get('pr_url'):
            console.print(f"[bold]PR:[/bold] {func['pr_url']} ({func.get('pr_state', '?')})")

        if func.get('branch'):
            console.print(f"[bold]Branch:[/bold] {func['branch']}")

        if func.get('claimed_by_agent'):
            console.print(f"[bold]Agent:[/bold] {func['claimed_by_agent']}")

        if func.get('notes'):
            console.print(f"[bold]Notes:[/bold] {func['notes']}")

        console.print(f"[dim]Updated: {_format_datetime(func.get('updated_at'))}[/dim]")
        return

    # Build query based on category
    with db.connection() as conn:
        query = "SELECT * FROM functions WHERE 1=1"
        params: list[Any] = []

        if category == "claimed":
            query += " AND status = 'claimed'"
        elif category == "in-progress":
            query += " AND status IN ('claimed', 'in_progress')"
        elif category == "matched":
            query += " AND match_percent >= 95 AND is_committed = FALSE"
        elif category == "committed":
            query += " AND is_committed = TRUE AND (pr_state IS NULL OR pr_state != 'MERGED')"
        elif category == "merged":
            query += " AND pr_state = 'MERGED'"

        if agent:
            query += " AND claimed_by_agent = ?"
            params.append(agent)

        query += " ORDER BY updated_at DESC LIMIT ?"
        params.append(limit)

        cursor = conn.execute(query, params)
        functions = [dict(row) for row in cursor.fetchall()]

    if output_json:
        print(json.dumps(functions, indent=2, default=str))
        return

    if not functions:
        console.print(f"[dim]No functions found for category: {category}[/dim]")
        return

    table = Table(title=f"Functions ({category})")
    table.add_column("Function", style="cyan", max_width=30)
    table.add_column("Match", justify="right")
    table.add_column("Status")
    table.add_column("Agent", style="dim", max_width=15)
    table.add_column("Updated", style="dim")

    for func in functions:
        match_str = f"{func.get('match_percent', 0):.0f}%"
        if func.get('match_percent', 0) >= 95:
            match_str = f"[green]{match_str}[/green]"
        elif func.get('match_percent', 0) >= 80:
            match_str = f"[yellow]{match_str}[/yellow]"

        status = func.get('status', 'unknown')
        if func.get('pr_state') == 'MERGED':
            status = "[green]merged[/green]"
        elif func.get('is_committed'):
            status = "[blue]committed[/blue]"
        elif status == 'claimed':
            status = "[yellow]claimed[/yellow]"

        table.add_row(
            func['function_name'],
            match_str,
            status,
            func.get('claimed_by_agent', '-') or '-',
            _format_age(func.get('updated_at')),
        )

    console.print(table)

    # Show summary
    with db.connection() as conn:
        cursor = conn.execute("SELECT COUNT(*) as total FROM functions")
        total = cursor.fetchone()['total']
        cursor = conn.execute("SELECT COUNT(*) as matched FROM functions WHERE match_percent >= 95")
        matched = cursor.fetchone()['matched']
        cursor = conn.execute("SELECT COUNT(*) as committed FROM functions WHERE is_committed = TRUE")
        committed = cursor.fetchone()['committed']

    console.print(f"\n[dim]Total: {total} | 95%+: {matched} | Committed: {committed}[/dim]")


@state_app.command("urls")
def state_urls(
    function_name: Annotated[str, typer.Argument(help="Function name")],
    output_json: Annotated[
        bool, typer.Option("--json", help="Output as JSON")
    ] = False,
):
    """Show all URLs for a function (local scratch, production scratch, PR)."""
    db = get_db()
    func = db.get_function(function_name)

    urls: dict[str, str | None] = {
        "function": function_name,
        "match_percent": None,
        "local_url": None,
        "production_url": None,
        "pr_url": None,
        "pr_state": None,
    }

    if func:
        urls["match_percent"] = func.get('match_percent')
        if func.get('local_scratch_slug'):
            urls["local_url"] = f"{LOCAL_DECOMP_ME}/scratch/{func['local_scratch_slug']}"
        if func.get('production_scratch_slug'):
            urls["production_url"] = f"{PRODUCTION_DECOMP_ME}/scratch/{func['production_scratch_slug']}"
        urls["pr_url"] = func.get('pr_url')
        urls["pr_state"] = func.get('pr_state')

    if output_json:
        print(json.dumps(urls, indent=2))
        return

    console.print(f"\n[bold cyan]Function:[/bold cyan] {function_name}")

    if urls["match_percent"] is not None:
        console.print(f"[bold]Match:[/bold] {urls['match_percent']:.1f}%")

    if urls["local_url"]:
        console.print(f"[bold]Local:[/bold] {urls['local_url']}")
    else:
        console.print("[dim]Local: (no scratch)[/dim]")

    if urls["production_url"]:
        console.print(f"[bold]Prod:[/bold] {urls['production_url']}")
    else:
        console.print("[dim]Prod: (not synced)[/dim]")

    if urls["pr_url"]:
        state = urls["pr_state"] or "?"
        if state == "MERGED":
            console.print(f"[bold]PR:[/bold] {urls['pr_url']} [green]({state})[/green]")
        elif state == "OPEN":
            console.print(f"[bold]PR:[/bold] {urls['pr_url']} [yellow]({state})[/yellow]")
        else:
            console.print(f"[bold]PR:[/bold] {urls['pr_url']} ({state})")
    else:
        console.print("[dim]PR: (none)[/dim]")


@state_app.command("history")
def state_history(
    function_name: Annotated[str, typer.Argument(help="Function name")],
    limit: Annotated[
        int, typer.Option("--limit", "-n", help="Maximum entries")
    ] = 20,
    output_json: Annotated[
        bool, typer.Option("--json", help="Output as JSON")
    ] = False,
):
    """Show audit history for a function."""
    db = get_db()
    entries = db.get_history(entity_type='function', entity_id=function_name, limit=limit)

    # Also get claim history
    claim_entries = db.get_history(entity_type='claim', entity_id=function_name, limit=limit)
    entries.extend(claim_entries)

    # Sort by timestamp
    entries.sort(key=lambda x: x.get('timestamp', 0), reverse=True)
    entries = entries[:limit]

    if output_json:
        print(json.dumps(entries, indent=2, default=str))
        return

    if not entries:
        console.print(f"[dim]No history found for: {function_name}[/dim]")
        return

    console.print(f"\n[bold cyan]History for:[/bold cyan] {function_name}\n")

    for entry in entries:
        timestamp = _format_datetime(entry.get('timestamp'))
        action = entry.get('action', '?')
        entity_type = entry.get('entity_type', '?')
        agent = entry.get('agent_id', '-')

        # Color code actions
        if action == 'created':
            action_str = f"[green]{action}[/green]"
        elif action == 'deleted' or action == 'released':
            action_str = f"[red]{action}[/red]"
        else:
            action_str = f"[yellow]{action}[/yellow]"

        console.print(f"[dim]{timestamp}[/dim] {entity_type} {action_str} by {agent}")

        # Show key changes
        new_val = entry.get('new_value', {})
        if isinstance(new_val, dict):
            if 'match_percent' in new_val:
                console.print(f"  match: {new_val['match_percent']:.1f}%")
            if 'status' in new_val:
                console.print(f"  status: {new_val['status']}")


@state_app.command("agents")
def state_agents(
    show_inactive: Annotated[
        bool, typer.Option("--inactive", "-i", help="Show inactive agents")
    ] = False,
    output_json: Annotated[
        bool, typer.Option("--json", help="Output as JSON")
    ] = False,
):
    """Show active agents and their work."""
    db = get_db()
    agents = db.get_agent_summary()

    # Filter inactive unless requested
    if not show_inactive:
        one_hour_ago = time.time() - 3600
        agents = [a for a in agents if (a.get('last_active_at') or 0) > one_hour_ago]

    if output_json:
        print(json.dumps(agents, indent=2, default=str))
        return

    if not agents:
        console.print("[dim]No active agents found[/dim]")
        return

    table = Table(title="Agent Summary")
    table.add_column("Agent ID", style="cyan")
    table.add_column("Worktree", style="dim", max_width=30)
    table.add_column("Claims", justify="right")
    table.add_column("Committed", justify="right")
    table.add_column("Last Active", style="dim")

    for agent in agents:
        worktree = agent.get('worktree_path', '-')
        if worktree and len(worktree) > 30:
            worktree = "..." + worktree[-27:]

        table.add_row(
            agent.get('agent_id', '?'),
            worktree or '-',
            str(agent.get('active_claims', 0)),
            str(agent.get('committed_functions', 0)),
            _format_age(agent.get('last_active_at')),
        )

    console.print(table)


@state_app.command("stale")
def state_stale(
    hours: Annotated[
        float, typer.Option("--hours", "-h", help="Staleness threshold in hours")
    ] = 1.0,
    output_json: Annotated[
        bool, typer.Option("--json", help="Output as JSON")
    ] = False,
):
    """Show data that may be stale and needs verification."""
    db = get_db()
    stale_data = db.get_stale_data(hours_threshold=hours)

    if output_json:
        print(json.dumps(stale_data, indent=2, default=str))
        return

    if not stale_data:
        console.print(f"[green]No stale data (threshold: {hours}h)[/green]")
        return

    table = Table(title=f"Stale Data (>{hours}h)")
    table.add_column("Function", style="cyan")
    table.add_column("Type")
    table.add_column("Last Verified", style="dim")
    table.add_column("Hours Stale", justify="right")

    for entry in stale_data:
        hours_stale = entry.get('hours_stale', 0)
        stale_str = f"{hours_stale:.1f}h"
        if hours_stale > 24:
            stale_str = f"[red]{stale_str}[/red]"
        elif hours_stale > 4:
            stale_str = f"[yellow]{stale_str}[/yellow]"

        table.add_row(
            entry.get('function_name', '?'),
            entry.get('stale_type', '?'),
            _format_datetime(entry.get('last_verified')),
            stale_str,
        )

    console.print(table)
    console.print(f"\n[dim]Run: melee-agent state validate --fix[/dim]")


@state_app.command("validate")
def state_validate(
    function_name: Annotated[
        Optional[str], typer.Argument(help="Function to validate (optional)")
    ] = None,
    fix: Annotated[
        bool, typer.Option("--fix", help="Automatically fix inconsistencies")
    ] = False,
    verbose: Annotated[
        bool, typer.Option("--verbose", "-v", help="Show detailed output")
    ] = False,
):
    """Validate database state against source data.

    Checks:
    - Claims in DB match /tmp/decomp_claims.json
    - Functions in DB match completed_functions.json
    - Scratches exist (if --fix, verifies via API)
    """
    db = get_db()
    issues: list[tuple[str, str, str]] = []  # (type, entity, message)

    # Check 1: Claims consistency
    claims_file = Path(os.environ.get("DECOMP_CLAIMS_FILE", "/tmp/decomp_claims.json"))
    if claims_file.exists():
        try:
            with open(claims_file) as f:
                file_claims = json.load(f)
            # Filter expired
            now = time.time()
            file_claims = {
                k: v for k, v in file_claims.items()
                if now - v.get('timestamp', 0) < 3600
            }
        except (json.JSONDecodeError, IOError):
            file_claims = {}
    else:
        file_claims = {}

    db_claims = {c['function_name']: c for c in db.get_active_claims()}

    # Claims in file but not DB
    for func in set(file_claims.keys()) - set(db_claims.keys()):
        issues.append(('claim', func, 'In JSON but not DB'))
        if fix:
            info = file_claims[func]
            db.add_claim(func, info.get('agent_id', 'unknown'))

    # Claims in DB but not file
    for func in set(db_claims.keys()) - set(file_claims.keys()):
        issues.append(('claim', func, 'In DB but not JSON'))
        if fix:
            db.release_claim(func)

    # Check 2: Completed functions consistency
    file_completed = load_completed_functions()
    with db.connection() as conn:
        cursor = conn.execute("SELECT function_name FROM functions")
        db_functions = {row['function_name'] for row in cursor.fetchall()}

    # Functions in file but not DB
    for func in set(file_completed.keys()) - db_functions:
        issues.append(('function', func, 'In JSON but not DB'))
        if fix:
            info = file_completed[func]
            db.upsert_function(
                func,
                agent_id=AGENT_ID,
                match_percent=info.get('match_percent', 0),
                local_scratch_slug=info.get('scratch_slug'),
                production_scratch_slug=info.get('production_slug'),
                is_committed=info.get('committed', False),
                branch=info.get('branch'),
                pr_url=info.get('pr_url'),
                pr_state=info.get('pr_state'),
                notes=info.get('notes'),
            )

    # Check 3: Slug map consistency
    slug_map = load_slug_map()
    with db.connection() as conn:
        cursor = conn.execute(
            "SELECT local_slug, production_slug FROM sync_state"
        )
        db_syncs = {(row['local_slug'], row['production_slug']) for row in cursor.fetchall()}

    for prod_slug, info in slug_map.items():
        local_slug = info.get('local_slug')
        if local_slug and (local_slug, prod_slug) not in db_syncs:
            issues.append(('sync', f"{local_slug}->{prod_slug}", 'In JSON but not DB'))
            if fix:
                db.record_sync(local_slug, prod_slug, info.get('function'))

    # Report
    if verbose or issues:
        console.print(f"\n[bold]Validation Results[/bold]")
        console.print(f"Claims checked: {len(file_claims)} (file) vs {len(db_claims)} (db)")
        console.print(f"Functions checked: {len(file_completed)} (file) vs {len(db_functions)} (db)")
        console.print(f"Sync mappings checked: {len(slug_map)} (file)")

    if not issues:
        console.print("[green]No issues found[/green]")
        return

    console.print(f"\n[yellow]Found {len(issues)} issue(s):[/yellow]")
    for issue_type, entity, message in issues[:20]:  # Limit output
        if fix:
            console.print(f"  [green]Fixed[/green] {issue_type}: {entity} - {message}")
        else:
            console.print(f"  [yellow]Issue[/yellow] {issue_type}: {entity} - {message}")

    if len(issues) > 20:
        console.print(f"  ... and {len(issues) - 20} more")

    if not fix:
        console.print(f"\n[dim]Run with --fix to repair these issues[/dim]")


@state_app.command("rebuild")
def state_rebuild(
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
    - json: Migrate from existing JSON files (default)
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
        console.print("[bold]Migrating from JSON files...[/bold]")

        # 1. completed_functions.json
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

        # 2. scratches_slug_map.json
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
                    db.upsert_scratch(
                        local_slug,
                        instance='local',
                        base_url=LOCAL_DECOMP_ME,
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


@state_app.command("export")
def state_export(
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


@state_app.command("prs")
def state_prs(
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


@state_app.command("refresh-prs")
def state_refresh_prs(
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
            console.print(f"  PR #{pr_num}: {old_display} → [green]{new_state}[/green]")
        elif new_state == "CLOSED":
            console.print(f"  PR #{pr_num}: {old_display} → [red]{new_state}[/red]")
        else:
            console.print(f"  PR #{pr_num}: {old_display} → [cyan]{new_state}[/cyan]")

    console.print(f"\n[bold]Summary:[/bold]")
    console.print(f"  Updated: {updated}")
    console.print(f"  Unchanged: {unchanged}")
    if errors:
        console.print(f"  Errors: {errors}")

    if dry_run:
        console.print("\n[yellow](dry run - no changes made)[/yellow]")
