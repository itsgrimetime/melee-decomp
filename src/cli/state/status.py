"""Status-related state commands: status, urls, history."""

import json
from typing import Annotated, Any, Optional

import typer
from rich.table import Table

from .._common import (
    PRODUCTION_DECOMP_ME,
    console,
    detect_local_api_url,
)
from ._helpers import format_age, format_datetime, find_best_local_scratch
from src.db import get_db


def status_command(
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
    - needs_fix: Committed but build is broken (needs /decomp-fixup)
    - merged: PR merged
    - documented: Has documentation (partial or complete)
    - undocumented: No documentation yet
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
            local_base = detect_local_api_url() or "http://localhost:8000"
            url = f"{local_base}/scratch/{func['local_scratch_slug']}"
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

        # Show build status for committed functions with issues
        if func.get('build_status') == 'broken':
            console.print(f"[bold]Build:[/bold] [yellow]broken[/yellow]")
            if func.get('build_diagnosis'):
                console.print(f"[bold]Diagnosis:[/bold] {func['build_diagnosis']}")
            console.print(f"[dim]Use /decomp-fixup to resolve build issues[/dim]")
        elif func.get('build_status') == 'passing':
            console.print(f"[bold]Build:[/bold] [green]passing[/green]")

        # Show documentation status
        doc_status = func.get('documentation_status') or 'none'
        if doc_status == 'complete':
            console.print(f"[bold]Docs:[/bold] [green]complete[/green]")
        elif doc_status == 'partial':
            console.print(f"[bold]Docs:[/bold] [yellow]partial[/yellow]")
        elif func.get('is_documented'):
            console.print(f"[bold]Docs:[/bold] [green]yes[/green]")

        if func.get('worktree_path'):
            console.print(f"[bold]Worktree:[/bold] {func['worktree_path']}")

        console.print(f"[dim]Updated: {format_datetime(func.get('updated_at'))}[/dim]")

        # Show branch progress if any
        branch_progress = db.get_branch_progress(function_name)
        if branch_progress:
            console.print(f"\n[bold]Branch Progress:[/bold]")
            for bp in branch_progress[:5]:  # Show top 5
                status_icon = "✓" if bp.get('is_committed') else "○"
                console.print(
                    f"  {status_icon} {bp['branch']}: {bp['match_percent']:.1f}% "
                    f"[dim]({format_datetime(bp.get('updated_at'))})[/dim]"
                )
            if len(branch_progress) > 5:
                console.print(f"  [dim]... and {len(branch_progress) - 5} more[/dim]")
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
        elif category == "needs_fix":
            query += " AND build_status = 'broken' AND is_committed = TRUE"
        elif category == "merged":
            query += " AND pr_state = 'MERGED'"
        elif category == "documented":
            query += " AND (is_documented = TRUE OR documentation_status IN ('partial', 'complete'))"
        elif category == "undocumented":
            query += " AND (is_documented = FALSE OR is_documented IS NULL) AND (documentation_status IS NULL OR documentation_status = 'none')"

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
    table.add_column("Slug", style="dim")
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
        elif func.get('build_status') == 'broken':
            status = "[yellow]needs_fix[/yellow]"
        elif func.get('is_committed'):
            status = "[blue]committed[/blue]"
        elif status == 'claimed':
            status = "[yellow]claimed[/yellow]"

        # Extract slug from local_scratch_slug (could be URL or just slug)
        local_slug = func.get('local_scratch_slug', '') or ''
        if '/' in local_slug:
            local_slug = local_slug.rstrip('/').split('/')[-1]

        table.add_row(
            func['function_name'],
            match_str,
            local_slug or '-',
            status,
            func.get('claimed_by_agent', '-') or '-',
            format_age(func.get('updated_at')),
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


def urls_command(
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
            local_base = detect_local_api_url() or "http://localhost:8000"
            urls["local_url"] = f"{local_base}/scratch/{func['local_scratch_slug']}"
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


def history_command(
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
        timestamp = format_datetime(entry.get('timestamp'))
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
