"""Analytics commands - analyze decomp agent performance from session logs."""

import json
from collections import defaultdict
from datetime import datetime, timedelta
from pathlib import Path
from typing import Annotated, Optional

import typer
from rich.table import Table
from rich.panel import Panel
from rich import box

from ._common import console
from src.analytics import DecompAnalyzer

analytics_app = typer.Typer(help="Analyze decomp agent performance from session logs")


def _format_duration(td: Optional[timedelta]) -> str:
    """Format a timedelta as human-readable string."""
    if td is None:
        return "-"
    total_seconds = int(td.total_seconds())
    hours = total_seconds // 3600
    minutes = (total_seconds % 3600) // 60
    if hours > 0:
        return f"{hours}h {minutes}m"
    elif minutes > 0:
        return f"{minutes}m"
    else:
        return "<1m"


def _format_pct(value: float) -> str:
    """Format a percentage."""
    if value >= 0.95:
        return f"[green]{value*100:.1f}%[/green]"
    elif value >= 0.8:
        return f"[yellow]{value*100:.1f}%[/yellow]"
    else:
        return f"[red]{value*100:.1f}%[/red]"


def _format_number(value: float) -> str:
    """Format a number with K/M suffixes."""
    if value >= 1_000_000:
        return f"{value/1_000_000:.1f}M"
    elif value >= 1_000:
        return f"{value/1_000:.1f}K"
    else:
        return f"{value:.0f}"


