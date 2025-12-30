"""State commands - unified state management and querying."""

import asyncio
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
    PRODUCTION_DECOMP_ME,
    console,
    detect_local_api_url,
    extract_pr_info,
    get_local_api_url,
    get_pr_status_from_gh,
    load_completed_functions,
    load_slug_map,
)
from src.db import get_db, StateDB

state_app = typer.Typer(help="Query and manage agent state database")


def _find_best_local_scratch(function_name: str) -> tuple[str | None, float]:
    """Search local decomp.me for scratches matching function name.

    Returns (slug, match_percent) of the best scratch, or (None, 0) if not found.
    """
    from src.client import DecompMeAPIClient

    async def search():
        api_url = get_local_api_url()
        async with DecompMeAPIClient(api_url) as client:
            # Search for scratches with this function name, ordered by score (lower = better)
            scratches = await client.list_scratches(
                search=function_name,
                platform="gc_wii",
                ordering="score",  # Lowest diff score first = best match
                page_size=10,
            )
            # Filter to exact name matches and find best
            # Note: score is diff score (lower = better), so match% = (1 - score/max_score) * 100
            best_slug = None
            best_pct = 0.0
            for s in scratches:
                if s.name == function_name:
                    if s.max_score > 0:
                        pct = (1 - s.score / s.max_score) * 100
                    else:
                        pct = 0.0
                    if pct > best_pct:
                        best_pct = pct
                        best_slug = s.slug
            return best_slug, best_pct

    try:
        return asyncio.run(search())
    except Exception:
        return None, 0.0


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
    verify_server: Annotated[
        bool, typer.Option("--verify-server", help="Verify scratches exist on server and match % is correct")
    ] = False,
    verify_git: Annotated[
        bool, typer.Option("--verify-git", help="Verify committed functions exist in git repo")
    ] = False,
    melee_root: Annotated[
        Optional[Path], typer.Option("--melee-root", "-r", help="Path to melee repo for --verify-git")
    ] = None,
    limit: Annotated[
        int, typer.Option("--limit", "-n", help="Limit entries to verify")
    ] = 100,
    fix: Annotated[
        bool, typer.Option("--fix", help="Automatically fix issues where possible")
    ] = False,
    verbose: Annotated[
        bool, typer.Option("--verbose", "-v", help="Show detailed output")
    ] = False,
    output_json: Annotated[
        bool, typer.Option("--json", help="Output as JSON")
    ] = False,
):
    """Validate database state for consistency and correctness (bidirectional).

    Basic checks (always run):
    - Status consistency (matches is_committed, pr_state, match_percent)
    - All functions linked to local scratch
    - All 95%+ functions synced to production
    - All committed functions linked to PRs
    - All PR links have state

    With --verify-server:
    - Scratches exist on server with correct match %
    - Scratch names match function names

    With --verify-git:
    - Committed functions have MATCHING marker in repo
    - Functions marked MATCHING in repo are tracked as committed

    To link functions to PRs, run: melee-agent audit discover-prs
    """
    db = get_db()
    issues: list[dict] = []
    fixes_applied = 0

    with db.connection() as conn:
        # Get all functions for validation
        cursor = conn.execute("""
            SELECT function_name, match_percent, status, local_scratch_slug,
                   production_scratch_slug, is_committed, pr_url, pr_state, pr_number, branch
            FROM functions
        """)
        all_functions = [dict(row) for row in cursor.fetchall()]

    console.print("[bold]Validating database state...[/bold]\n")

    # === Check 1: Status consistency ===
    for func in all_functions:
        name = func['function_name']
        status = func.get('status') or 'unclaimed'
        is_committed = func.get('is_committed', False)
        pr_state = func.get('pr_state')
        match_pct = func.get('match_percent', 0)

        # Determine what status SHOULD be based on available data
        if pr_state == 'MERGED':
            expected_status = 'merged'
        elif pr_state == 'OPEN':
            expected_status = 'in_review'
        elif pr_state == 'CLOSED':
            # Closed but not merged - work was rejected/abandoned
            expected_status = 'matched' if match_pct >= 95 else 'in_progress' if match_pct > 0 else 'unclaimed'
        elif is_committed:
            expected_status = 'committed'
        elif match_pct >= 95:
            expected_status = 'matched'
        elif match_pct > 0:
            expected_status = 'in_progress'
        else:
            expected_status = 'unclaimed'

        if status != expected_status:
            issues.append({
                'type': 'status_mismatch',
                'severity': 'warning',
                'function': name,
                'message': f'Status "{status}" should be "{expected_status}"',
                'fix': {'status': expected_status},
            })

    # === Check 2: Missing local scratch ===
    for func in all_functions:
        if func.get('match_percent', 0) > 0 and not func.get('local_scratch_slug'):
            func_name = func['function_name']
            issue = {
                'type': 'missing_local_scratch',
                'severity': 'error',
                'function': func_name,
                'message': f'Has {func["match_percent"]:.0f}% but no local scratch slug',
            }
            # If --fix is set, search for existing scratch on local decomp.me
            if fix:
                console.print(f"[dim]Searching for scratch: {func_name}...[/dim]")
                slug, pct = _find_best_local_scratch(func_name)
                if slug:
                    issue['fix'] = {'local_scratch_slug': slug, 'match_percent': pct}
                    issue['message'] += f' (found {slug} at {pct:.1f}%)'
            issues.append(issue)

    # === Check 3: Missing production scratch (has local scratch with progress but not synced) ===
    for func in all_functions:
        if func.get('local_scratch_slug') and func.get('match_percent', 0) > 0 and not func.get('production_scratch_slug'):
            issues.append({
                'type': 'missing_prod_scratch',
                'severity': 'info',
                'function': func['function_name'],
                'message': f'{func["match_percent"]:.0f}% match not synced to production',
            })

    # === Check 4: Committed but no PR ===
    for func in all_functions:
        if func.get('is_committed') and not func.get('pr_url'):
            issues.append({
                'type': 'committed_no_pr',
                'severity': 'warning',
                'function': func['function_name'],
                'message': 'Committed but not linked to a PR',
            })

    # === Check 5: PR linked but no state ===
    for func in all_functions:
        if func.get('pr_url') and not func.get('pr_state'):
            issues.append({
                'type': 'pr_no_state',
                'severity': 'warning',
                'function': func['function_name'],
                'message': f'PR linked but state unknown',
                'pr_url': func['pr_url'],
            })

    # === Check 6: 100% match not committed ===
    # Note: When --verify-git is enabled, we'll cross-check these against the build report
    # and provide more accurate fixes. Store them for now, we'll process them later.
    uncommitted_100_funcs = [f for f in all_functions if f.get('match_percent', 0) >= 100 and not f.get('is_committed')]

    # If not verifying against git, just add basic issues without fixes
    if not verify_git:
        for func in uncommitted_100_funcs:
            issues.append({
                'type': 'uncommitted_100',
                'severity': 'info',
                'function': func['function_name'],
                'message': '100% match but not committed',
            })

    # === Check 7: Verify scratches on server (optional) ===
    if verify_server:
        import asyncio
        import httpx

        api_base = detect_local_api_url()
        if not api_base:
            console.print("[yellow]Could not detect local decomp.me server - skipping server verification[/yellow]")
        else:
            # Normalize base URL - remove /api suffix if present (we add it in requests)
            if api_base.endswith("/api"):
                api_base = api_base[:-4]

            funcs_with_scratch = [f for f in all_functions if f.get('local_scratch_slug')][:limit]
            console.print(f"[dim]Verifying {len(funcs_with_scratch)} scratches on {api_base}...[/dim]")

            async def verify_scratches():
                results = []
                checked = 0
                errors = 0
                async with httpx.AsyncClient(base_url=api_base, timeout=10.0) as client:
                    for i, func in enumerate(funcs_with_scratch):
                        slug = func['local_scratch_slug']
                        name = func['function_name']
                        recorded_pct = func.get('match_percent', 0)

                        # Progress every 10 or on verbose
                        if verbose or (i + 1) % 10 == 0 or i == 0:
                            console.print(f"[dim]  Checking ({i+1}/{len(funcs_with_scratch)}) {name}...[/dim]", end="" if verbose else "\n")

                        try:
                            resp = await asyncio.wait_for(
                                client.get(f'/api/scratch/{slug}'),
                                timeout=5.0
                            )
                            checked += 1
                            if resp.status_code == 404:
                                results.append({
                                    'type': 'scratch_not_found',
                                    'severity': 'error',
                                    'function': name,
                                    'message': f'Scratch {slug} not found on server',
                                })
                                if verbose:
                                    console.print(" [red]NOT FOUND[/red]")
                            elif resp.status_code == 200:
                                data = resp.json()
                                score = data.get('score', 0)
                                max_score = data.get('max_score', 1)
                                actual_pct = ((max_score - score) / max_score * 100) if max_score > 0 else 0

                                # Check if match % differs significantly
                                if abs(actual_pct - recorded_pct) > 1.0:
                                    results.append({
                                        'type': 'match_pct_mismatch',
                                        'severity': 'warning',
                                        'function': name,
                                        'message': f'Recorded {recorded_pct:.1f}% but server shows {actual_pct:.1f}%',
                                        'fix': {'match_percent': actual_pct},
                                    })
                                    if verbose:
                                        console.print(f" [yellow]{recorded_pct:.0f}% -> {actual_pct:.0f}%[/yellow]")
                                elif verbose:
                                    console.print(" [green]OK[/green]")

                                # Check scratch name matches function name
                                scratch_name = data.get('name', '')
                                if scratch_name and scratch_name != name:
                                    results.append({
                                        'type': 'scratch_name_mismatch',
                                        'severity': 'error',
                                        'function': name,
                                        'message': f'Scratch named "{scratch_name}" but tracking as "{name}"',
                                    })
                            else:
                                errors += 1
                                if verbose:
                                    console.print(f" [yellow]HTTP {resp.status_code}[/yellow]")
                        except asyncio.TimeoutError:
                            errors += 1
                            if verbose:
                                console.print(" [yellow]timeout[/yellow]")
                        except Exception as e:
                            errors += 1
                            if verbose:
                                console.print(f" [red]error: {e}[/red]")

                console.print(f"[dim]  Checked {checked}, errors {errors}[/dim]")
                return results

            server_issues = asyncio.run(verify_scratches())
            issues.extend(server_issues)

    # === Check 8: Verify against build report (optional) ===
    if verify_git:
        from ._common import DEFAULT_MELEE_ROOT

        repo_path = melee_root or DEFAULT_MELEE_ROOT
        report_path = repo_path / "build" / "GALE01" / "report.json"

        if not report_path.exists():
            console.print(f"[yellow]Build report not found at {report_path}[/yellow]")
            console.print(f"[dim]Run 'ninja' in melee repo to generate report.json[/dim]")
        else:
            console.print(f"[dim]Verifying against build report...[/dim]")

            # Parse report.json
            try:
                with open(report_path) as f:
                    report = json.load(f)

                # Build map of function name -> match percent from report
                report_funcs: dict[str, float] = {}
                for unit in report.get('units', []):
                    for func in unit.get('functions', []):
                        name = func.get('name')
                        pct = func.get('fuzzy_match_percent', 0)
                        if name:
                            report_funcs[name] = pct

                console.print(f"[dim]  Found {len(report_funcs)} functions in build report[/dim]")

                # Get DB functions
                db_committed = {f['function_name']: f for f in all_functions if f.get('is_committed')}
                db_by_name = {f['function_name']: f for f in all_functions}

                # Check 1: DB committed functions should be 100% in report
                mismatch_count = 0
                for func_name, func in list(db_committed.items())[:limit]:
                    if func_name in report_funcs:
                        report_pct = report_funcs[func_name]
                        if report_pct < 100:
                            mismatch_count += 1
                            # Determine new status based on report percentage
                            if report_pct >= 95:
                                new_status = 'matched'  # High match but not committed
                            elif report_pct > 0:
                                new_status = 'in_progress'
                            else:
                                new_status = 'unclaimed'
                            issues.append({
                                'type': 'committed_not_100_in_build',
                                'severity': 'warning',
                                'function': func_name,
                                'message': f'Marked committed but build shows {report_pct:.1f}%',
                                'fix': {
                                    'match_percent': report_pct,
                                    'is_committed': False,
                                    'status': new_status,
                                },
                            })
                    else:
                        issues.append({
                            'type': 'committed_not_in_build',
                            'severity': 'warning',
                            'function': func_name,
                            'message': 'Marked committed but not found in build report',
                        })

                # Check 2: 100% functions in report should be committed in DB
                not_tracked = 0
                for func_name, pct in report_funcs.items():
                    if pct >= 100:
                        if func_name in db_by_name:
                            if not db_by_name[func_name].get('is_committed'):
                                issues.append({
                                    'type': 'build_100_not_committed',
                                    'severity': 'info',
                                    'function': func_name,
                                    'message': '100% in build but not marked committed in DB',
                                    'fix': {'is_committed': True, 'match_percent': 100, 'status': 'committed'},
                                })
                        else:
                            not_tracked += 1

                console.print(f"[dim]  {mismatch_count} committed functions not 100% in build[/dim]")
                console.print(f"[dim]  {not_tracked} 100% functions in build not tracked in DB[/dim]")

                # Check 3: Cross-check uncommitted_100 from DB against build report
                # These are functions our DB says are 100% but not committed
                db_correct = 0
                db_wrong = 0
                for func in uncommitted_100_funcs:
                    func_name = func['function_name']
                    if func_name in report_funcs:
                        report_pct = report_funcs[func_name]
                        if report_pct >= 100:
                            # Build confirms 100% - can safely mark as committed
                            db_correct += 1
                            issues.append({
                                'type': 'uncommitted_100',
                                'severity': 'info',
                                'function': func_name,
                                'message': '100% in DB and build, not marked committed',
                                'fix': {'is_committed': True, 'match_percent': 100, 'status': 'committed'},
                            })
                        else:
                            # Build shows different % - our DB is wrong
                            db_wrong += 1
                            if report_pct >= 95:
                                new_status = 'matched'
                            elif report_pct > 0:
                                new_status = 'in_progress'
                            else:
                                new_status = 'unclaimed'
                            issues.append({
                                'type': 'db_100_but_build_differs',
                                'severity': 'warning',
                                'function': func_name,
                                'message': f'DB shows 100% but build shows {report_pct:.1f}%',
                                'fix': {'match_percent': report_pct, 'status': new_status},
                            })
                    else:
                        # Function not in build report - can't verify
                        issues.append({
                            'type': 'uncommitted_100',
                            'severity': 'info',
                            'function': func_name,
                            'message': '100% in DB but not found in build report',
                        })

                console.print(f"[dim]  {db_correct} uncommitted 100% confirmed by build[/dim]")
                console.print(f"[dim]  {db_wrong} uncommitted 100% contradicted by build[/dim]")

                # Check 4: Find functions that improved from baseline but aren't tracked
                # This catches partial implementations that were never added to DB
                from .pr import _get_cached_baseline_path, _check_upstream_status

                commit_hash, _, _ = _check_upstream_status(repo_path)
                if commit_hash:
                    baseline_path = _get_cached_baseline_path(commit_hash)
                    if baseline_path.exists():
                        console.print(f"[dim]Comparing against baseline to find untracked improvements...[/dim]")
                        try:
                            with open(baseline_path) as f:
                                baseline = json.load(f)

                            # Build map of baseline function percentages
                            baseline_funcs: dict[str, float] = {}
                            for unit in baseline.get('units', []):
                                for func in unit.get('functions', []):
                                    name = func.get('name')
                                    pct = func.get('fuzzy_match_percent', 0)
                                    if name:
                                        baseline_funcs[name] = pct

                            # Find functions that improved but aren't in DB
                            untracked_improved = 0
                            for func_name, current_pct in report_funcs.items():
                                baseline_pct = baseline_funcs.get(func_name, 0)
                                # Function improved from baseline and isn't tracked
                                if current_pct > baseline_pct and func_name not in db_by_name:
                                    untracked_improved += 1
                                    # Determine status based on match percentage
                                    if current_pct >= 100:
                                        new_status = 'committed'
                                        is_committed = True
                                    elif current_pct >= 95:
                                        new_status = 'matched'
                                        is_committed = False
                                    else:
                                        new_status = 'in_progress'
                                        is_committed = False
                                    issues.append({
                                        'type': 'improved_not_tracked',
                                        'severity': 'warning',
                                        'function': func_name,
                                        'message': f'Improved {baseline_pct:.1f}% -> {current_pct:.1f}% but not tracked in DB',
                                        'fix': {
                                            'match_percent': current_pct,
                                            'status': new_status,
                                            'is_committed': is_committed,
                                        },
                                    })

                            console.print(f"[dim]  {untracked_improved} improved functions not tracked in DB[/dim]")

                        except Exception as e:
                            console.print(f"[yellow]Error comparing to baseline: {e}[/yellow]")
                    else:
                        console.print(f"[dim]No baseline report cached - run 'pr describe' first to generate[/dim]")

            except Exception as e:
                console.print(f"[yellow]Error parsing report.json: {e}[/yellow]")

    # === Apply fixes if requested ===
    applied_fixes = []
    if fix:
        for issue in issues:
            if 'fix' in issue:
                fix_data = issue['fix']
                func_name = issue['function']
                issue_type = issue['type']
                # Use upsert to handle both new and existing functions
                db.upsert_function(func_name, **fix_data)
                fixes_applied += 1
                applied_fixes.append((issue_type, func_name, fix_data))

    # === Summary stats ===
    with db.connection() as conn:
        cursor = conn.execute("SELECT COUNT(*) as cnt FROM functions")
        total_functions = cursor.fetchone()['cnt']
        cursor = conn.execute("SELECT COUNT(*) as cnt FROM functions WHERE is_committed = TRUE")
        committed = cursor.fetchone()['cnt']
        cursor = conn.execute("SELECT COUNT(*) as cnt FROM functions WHERE match_percent >= 95")
        matched = cursor.fetchone()['cnt']
        cursor = conn.execute("SELECT COUNT(*) as cnt FROM functions WHERE pr_url IS NOT NULL")
        with_pr = cursor.fetchone()['cnt']
        cursor = conn.execute("SELECT COUNT(*) as cnt FROM functions WHERE production_scratch_slug IS NOT NULL")
        with_prod = cursor.fetchone()['cnt']

    if output_json:
        print(json.dumps({
            'summary': {
                'total_functions': total_functions,
                'committed': committed,
                'matched_95plus': matched,
                'with_pr': with_pr,
                'with_production_scratch': with_prod,
            },
            'issues': issues,
            'fixes_applied': fixes_applied,
        }, indent=2))
        return

    console.print("[bold]Summary:[/bold]")
    console.print(f"  Total functions: {total_functions}")
    console.print(f"  95%+ matches: {matched}")
    console.print(f"  Committed: {committed}")
    console.print(f"  Linked to PR: {with_pr}")
    console.print(f"  Synced to production: {with_prod}")

    if not issues:
        console.print("\n[green]No issues found[/green]")
        return

    # Group issues by type
    by_type: dict[str, list] = {}
    for issue in issues:
        t = issue['type']
        if t not in by_type:
            by_type[t] = []
        by_type[t].append(issue)

    console.print(f"\n[yellow]Found {len(issues)} issue(s):[/yellow]")

    for issue_type, type_issues in by_type.items():
        severity = type_issues[0]['severity']
        if severity == 'error':
            color = 'red'
        elif severity == 'warning':
            color = 'yellow'
        else:
            color = 'dim'

        console.print(f"\n[{color}]{issue_type}[/{color}] ({len(type_issues)}):")
        for issue in type_issues[:5]:
            console.print(f"  {issue['function']}: {issue['message']}")
        if len(type_issues) > 5:
            console.print(f"  [dim]... and {len(type_issues) - 5} more[/dim]")

    if applied_fixes:
        console.print(f"\n[bold green]Applied {len(applied_fixes)} fix(es):[/bold green]")
        for issue_type, func_name, fix_data in applied_fixes:
            fix_summary = ', '.join(f"{k}={v}" for k, v in fix_data.items())
            console.print(f"  [green]✓[/green] {func_name} ({issue_type}): {fix_summary}")
    elif any('fix' in i for i in issues):
        fixable = sum(1 for i in issues if 'fix' in i)
        console.print(f"\n[dim]{fixable} issues are auto-fixable. Run with --fix to apply.[/dim]")

    # Show suggestions for non-auto-fixable issues
    suggestions = []
    if 'committed_no_pr' in by_type:
        suggestions.append("committed_no_pr: run 'melee-agent audit discover-prs'")
    if 'missing_prod_scratch' in by_type:
        suggestions.append("missing_prod_scratch: run 'melee-agent sync production'")
    if 'uncommitted_100' in by_type:
        suggestions.append("uncommitted_100: run 'melee-agent commit apply <func> <slug>'")
    if 'git_not_committed' in by_type:
        suggestions.append("git_not_committed: run with --fix to mark as committed")
    if 'committed_not_in_git' in by_type:
        suggestions.append("committed_not_in_git: check if function was renamed or pragma format differs")

    if suggestions:
        console.print("\n[dim]To fix:[/dim]")
        for s in suggestions:
            console.print(f"[dim]  {s}[/dim]")


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


@state_app.command("cleanup")
def state_cleanup(
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
        import asyncio
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
