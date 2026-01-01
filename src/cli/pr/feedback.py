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
        console.print(f"  Matching functions: {decomp_report.get('matching_functions', 0)}")
        console.print(f"  Matching bytes: {decomp_report.get('matching_bytes', 0):,} / {decomp_report.get('total_bytes', 0):,}")
        console.print(f"  Completion: {decomp_report.get('completion_percent', 0):.2f}%")

    # Merge status
    if merge_status:
        console.print(f"\n[bold]Merge Status:[/bold]")
        if merge_status.get('state') == 'MERGED':
            console.print(f"  [green]Merged by {merge_status.get('merged_by', 'unknown')}[/green]")
        elif merge_status.get('mergeable') == 'CONFLICTING':
            console.print("  [red]Has conflicts - needs rebase[/red]")
        elif merge_status.get('mergeable') == 'MERGEABLE':
            console.print("  [green]Ready to merge[/green]")
        else:
            console.print(f"  Mergeable: {merge_status.get('mergeable', 'unknown')}")

    return None