@analytics_app.command("summary")
def analytics_summary(
    since_days: Annotated[
        int, typer.Option("--since", "-s", help="Analyze sessions from last N days")
    ] = 30,
    project: Annotated[
        str, typer.Option("--project", "-p", help="Project filter")
    ] = "melee-decomp",
    output_json: Annotated[
        bool, typer.Option("--json", help="Output as JSON")
    ] = False,
):
    """Show summary metrics for decomp agent performance.

    Analyzes Claude Code session logs to extract:
    - Success rates (per-function, commit, worktree usage)
    - Efficiency metrics (tokens, turns per function)
    - Error rates by category
    - Match progression statistics
    """
    analyzer = DecompAnalyzer(project_filter=project)

    console.print(f"[dim]Analyzing sessions from last {since_days} days...[/dim]\n")

    sessions = analyzer.analyze_all(since_days=since_days)

    if not sessions:
        console.print("[yellow]No decomp sessions found[/yellow]")
        console.print(f"[dim]Searched in ~/.claude/projects/ for '{project}'[/dim]")
        return

    metrics = analyzer.compute_aggregate_metrics()

    if output_json:
        print(json.dumps({
            "total_sessions": metrics.total_sessions,
            "total_functions_attempted": metrics.total_functions_attempted,
            "total_functions_completed": metrics.total_functions_completed,
            "success_rates": {
                "overall": metrics.overall_success_rate,
                "commit": metrics.commit_success_rate,
                "worktree_correct": metrics.worktree_correct_rate,
                "build_first_try": metrics.build_first_try_rate,
                "dry_run_usage": metrics.dry_run_usage_rate,
            },
            "efficiency": {
                "avg_tokens_per_function": metrics.avg_tokens_per_function,
                "avg_turns_per_function": metrics.avg_turns_per_function,
                "avg_iterations_per_function": metrics.avg_iterations_per_function,
                "avg_duration_minutes": metrics.avg_duration_per_function.total_seconds() / 60 if metrics.avg_duration_per_function else None,
            },
            "errors": {
                "total": metrics.total_errors,
                "per_function": metrics.errors_per_function,
                "by_category": metrics.errors_by_category,
            },
            "match_progression": {
                "avg_initial": metrics.avg_initial_match,
                "avg_final": metrics.avg_final_match,
                "thrashing_rate": metrics.thrashing_rate,
            },
        }, indent=2))
        return

    # Header
    console.print(Panel.fit(
        f"[bold]Decomp Agent Performance Summary[/bold]\n"
        f"[dim]{metrics.total_sessions} sessions, {since_days} days, project: {project}[/dim]",
        border_style="cyan"
    ))

    # Success Rates
    console.print("\n[bold cyan]Success Rates[/bold cyan]")
    table = Table(box=box.SIMPLE)
    table.add_column("Metric", style="dim")
    table.add_column("Value", justify="right")
    table.add_column("Details", style="dim")

    table.add_row(
        "Functions completed",
        f"[bold]{metrics.total_functions_completed}[/bold] / {metrics.total_functions_attempted}",
        _format_pct(metrics.overall_success_rate) if metrics.total_functions_attempted > 0 else "-"
    )
    table.add_row(
        "Build passed first try",
        _format_pct(metrics.build_first_try_rate),
        f"of {metrics.total_functions_attempted} attempts"
    )
    table.add_row(
        "Used --dry-run",
        _format_pct(metrics.dry_run_usage_rate),
        "before committing"
    )
    table.add_row(
        "Worktree used correctly",
        _format_pct(metrics.worktree_correct_rate) if metrics.worktree_correct_rate > 0 else "[dim]N/A[/dim]",
        "no main repo commits"
    )
    console.print(table)

    # Efficiency Metrics
    console.print("\n[bold cyan]Efficiency Metrics[/bold cyan]")
    table = Table(box=box.SIMPLE)
    table.add_column("Metric", style="dim")
    table.add_column("Average", justify="right")
    table.add_column("Context", style="dim")

    table.add_row(
        "Tokens per function",
        f"[bold]{_format_number(metrics.avg_tokens_per_function)}[/bold]",
        f"total: {_format_number(sum(metrics.tokens_distribution))}"
    )
    table.add_row(
        "Turns per function",
        f"[bold]{metrics.avg_turns_per_function:.1f}[/bold]",
        f"total: {sum(metrics.turns_distribution)}"
    )
    table.add_row(
        "Compile iterations",
        f"[bold]{metrics.avg_iterations_per_function:.1f}[/bold]",
        "scratch compile calls"
    )
    table.add_row(
        "Duration per function",
        f"[bold]{_format_duration(metrics.avg_duration_per_function)}[/bold]",
        "wall clock time"
    )
    console.print(table)

    # Error Rates
    console.print("\n[bold cyan]Error Rates[/bold cyan]")
    table = Table(box=box.SIMPLE)
    table.add_column("Category", style="dim")
    table.add_column("Count", justify="right")
    table.add_column("Per Function", justify="right")

    table.add_row(
        "[bold]Total errors[/bold]",
        f"[bold]{metrics.total_errors}[/bold]",
        f"{metrics.errors_per_function:.2f}"
    )

    for category, count in sorted(metrics.errors_by_category.items(), key=lambda x: -x[1]):
        per_func = count / metrics.total_functions_attempted if metrics.total_functions_attempted > 0 else 0
        color = "red" if category in ("build_failure", "server_error") else "yellow"
        table.add_row(
            f"  [{color}]{category}[/{color}]",
            str(count),
            f"{per_func:.2f}"
        )
    console.print(table)

    # Match Progression
    console.print("\n[bold cyan]Match Progression[/bold cyan]")
    table = Table(box=box.SIMPLE)
    table.add_column("Metric", style="dim")
    table.add_column("Value", justify="right")

    table.add_row("Average initial match", f"{metrics.avg_initial_match:.1f}%")
    table.add_row("Average final match", f"[bold]{metrics.avg_final_match:.1f}%[/bold]")
    table.add_row(
        "Improvement",
        f"[green]+{metrics.avg_final_match - metrics.avg_initial_match:.1f}%[/green]"
    )
    table.add_row(
        "Thrashing rate",
        f"[yellow]{metrics.thrashing_rate*100:.1f}%[/yellow]" if metrics.thrashing_rate > 0.1 else f"{metrics.thrashing_rate*100:.1f}%"
    )
    console.print(table)

    # Distribution hints
    if metrics.final_match_distribution:
        console.print(f"\n[dim]Final match distribution:[/dim]")
        below_95 = sum(1 for m in metrics.final_match_distribution if m < 95)
        at_95_99 = sum(1 for m in metrics.final_match_distribution if 95 <= m < 100)
        at_100 = sum(1 for m in metrics.final_match_distribution if m >= 100)
        console.print(f"  <95%: {below_95} | 95-99%: {at_95_99} | 100%: {at_100}")


