"""Feedback PR command."""

import json
from typing import Annotated

import typer
from rich.table import Table

from .._common import console, extract_pr_info
from ._helpers import (
    get_extended_pr_info,
    get_pr_checks,
    get_failed_check_logs,
    parse_build_errors,
    get_pr_review_comments,
    get_decomp_dev_report,
    get_pr_merge_status,
)


def feedback_command(
    pr_refs: Annotated[
        list[str], typer.Argument(help="PR number(s) or URL(s) to analyze")
    ],
    include_logs: Annotated[
        bool, typer.Option("--logs", "-l", help="Include failed check logs")
    ] = False,
    output_json: Annotated[
        bool, typer.Option("--json", help="Output as JSON")
    ] = False,
):
    """Get comprehensive feedback on a PR including reviews, checks, and decomp.dev report.

    Shows:
    - Review comments and requested changes
    - CI check status and failure logs
    - decomp.dev matching statistics
    - Merge status and blockers
    """
    all_results = []
    for pr_ref in pr_refs:
        repo, pr_number = extract_pr_info(pr_ref)
        if not pr_number:
            console.print(f"[red]Invalid PR reference: {pr_ref}[/red]")
            raise typer.Exit(1)

        result = _feedback_single_pr(repo, pr_number, include_logs, output_json)
        if result:
            all_results.append(result)

        if not output_json and len(pr_refs) > 1 and pr_ref != pr_refs[-1]:
            console.print("\n" + "─" * 60 + "\n")

    if output_json and all_results:
        print(json.dumps(all_results if len(all_results) > 1 else all_results[0], indent=2))


