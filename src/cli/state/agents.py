"""Agent-related state commands: agents, stale."""

import json
import time
from typing import Annotated

import typer
from rich.table import Table

from .._common import console
from ._helpers import format_age, format_datetime
from src.db import get_db


def agents_command(
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
            format_age(agent.get('last_active_at')),
        )

    console.print(table)


def stale_command(
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
            format_datetime(entry.get('last_verified')),
            stale_str,
        )

    console.print(table)
    console.print(f"\n[dim]Run: melee-agent state validate --fix[/dim]")