@analytics_app.command("sessions")
def analytics_sessions(
    since_days: Annotated[
        int, typer.Option("--since", "-s", help="Analyze sessions from last N days")
    ] = 30,
    project: Annotated[
        str, typer.Option("--project", "-p", help="Project filter")
    ] = "melee-decomp",
    limit: Annotated[
        int, typer.Option("--limit", "-n", help="Maximum sessions to show")
    ] = 20,
    output_json: Annotated[
        bool, typer.Option("--json", help="Output as JSON")
    ] = False,
):
    """List analyzed decomp sessions with per-session metrics."""
    analyzer = DecompAnalyzer(project_filter=project)
    sessions = analyzer.analyze_all(since_days=since_days)

    if not sessions:
        console.print("[yellow]No decomp sessions found[/yellow]")
        return

    # Sort by start time descending
    sessions.sort(key=lambda s: s.started_at or s.ended_at, reverse=True)
    sessions = sessions[:limit]

    if output_json:
        output = []
        for s in sessions:
            output.append({
                "session_id": s.session_id[:8],
                "started_at": s.started_at.isoformat() if s.started_at else None,
                "duration_minutes": s.duration.total_seconds() / 60 if s.duration else None,
                "functions_attempted": s.functions_attempted,
                "functions_completed": s.functions_completed,
                "success_rate": s.success_rate,
                "total_tokens": s.total_input_tokens + s.total_output_tokens,
                "total_turns": s.total_turns,
            })
        print(json.dumps(output, indent=2))
        return

    console.print(f"[bold]Decomp Sessions ({len(sessions)} shown)[/bold]\n")

    table = Table(box=box.SIMPLE)
    table.add_column("Session", style="cyan")
    table.add_column("Date", style="dim")
    table.add_column("Duration")
    table.add_column("Functions", justify="right")
    table.add_column("Success", justify="right")
    table.add_column("Tokens", justify="right")
    table.add_column("Turns", justify="right")

    for session in sessions:
        date_str = session.started_at.strftime("%m-%d %H:%M") if session.started_at else "-"

        func_str = f"{session.functions_completed}/{session.functions_attempted}"
        if session.functions_completed > 0:
            func_str = f"[green]{func_str}[/green]"

        success_str = _format_pct(session.success_rate) if session.functions_attempted > 0 else "-"

        table.add_row(
            session.session_id[:8],
            date_str,
            _format_duration(session.duration),
            func_str,
            success_str,
            _format_number(session.total_input_tokens + session.total_output_tokens),
            str(session.total_turns),
        )

    console.print(table)


