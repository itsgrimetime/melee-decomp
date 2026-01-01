"""Validate state command."""

import asyncio
import json
from pathlib import Path
from typing import Annotated, Optional

import typer

from .._common import (
    console,
    detect_local_api_url,
)
from ._helpers import find_best_local_scratch
from src.db import get_db


def validate_command(
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
                   production_scratch_slug, is_committed, pr_url, pr_state, pr_number, branch,
                   build_status, build_diagnosis
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
        build_status = func.get('build_status')

        # Determine what status SHOULD be based on available data
        if pr_state == 'MERGED':
            expected_status = 'merged'
        elif pr_state == 'OPEN':
            expected_status = 'in_review'
        elif pr_state == 'CLOSED':
            # Closed but not merged - work was rejected/abandoned
            expected_status = 'matched' if match_pct >= 95 else 'in_progress' if match_pct > 0 else 'unclaimed'
        elif is_committed:
            # Check if build is broken - use committed_needs_fix status
            if build_status == 'broken':
                expected_status = 'committed_needs_fix'
            else:
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
                slug, pct = find_best_local_scratch(func_name)
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
        from .._common import DEFAULT_MELEE_ROOT

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
                from ..pr import _get_cached_baseline_path, _check_upstream_status

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
    if 'missing_local_scratch' in by_type:
        suggestions.append("missing_local_scratch: run with --fix to search, or 'melee-agent extract get <func> --create-scratch'")
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