def _feedback_single_pr(repo: str, pr_number: int, include_logs: bool, output_json: bool) -> dict | None:
    """Get feedback for a single PR."""

    # Gather all data
    pr_info = get_extended_pr_info(repo, pr_number)
    if not pr_info:
        console.print("[red]Could not fetch PR info[/red]")
        return None

    checks = get_pr_checks(repo, pr_number)
    comments = get_pr_review_comments(repo, pr_number)
    decomp_report = get_decomp_dev_report(repo, pr_number)
    merge_status = get_pr_merge_status(repo, pr_number)

    # Parse check results
    failed_checks = [c for c in checks if c.get('conclusion') == 'failure']
    pending_checks = [c for c in checks if c.get('state') in ('pending', 'in_progress')]
    passed_checks = [c for c in checks if c.get('conclusion') == 'success']

    # Get logs for failed checks if requested
    check_logs = {}
    if include_logs and failed_checks:
        for check in failed_checks[:3]:  # Limit to first 3 failures
            run_id = str(check.get('databaseId', ''))
            if run_id:
                logs = get_failed_check_logs(repo, run_id)
                if logs:
                    check_logs[check.get('name', 'unknown')] = logs

    # Build output
    output = {
        'pr_number': pr_number,
        'repo': repo,
        'title': pr_info.get('title', ''),
        'state': pr_info.get('state', 'unknown'),
        'review_decision': pr_info.get('reviewDecision'),
        'merge_status': merge_status,
        'checks': {
            'total': len(checks),
            'passed': len(passed_checks),
            'failed': len(failed_checks),
            'pending': len(pending_checks),
            'failed_names': [c.get('name') for c in failed_checks],
        },
        'comments': comments,
        'decomp_report': decomp_report,
    }

    if include_logs:
        output['check_logs'] = check_logs
        output['parsed_errors'] = {}
        for name, logs in check_logs.items():
            output['parsed_errors'][name] = parse_build_errors(logs)

    if output_json:
        return output

    # Display
    console.print(f"[bold]PR #{pr_number}[/bold]: {pr_info.get('title', '')}\n")

    # State
    state = pr_info.get('state', 'unknown')
    if state == 'MERGED':
        console.print("[green]✓ MERGED[/green]")
    elif state == 'CLOSED':
        console.print("[red]✗ CLOSED[/red]")
    else:
        console.print(f"[cyan]○ {state}[/cyan]")

    review = pr_info.get('reviewDecision')
    if review == 'APPROVED':
        console.print("[green]✓ Review: APPROVED[/green]")
    elif review == 'CHANGES_REQUESTED':
        console.print("[yellow]⚠ Review: CHANGES REQUESTED[/yellow]")
    elif review:
        console.print(f"  Review: {review}")

    # Checks
    console.print(f"\n[bold]Checks:[/bold] {len(passed_checks)} passed, {len(failed_checks)} failed, {len(pending_checks)} pending")
    
    if failed_checks:
        console.print("\n[red]Failed checks:[/red]")
        for check in failed_checks:
            console.print(f"  ✗ {check.get('name', 'unknown')}")
            if include_logs and check.get('name') in check_logs:
                errors = parse_build_errors(check_logs[check.get('name')])
                for err in errors[:5]:
                    if err.get('file'):
                        console.print(f"    [dim]{err['file']}:{err.get('line', '?')}: {err.get('message', '')}[/dim]")
                    else:
                        console.print(f"    [dim]{err.get('message', '')}[/dim]")
                if len(errors) > 5:
                    console.print(f"    [dim]... and {len(errors) - 5} more errors[/dim]")

    if pending_checks:
        console.print("\n[yellow]Pending checks:[/yellow]")
        for check in pending_checks:
            console.print(f"  ○ {check.get('name', 'unknown')}")

    # Review comments
    if comments:
        console.print(f"\n[bold]Review Comments ({len(comments)}):[/bold]")
        for comment in comments[:5]:
            user = comment.get('user', 'unknown')
            path = comment.get('path', '')
            body = comment.get('body', '')[:100]
            console.print(f"  [{user}] {path}: {body}...")
        if len(comments) > 5:
            console.print(f"  [dim]... and {len(comments) - 5} more[/dim]")

    # decomp.dev report
    if decomp_report:
        console.print(f"\n[bold]decomp.dev Report:[/bold]")

        # New bot format with delta
        if decomp_report.get('delta_bytes') is not None:
            delta_bytes = decomp_report.get('delta_bytes', 0)
            delta_pct = decomp_report.get('delta_percent', 0)
            completion = decomp_report.get('completion_percent', 0)

            # Color based on positive/negative delta
            if delta_bytes > 0:
                console.print(f"  [green]Matched code: {completion:.2f}% (+{delta_pct:.2f}%, +{delta_bytes:,} bytes)[/green]")
            elif delta_bytes < 0:
                console.print(f"  [red]Matched code: {completion:.2f}% ({delta_pct:.2f}%, {delta_bytes:,} bytes)[/red]")
            else:
                console.print(f"  Matched code: {completion:.2f}% (no change)")

            # Show broken matches prominently
            broken_count = decomp_report.get('broken_matches_count', 0)
            if broken_count > 0:
                console.print(f"  [bold red]💔 {broken_count} broken matches[/bold red]")
                broken_funcs = decomp_report.get('broken_matches', [])
                for func in broken_funcs[:5]:
                    console.print(f"    - {func}")
                if len(broken_funcs) > 5:
                    console.print(f"    [dim]... and {len(broken_funcs) - 5} more[/dim]")

            # Show regressions
            regression_count = decomp_report.get('regressions_count', 0)
            if regression_count > 0:
                console.print(f"  [yellow]📉 {regression_count} regressions[/yellow]")
                regression_funcs = decomp_report.get('regressions', [])
                for func in regression_funcs[:5]:
                    console.print(f"    - {func}")
                if len(regression_funcs) > 5:
                    console.print(f"    [dim]... and {len(regression_funcs) - 5} more[/dim]")

            # Show new matches
            new_count = decomp_report.get('new_matches_count', 0)
            if new_count > 0:
                console.print(f"  [green]✅ {new_count} new match{'es' if new_count > 1 else ''}[/green]")

            # Show improvements
            improvement_count = decomp_report.get('improvements_count', 0)
            if improvement_count > 0:
                console.print(f"  [cyan]📈 {improvement_count} improvement{'s' if improvement_count > 1 else ''}[/cyan]")

        else:
            # Old format (from PR body)
            console.print(f"  Matching functions: {decomp_report.get('matching_functions', 0)}")
            console.print(f"  Matching bytes: {decomp_report.get('matching_bytes', 0):,} / {decomp_report.get('total_bytes', 0):,}")
            console.print(f"  Completion: {decomp_report.get('completion_percent', 0):.2f}%")

    # Merge status (considering checks and regressions)
    console.print(f"\n[bold]Merge Status:[/bold]")
    if merge_status and merge_status.get('state') == 'MERGED':
        console.print(f"  [green]Merged by {merge_status.get('merged_by', 'unknown')}[/green]")
    else:
        blockers = []
        warnings_list = []

        # Check for conflicts
        if merge_status and merge_status.get('mergeable') == 'CONFLICTING':
            blockers.append("Has conflicts - needs rebase")

        # Check for failed CI
        if failed_checks:
            blockers.append(f"{len(failed_checks)} CI check(s) failing")

        # Check for broken matches
        if decomp_report:
            broken_count = decomp_report.get('broken_matches_count', 0)
            if broken_count > 0:
                blockers.append(f"{broken_count} broken match(es)")

            regression_count = decomp_report.get('regressions_count', 0)
            if regression_count > 0:
                warnings_list.append(f"{regression_count} regression(s)")

            if decomp_report.get('delta_bytes', 0) < 0:
                warnings_list.append("Net negative progress")

        if blockers:
            console.print(f"  [bold red]🚫 NOT ready to merge:[/bold red]")
            for blocker in blockers:
                console.print(f"    - {blocker}")
        elif warnings_list:
            console.print(f"  [yellow]⚠ Mergeable with warnings:[/yellow]")
            for warning in warnings_list:
                console.print(f"    - {warning}")
        elif merge_status and merge_status.get('mergeable') == 'MERGEABLE':
            console.print("  [green]✓ Ready to merge[/green]")
        else:
            console.print(f"  Mergeable: {merge_status.get('mergeable', 'unknown') if merge_status else 'unknown'}")

    return None