@analytics_app.command("functions")
def analytics_functions(
    since_days: Annotated[
        int, typer.Option("--since", "-s", help="Analyze sessions from last N days")
    ] = 30,
    project: Annotated[
        str, typer.Option("--project", "-p", help="Project filter")
    ] = "melee-decomp",
    committed_only: Annotated[
        bool, typer.Option("--committed", help="Only show committed functions")
    ] = False,
    failed_only: Annotated[
        bool, typer.Option("--failed", help="Only show abandoned/failed functions")
    ] = False,
    limit: Annotated[
        int, typer.Option("--limit", "-n", help="Maximum functions to show")
    ] = 50,
    output_json: Annotated[
        bool, typer.Option("--json", help="Output as JSON")
    ] = False,
):
    """List individual function attempts with detailed metrics."""
    analyzer = DecompAnalyzer(project_filter=project)
    analyzer.analyze_all(since_days=since_days)

    details = analyzer.get_function_details()

    # Apply filters
    if committed_only:
        details = [d for d in details if d['committed']]
    if failed_only:
        details = [d for d in details if not d['committed'] and d['iterations'] > 0]

    details = details[:limit]

    if output_json:
        print(json.dumps(details, indent=2))
        return

    if not details:
        console.print("[yellow]No function attempts found matching criteria[/yellow]")
        return

    console.print(f"[bold]Function Attempts ({len(details)} shown)[/bold]\n")

    table = Table(box=box.SIMPLE)
    table.add_column("Function", style="cyan", max_width=25)
    table.add_column("Session", style="dim")
    table.add_column("Status")
    table.add_column("Match", justify="right")
    table.add_column("Iters", justify="right")
    table.add_column("Tokens", justify="right")
    table.add_column("Errors", justify="right")
    table.add_column("Duration")

    for func in details:
        status = "[green]committed[/green]" if func['committed'] else "[dim]abandoned[/dim]"

        match_str = f"{func['final_match']:.0f}%" if func['final_match'] else "-"
        if func['final_match'] and func['final_match'] >= 95:
            match_str = f"[green]{match_str}[/green]"

        errors_str = str(func['errors']) if func['errors'] else "-"
        if func['errors'] > 0:
            errors_str = f"[red]{errors_str}[/red]"

        duration_str = f"{func['duration_mins']:.0f}m" if func['duration_mins'] else "-"

        table.add_row(
            func['function'][:25],
            func['session_id'],
            status,
            match_str,
            str(func['iterations']),
            _format_number(func['tokens']),
            errors_str,
            duration_str,
        )

    console.print(table)


@analytics_app.command("errors")
def analytics_errors(
    since_days: Annotated[
        int, typer.Option("--since", "-s", help="Analyze sessions from last N days")
    ] = 30,
    project: Annotated[
        str, typer.Option("--project", "-p", help="Project filter")
    ] = "melee-decomp",
    category: Annotated[
        Optional[str], typer.Option("--category", "-c", help="Filter by error category")
    ] = None,
    output_json: Annotated[
        bool, typer.Option("--json", help="Output as JSON")
    ] = False,
):
    """Show detailed error breakdown with examples.

    Error categories:
    - claim_conflict: Function already claimed by another agent
    - server_error: decomp.me server connectivity issues
    - build_failure: Commit failed to compile
    - type_mismatch: Type casting issues
    - missing_stub: Stub marker not found
    - missing_context: Undefined symbols
    - proto_mismatch: Function prototype mismatch
    - syntax_error: C syntax errors
    - general: Other errors
    """
    analyzer = DecompAnalyzer(project_filter=project)
    analyzer.analyze_all(since_days=since_days)

    # Collect all errors
    all_errors = []
    for session in analyzer.sessions:
        for func in session.functions:
            for error in func.errors:
                all_errors.append({
                    'session_id': session.session_id[:8],
                    'function': func.function_name,
                    'category': error.category.value,
                    'message': error.message[:100],
                    'timestamp': error.timestamp.isoformat() if error.timestamp else None,
                })

    # Filter by category
    if category:
        all_errors = [e for e in all_errors if e['category'] == category]

    if output_json:
        print(json.dumps(all_errors, indent=2))
        return

    if not all_errors:
        console.print("[green]No errors found[/green]")
        return

    # Group by category
    by_category: dict[str, list] = {}
    for error in all_errors:
        cat = error['category']
        if cat not in by_category:
            by_category[cat] = []
        by_category[cat].append(error)

    console.print(f"[bold]Error Analysis ({len(all_errors)} total errors)[/bold]\n")

    for cat, errors in sorted(by_category.items(), key=lambda x: -len(x[1])):
        color = "red" if cat in ("build_failure", "server_error") else "yellow"
        console.print(f"[{color}]{cat}[/{color}]: {len(errors)} occurrences")

        # Show examples
        for error in errors[:3]:
            console.print(f"  [dim]{error['session_id']}[/dim] {error['function']}")
            if error['message']:
                msg = error['message'][:80].replace('\n', ' ')
                console.print(f"    [dim]{msg}[/dim]")
        if len(errors) > 3:
            console.print(f"  [dim]... and {len(errors) - 3} more[/dim]")
        console.print()


@analytics_app.command("export")
def analytics_export(
    output_file: Annotated[
        Path, typer.Argument(help="Output file path")
    ] = Path("decomp_analytics.json"),
    since_days: Annotated[
        int, typer.Option("--since", "-s", help="Analyze sessions from last N days")
    ] = 30,
    project: Annotated[
        str, typer.Option("--project", "-p", help="Project filter")
    ] = "melee-decomp",
):
    """Export full analytics data to JSON for external analysis."""
    analyzer = DecompAnalyzer(project_filter=project)
    analyzer.analyze_all(since_days=since_days)
    metrics = analyzer.compute_aggregate_metrics()

    export_data = {
        "metadata": {
            "exported_at": str(metrics.total_sessions),
            "since_days": since_days,
            "project_filter": project,
        },
        "aggregate_metrics": {
            "total_sessions": metrics.total_sessions,
            "total_functions_attempted": metrics.total_functions_attempted,
            "total_functions_completed": metrics.total_functions_completed,
            "overall_success_rate": metrics.overall_success_rate,
            "commit_success_rate": metrics.commit_success_rate,
            "worktree_correct_rate": metrics.worktree_correct_rate,
            "build_first_try_rate": metrics.build_first_try_rate,
            "dry_run_usage_rate": metrics.dry_run_usage_rate,
            "avg_tokens_per_function": metrics.avg_tokens_per_function,
            "avg_turns_per_function": metrics.avg_turns_per_function,
            "avg_iterations_per_function": metrics.avg_iterations_per_function,
            "avg_duration_seconds": metrics.avg_duration_per_function.total_seconds() if metrics.avg_duration_per_function else None,
            "total_errors": metrics.total_errors,
            "errors_per_function": metrics.errors_per_function,
            "errors_by_category": metrics.errors_by_category,
            "avg_initial_match": metrics.avg_initial_match,
            "avg_final_match": metrics.avg_final_match,
            "thrashing_rate": metrics.thrashing_rate,
        },
        "distributions": {
            "final_match_pct": metrics.final_match_distribution,
            "tokens": metrics.tokens_distribution,
            "turns": metrics.turns_distribution,
        },
        "function_details": analyzer.get_function_details(),
        "sessions": [
            {
                "session_id": s.session_id,
                "project": s.project,
                "started_at": s.started_at.isoformat() if s.started_at else None,
                "ended_at": s.ended_at.isoformat() if s.ended_at else None,
                "duration_seconds": s.duration.total_seconds() if s.duration else None,
                "functions_attempted": s.functions_attempted,
                "functions_completed": s.functions_completed,
                "total_tokens": s.total_input_tokens + s.total_output_tokens,
                "total_turns": s.total_turns,
                "tool_counts": s.tool_counts,
                "tool_errors": s.tool_errors,
            }
            for s in analyzer.sessions
        ],
    }

    with open(output_file, 'w') as f:
        json.dump(export_data, f, indent=2)

    console.print(f"[green]Exported to:[/green] {output_file}")
    console.print(f"  Sessions: {len(analyzer.sessions)}")
    console.print(f"  Functions: {len(export_data['function_details'])}")


@analytics_app.command("trends")
def analytics_trends(
    since_days: Annotated[
        int, typer.Option("--since", "-s", help="Analyze sessions from last N days")
    ] = 30,
    project: Annotated[
        str, typer.Option("--project", "-p", help="Project filter")
    ] = "melee-decomp",
    metric: Annotated[
        str, typer.Option("--metric", "-m", help="Metric to plot: success, tokens, errors, match, all")
    ] = "all",
    granularity: Annotated[
        str, typer.Option("--granularity", "-g", help="Time granularity: day, week")
    ] = "day",
    width: Annotated[
        int, typer.Option("--width", "-w", help="Chart width in characters")
    ] = 80,
    height: Annotated[
        int, typer.Option("--height", help="Chart height in characters")
    ] = 15,
):
    """Show trends of key metrics over time with TUI graphs.

    Displays time-series charts for:
    - success: Functions completed vs attempted per period
    - tokens: Average tokens per function over time
    - errors: Error count per period
    - match: Average final match % over time
    - all: Show all metrics (default)

    Examples:
        melee-agent analytics trends --since 60 --metric success
        melee-agent analytics trends --granularity week --metric all
    """
    try:
        import plotext as plt
    except ImportError:
        console.print("[red]plotext not installed. Install with: pip install plotext[/red]")
        return

    analyzer = DecompAnalyzer(project_filter=project)
    console.print(f"[dim]Analyzing sessions from last {since_days} days...[/dim]\n")

    sessions = analyzer.analyze_all(since_days=since_days)

    if not sessions:
        console.print("[yellow]No decomp sessions found[/yellow]")
        return

    # Collect all function data with timestamps
    function_data = []
    for session in sessions:
        for func in session.functions:
            if func.started_at:
                function_data.append({
                    'timestamp': func.started_at,
                    'committed': func.committed,
                    'tokens': func.input_tokens + func.output_tokens,
                    'errors': len(func.errors),
                    'final_match': func.final_match_pct or 0,
                    'iterations': func.compile_count,
                })

    if not function_data:
        console.print("[yellow]No function attempts with timestamps found[/yellow]")
        return

    # Sort by timestamp
    function_data.sort(key=lambda x: x['timestamp'])

    # Determine time buckets
    if granularity == "week":
        def get_bucket(dt: datetime) -> str:
            # Get start of week (Monday)
            start = dt - timedelta(days=dt.weekday())
            return start.strftime("%m/%d")
        bucket_label = "Week of"
    else:  # day
        def get_bucket(dt: datetime) -> str:
            return dt.strftime("%m/%d")
        bucket_label = "Date"

    # Aggregate data by time bucket
    buckets: dict[str, dict] = defaultdict(lambda: {
        'attempted': 0,
        'completed': 0,
        'total_tokens': 0,
        'total_errors': 0,
        'total_match': 0,
        'match_count': 0,
    })

    for func in function_data:
        bucket = get_bucket(func['timestamp'])
        buckets[bucket]['attempted'] += 1
        if func['committed']:
            buckets[bucket]['completed'] += 1
        buckets[bucket]['total_tokens'] += func['tokens']
        buckets[bucket]['total_errors'] += func['errors']
        if func['final_match'] > 0:
            buckets[bucket]['total_match'] += func['final_match']
            buckets[bucket]['match_count'] += 1

    # Sort buckets by date
    sorted_buckets = sorted(buckets.items(), key=lambda x: x[0])
    dates = [b[0] for b in sorted_buckets]
    data = [b[1] for b in sorted_buckets]

    if len(dates) < 2:
        console.print("[yellow]Not enough data points for trends (need at least 2 time periods)[/yellow]")
        return

    # Calculate metrics for each bucket
    success_rates = []
    avg_tokens = []
    error_counts = []
    avg_matches = []
    attempted_counts = []
    completed_counts = []

    for d in data:
        # Success rate
        rate = (d['completed'] / d['attempted'] * 100) if d['attempted'] > 0 else 0
        success_rates.append(rate)

        # Average tokens (in thousands)
        avg_tok = (d['total_tokens'] / d['attempted'] / 1000) if d['attempted'] > 0 else 0
        avg_tokens.append(avg_tok)

        # Error count
        error_counts.append(d['total_errors'])

        # Average match %
        avg_match = (d['total_match'] / d['match_count']) if d['match_count'] > 0 else 0
        avg_matches.append(avg_match)

        # Counts
        attempted_counts.append(d['attempted'])
        completed_counts.append(d['completed'])

    # Configure plotext - use numeric x-axis with string labels
    plt.theme("pro")
    plt.plotsize(width, height)

    # Use numeric indices for x-axis
    x_indices = list(range(len(dates)))

    metrics_to_show = ["success", "tokens", "errors", "match"] if metric == "all" else [metric]

    for m in metrics_to_show:
        plt.clear_figure()
        plt.plotsize(width, height)

        if m == "success":
            plt.title("Success Rate Over Time")
            plt.xlabel(bucket_label)
            plt.ylabel("Count / Success %")

            # Plot bars for attempted and completed
            plt.bar(x_indices, attempted_counts, label="Attempted", color="blue")
            plt.bar(x_indices, completed_counts, label="Completed", color="green")

            # Add trend line for success rate
            if len(success_rates) > 2:
                plt.plot(x_indices, success_rates, label="Success %", color="yellow", marker="dot")

            plt.ylim(0, max(attempted_counts) * 1.2 if attempted_counts else 10)

        elif m == "tokens":
            plt.title("Average Tokens per Function (K)")
            plt.xlabel(bucket_label)
            plt.ylabel("Tokens (K)")
            plt.bar(x_indices, avg_tokens, color="cyan")

            # Add trend line
            if len(avg_tokens) > 2:
                # Simple moving average
                window = min(3, len(avg_tokens))
                smoothed = []
                for i in range(len(avg_tokens)):
                    start = max(0, i - window + 1)
                    smoothed.append(sum(avg_tokens[start:i+1]) / (i - start + 1))
                plt.plot(x_indices, smoothed, label="Trend", color="yellow", marker="dot")

        elif m == "errors":
            plt.title("Errors per Time Period")
            plt.xlabel(bucket_label)
            plt.ylabel("Error Count")
            plt.bar(x_indices, error_counts, color="red")

        elif m == "match":
            plt.title("Average Final Match %")
            plt.xlabel(bucket_label)
            plt.ylabel("Match %")
            plt.bar(x_indices, avg_matches, color="green")
            plt.ylim(0, 105)

            # Add 95% threshold line
            plt.hline(95, color="yellow")

        # Set x-axis labels (show subset if too many)
        if len(dates) <= 10:
            plt.xticks(x_indices, dates)
        else:
            # Show every Nth label
            step = max(1, len(dates) // 8)
            ticks = x_indices[::step]
            labels = dates[::step]
            plt.xticks(ticks, labels)

        plt.show()
        print()  # Spacing between charts

    # Summary statistics
    console.print(Panel.fit(
        f"[bold]Trend Summary[/bold]\n"
        f"[dim]Period: {dates[0]} to {dates[-1]} ({len(dates)} {granularity}s)[/dim]\n\n"
        f"Functions: {sum(attempted_counts)} attempted, {sum(completed_counts)} completed\n"
        f"Success rate: {sum(completed_counts)/sum(attempted_counts)*100:.1f}% overall\n"
        f"Avg tokens: {sum(d['total_tokens'] for d in data)/sum(attempted_counts)/1000:.1f}K per function\n"
        f"Total errors: {sum(error_counts)}",
        border_style="cyan"
    ))

    # Show trend direction
    if len(success_rates) >= 3:
        first_half = sum(success_rates[:len(success_rates)//2]) / (len(success_rates)//2)
        second_half = sum(success_rates[len(success_rates)//2:]) / (len(success_rates) - len(success_rates)//2)
        trend = second_half - first_half

        if trend > 5:
            console.print(f"[green]Success rate trending UP (+{trend:.1f}%)[/green]")
        elif trend < -5:
            console.print(f"[red]Success rate trending DOWN ({trend:.1f}%)[/red]")
        else:
            console.print(f"[dim]Success rate stable ({trend:+.1f}%)[/dim]")
