"""Validation sync commands: validate, dedup, find-duplicates."""

import asyncio
import json
from typing import Annotated, Optional

import typer

from .._common import (
    console,
    get_local_api_url,
    db_upsert_function,
    db_upsert_scratch,
)


def validate_command(
    local_url: Annotated[
        Optional[str], typer.Option("--local-url", "-u", help="Local decomp.me instance URL (auto-detected if not specified)")
    ] = None,
    min_match: Annotated[
        float, typer.Option("--min-match", help="Minimum match percentage to validate")
    ] = 95.0,
    limit: Annotated[
        int, typer.Option("--limit", "-n", help="Maximum scratches to validate")
    ] = 50,
    fix: Annotated[
        bool, typer.Option("--fix", help="Automatically fix issues where possible")
    ] = False,
    output_json: Annotated[
        bool, typer.Option("--json", help="Output as JSON")
    ] = False,
):
    """Validate scratches before syncing to production.

    Checks each scratch for:
    - Exists on local server
    - Scratch name matches expected function name
    - Code contains the function name (catches wrong-code bugs)
    - Match % matches recorded value
    - No duplicate scratches for same function

    Use --fix to automatically resolve issues where possible.
    """
    # Auto-detect local URL if not provided
    if local_url is None:
        local_url = get_local_api_url()
        console.print(f"[dim]Auto-detected local server: {local_url}[/dim]")

    # Query from database
    from src.db import get_db

    db = get_db()
    candidates = []

    with db.connection() as conn:
        cursor = conn.execute("""
            SELECT function_name, match_percent, local_scratch_slug, production_scratch_slug
            FROM functions
            WHERE match_percent >= ?
            AND local_scratch_slug IS NOT NULL
            AND production_scratch_slug IS NULL
            ORDER BY match_percent DESC
            LIMIT ?
        """, (min_match, limit))

        for row in cursor.fetchall():
            candidates.append({
                'function': row['function_name'],
                'slug': row['local_scratch_slug'],
                'recorded_match': row['match_percent'] or 0,
            })

    if not candidates:
        console.print("[green]No unsynced scratches to validate[/green]")
        return

    console.print(f"[bold]Validating {len(candidates)} scratches...[/bold]\n")

    from src.client import DecompMeAPIClient

    # Track duplicates (multiple scratches for same function)
    func_to_slugs: dict[str, list[str]] = {}
    for c in candidates:
        func = c['function']
        if func not in func_to_slugs:
            func_to_slugs[func] = []
        func_to_slugs[func].append(c['slug'])

    duplicates = {f: slugs for f, slugs in func_to_slugs.items() if len(slugs) > 1}

    async def do_validate():
        results = {
            'valid': [],
            'issues': [],
            'errors': [],
            'duplicates': [],
        }

        async with DecompMeAPIClient(base_url=local_url) as client:
            for i, candidate in enumerate(candidates):
                func_name = candidate['function']
                slug = candidate['slug']
                recorded_match = candidate['recorded_match']

                console.print(f"[dim]Validating {func_name} ({i+1}/{len(candidates)})...[/dim]", end="")

                issues = []

                try:
                    scratch = await asyncio.wait_for(
                        client.get_scratch(slug),
                        timeout=10.0
                    )

                    # Check 1: Name matches
                    if scratch.name != func_name:
                        issues.append({
                            'type': 'name_mismatch',
                            'expected': func_name,
                            'actual': scratch.name,
                            'message': f"Scratch name '{scratch.name}' doesn't match function '{func_name}'",
                        })

                    # Check 2: Code contains function name
                    if func_name not in scratch.source_code:
                        # Also check if scratch name is in code (might be renamed)
                        if scratch.name not in scratch.source_code:
                            issues.append({
                                'type': 'wrong_code',
                                'message': f"Function name not found in scratch code",
                            })
                        else:
                            issues.append({
                                'type': 'code_uses_scratch_name',
                                'message': f"Code uses scratch name '{scratch.name}' not '{func_name}'",
                            })

                    # Check 3: Match % is reasonable
                    if scratch.max_score > 0:
                        actual_match = (scratch.max_score - scratch.score) / scratch.max_score * 100
                        if abs(actual_match - recorded_match) > 5:
                            issues.append({
                                'type': 'match_mismatch',
                                'expected': recorded_match,
                                'actual': actual_match,
                                'message': f"Match % differs: recorded {recorded_match:.1f}%, actual {actual_match:.1f}%",
                            })

                    # Check 4: Duplicate
                    if func_name in duplicates:
                        issues.append({
                            'type': 'duplicate',
                            'slugs': duplicates[func_name],
                            'message': f"Multiple scratches for this function: {', '.join(duplicates[func_name])}",
                        })

                    entry = {
                        'function': func_name,
                        'slug': slug,
                        'scratch_name': scratch.name,
                        'recorded_match': recorded_match,
                        'actual_match': (scratch.max_score - scratch.score) / scratch.max_score * 100 if scratch.max_score > 0 else 0,
                        'issues': issues,
                    }

                    if issues:
                        results['issues'].append(entry)
                        console.print(f" [yellow]{len(issues)} issue(s)[/yellow]")
                    else:
                        results['valid'].append(entry)
                        console.print(f" [green]OK[/green]")

                except asyncio.TimeoutError:
                    results['errors'].append({
                        'function': func_name,
                        'slug': slug,
                        'error': 'timeout (10s)',
                    })
                    console.print(f" [red]timeout[/red]")
                except Exception as e:
                    results['errors'].append({
                        'function': func_name,
                        'slug': slug,
                        'error': str(e),
                    })
                    console.print(f" [red]error[/red]")

        return results

    results = asyncio.run(do_validate())

    if output_json:
        print(json.dumps(results, indent=2))
        return

    # Display results
    if results['valid']:
        console.print(f"[green]✓ Valid scratches: {len(results['valid'])}[/green]")

    if results['issues']:
        console.print(f"\n[yellow]⚠ Issues found: {len(results['issues'])}[/yellow]\n")

        for entry in results['issues']:
            console.print(f"[cyan]{entry['function']}[/cyan] ({entry['slug']})")
            for issue in entry['issues']:
                issue_type = issue['type']
                if issue_type == 'name_mismatch':
                    console.print(f"  [yellow]• Name mismatch:[/yellow] scratch='{issue['actual']}', expected='{issue['expected']}'")
                elif issue_type == 'wrong_code':
                    console.print(f"  [red]• Wrong code:[/red] function name not in scratch code")
                elif issue_type == 'code_uses_scratch_name':
                    console.print(f"  [yellow]• Code mismatch:[/yellow] {issue['message']}")
                elif issue_type == 'match_mismatch':
                    console.print(f"  [yellow]• Match differs:[/yellow] recorded {issue['expected']:.1f}% vs actual {issue['actual']:.1f}%")
                elif issue_type == 'duplicate':
                    console.print(f"  [yellow]• Duplicate:[/yellow] {', '.join(issue['slugs'])}")
            console.print()

    if results['errors']:
        console.print(f"\n[red]✗ Errors: {len(results['errors'])}[/red]")
        for err in results['errors'][:5]:
            console.print(f"  {err['function']} ({err['slug']}): {err['error']}")
        if len(results['errors']) > 5:
            console.print(f"  [dim]... and {len(results['errors']) - 5} more[/dim]")

    # Summary
    console.print(f"\n[bold]Summary:[/bold]")
    console.print(f"  Valid: {len(results['valid'])}")
    console.print(f"  Issues: {len(results['issues'])}")
    console.print(f"  Errors: {len(results['errors'])}")

    if results['issues'] and not fix:
        console.print(f"\n[dim]Run with --fix to attempt automatic fixes[/dim]")
        console.print(f"[dim]Or manually inspect and fix issues before syncing[/dim]")


def dedup_command(
    local_url: Annotated[
        Optional[str], typer.Option("--local-url", "-u", help="Local decomp.me instance URL (auto-detected if not specified)")
    ] = None,
    min_match: Annotated[
        float, typer.Option("--min-match", help="Minimum match percentage")
    ] = 95.0,
    dry_run: Annotated[
        bool, typer.Option("--dry-run", help="Show what would change without applying")
    ] = False,
    output_json: Annotated[
        bool, typer.Option("--json", help="Output as JSON")
    ] = False,
):
    """De-duplicate scratches - pick best scratch when multiple exist for same function.

    When multiple scratches exist for the same function, compares them and
    picks the best one based on:
    1. Highest match percentage
    2. Name matches function name
    3. Most recent update

    Updates database to use the winning scratch.
    """
    # Auto-detect local URL if not provided
    if local_url is None:
        local_url = get_local_api_url()
        console.print(f"[dim]Auto-detected local server: {local_url}[/dim]")

    # Query from database
    from src.db import get_db
    db = get_db()

    # Get current function data for comparison
    with db.connection() as conn:
        cursor = conn.execute("SELECT function_name, local_scratch_slug FROM functions")
        completed = {row['function_name']: {'scratch_slug': row['local_scratch_slug']} for row in cursor.fetchall()}

    # Find functions with multiple candidate scratches

    with db.connection() as conn:
        cursor = conn.execute('''
            SELECT function_name, GROUP_CONCAT(slug) as slugs, COUNT(*) as cnt
            FROM scratches
            WHERE function_name IS NOT NULL AND instance = 'local'
            GROUP BY function_name
            HAVING cnt > 1
        ''')
        duplicates = [dict(row) for row in cursor.fetchall()]

    if not duplicates:
        # Also check completed_functions for duplicates by slug
        slug_to_funcs: dict[str, list[str]] = {}
        for func_name, info in completed.items():
            slug = info.get('scratch_slug')
            if slug:
                if slug not in slug_to_funcs:
                    slug_to_funcs[slug] = []
                slug_to_funcs[slug].append(func_name)

        # Find slugs used by multiple functions (different issue - same scratch, multiple functions)
        multi_use = {s: funcs for s, funcs in slug_to_funcs.items() if len(funcs) > 1}
        if multi_use:
            console.print("[yellow]Found scratches used by multiple functions:[/yellow]")
            for slug, funcs in list(multi_use.items())[:10]:
                console.print(f"  {slug}: {', '.join(funcs)}")
            console.print("\n[dim]This may indicate wrong-code issues[/dim]")
            return

        console.print("[green]No duplicate scratches found[/green]")
        return

    console.print(f"[bold]Found {len(duplicates)} functions with multiple scratches[/bold]\n")

    from src.client import DecompMeAPIClient

    async def do_dedup():
        results = []

        async with DecompMeAPIClient(base_url=local_url) as client:
            for i, dup in enumerate(duplicates):
                func_name = dup['function_name']
                slugs = dup['slugs'].split(',')

                console.print(f"[dim]Checking {func_name} ({i+1}/{len(duplicates)})...[/dim]")

                # Fetch all scratches for this function
                scratch_info = []
                for slug in slugs:
                    try:
                        console.print(f"[dim]  Fetching {slug}...[/dim]", end="")
                        scratch = await asyncio.wait_for(
                            client.get_scratch(slug),
                            timeout=10.0
                        )
                        match_pct = (scratch.max_score - scratch.score) / scratch.max_score * 100 if scratch.max_score > 0 else 0
                        scratch_info.append({
                            'slug': slug,
                            'name': scratch.name,
                            'match_pct': match_pct,
                            'name_matches': scratch.name == func_name,
                            'code_has_func': func_name in scratch.source_code,
                        })
                        console.print(f" [green]OK[/green]")
                    except asyncio.TimeoutError:
                        scratch_info.append({
                            'slug': slug,
                            'error': 'timeout',
                        })
                        console.print(f" [red]timeout[/red]")
                    except Exception as e:
                        scratch_info.append({
                            'slug': slug,
                            'error': str(e),
                        })
                        console.print(f" [red]error[/red]")

                # Score each scratch
                for s in scratch_info:
                    if 'error' in s:
                        s['score'] = -1000
                    else:
                        s['score'] = s['match_pct']
                        if s['name_matches']:
                            s['score'] += 10
                        if s['code_has_func']:
                            s['score'] += 5

                # Pick winner
                valid = [s for s in scratch_info if 'error' not in s]
                if valid:
                    winner = max(valid, key=lambda x: x['score'])
                    losers = [s for s in valid if s['slug'] != winner['slug']]
                else:
                    winner = None
                    losers = []

                results.append({
                    'function': func_name,
                    'scratches': scratch_info,
                    'winner': winner,
                    'losers': losers,
                })

        return results

    results = asyncio.run(do_dedup())

    if output_json:
        print(json.dumps(results, indent=2))
        return

    changes_made = 0
    for r in results:
        func_name = r['function']
        winner = r['winner']

        if not winner:
            console.print(f"[red]{func_name}:[/red] No valid scratches found")
            continue

        console.print(f"[cyan]{func_name}:[/cyan]")
        console.print(f"  [green]Winner:[/green] {winner['slug']} ({winner['match_pct']:.1f}%)")
        for loser in r['losers']:
            console.print(f"  [dim]Loser:[/dim] {loser['slug']} ({loser['match_pct']:.1f}%)")

        # Update DB if needed
        current_slug = completed.get(func_name, {}).get('scratch_slug')
        if current_slug != winner['slug']:
            console.print(f"  [yellow]→ Updating from {current_slug} to {winner['slug']}[/yellow]")
            if not dry_run:
                db_upsert_function(func_name, local_scratch_slug=winner['slug'], match_percent=winner['match_pct'])
            changes_made += 1
        console.print()

    if changes_made > 0 and not dry_run:
        console.print(f"[green]Updated {changes_made} function(s) in database[/green]")
    elif dry_run and changes_made > 0:
        console.print(f"[yellow]Would update {changes_made} function(s) (dry run)[/yellow]")


def find_duplicates_command(
    local_url: Annotated[
        Optional[str], typer.Option("--local-url", "-u", help="Local decomp.me instance URL (auto-detected if not specified)")
    ] = None,
    min_match: Annotated[
        float, typer.Option("--min-match", help="Minimum match percentage to check")
    ] = 95.0,
    limit: Annotated[
        int, typer.Option("--limit", "-n", help="Maximum functions to check")
    ] = 100,
    update_db: Annotated[
        bool, typer.Option("--update-db/--no-update-db", help="Update scratches table with found scratches")
    ] = True,
    output_json: Annotated[
        bool, typer.Option("--json", help="Output as JSON")
    ] = False,
):
    """Find functions with multiple scratches on the server.

    Queries the decomp.me API for each tracked function to find cases where
    multiple scratches exist for the same function. This catches duplicates
    created by different agents or repeated attempts.

    Reports:
    - Functions with multiple scratches
    - Match percentages for each scratch
    - Which scratch is currently tracked
    """
    # Auto-detect local URL if not provided
    if local_url is None:
        local_url = get_local_api_url()
        console.print(f"[dim]Auto-detected local server: {local_url}[/dim]")

    from src.db import get_db
    db = get_db()

    # Get functions to check from database
    candidates = []
    with db.connection() as conn:
        cursor = conn.execute("""
            SELECT function_name, local_scratch_slug, match_percent
            FROM functions
            WHERE match_percent >= ?
            ORDER BY match_percent DESC
        """, (min_match,))
        for row in cursor.fetchall():
            candidates.append({
                'function': row['function_name'],
                'tracked_slug': row['local_scratch_slug'],
                'match_pct': row['match_percent'] or 0,
            })

    candidates.sort(key=lambda x: -x['match_pct'])
    candidates = candidates[:limit]

    if not candidates:
        console.print("[yellow]No functions to check[/yellow]")
        return

    console.print(f"[bold]Searching for duplicates across {len(candidates)} functions...[/bold]\n")

    import httpx

    async def search_duplicates():
        duplicates = []
        name_mismatches = []
        checked = 0
        errors = 0
        scratches_added = 0

        async with httpx.AsyncClient(base_url=local_url, timeout=15.0) as client:
            for i, candidate in enumerate(candidates):
                func_name = candidate['function']
                tracked_slug = candidate['tracked_slug']

                console.print(f"[dim]Searching {func_name} ({i+1}/{len(candidates)})...[/dim]", end="")

                # First, check if the tracked scratch has the correct name
                if tracked_slug:
                    try:
                        tracked_resp = await asyncio.wait_for(
                            client.get(f'/api/scratch/{tracked_slug}'),
                            timeout=10.0
                        )
                        if tracked_resp.status_code == 200:
                            tracked_data = tracked_resp.json()
                            tracked_name = tracked_data.get('name', '')
                            if tracked_name and tracked_name != func_name:
                                name_mismatches.append({
                                    'function': func_name,
                                    'slug': tracked_slug,
                                    'scratch_name': tracked_name,
                                    'match_pct': candidate['match_pct'],
                                })
                    except (asyncio.TimeoutError, Exception):
                        pass  # Will be caught by search below

                try:
                    resp = await asyncio.wait_for(
                        client.get('/api/scratch', params={'search': func_name}),
                        timeout=10.0
                    )

                    if resp.status_code != 200:
                        console.print(f" [red]HTTP {resp.status_code}[/red]")
                        errors += 1
                        continue

                    data = resp.json()
                    results = data.get('results', [])

                    # Filter to exact name matches
                    exact_matches = [r for r in results if r.get('name') == func_name]

                    # Build scratch info list
                    scratch_list = []
                    for r in exact_matches:
                        score = r.get('score', 0)
                        max_score = r.get('max_score', 0)
                        match_pct = ((max_score - score) / max_score * 100) if max_score > 0 else 0
                        scratch_list.append({
                            'slug': r.get('slug'),
                            'score': score,
                            'max_score': max_score,
                            'match_pct': match_pct,
                            'is_tracked': r.get('slug') == tracked_slug,
                        })

                        # Update database with this scratch
                        if update_db:
                            db_upsert_scratch(
                                r.get('slug'),
                                instance='local',
                                base_url=local_url,
                                function_name=func_name,
                                score=score,
                                max_score=max_score,
                            )
                            scratches_added += 1

                    if len(exact_matches) > 1:
                        console.print(f" [yellow]{len(exact_matches)} scratches![/yellow]")
                        duplicates.append({
                            'function': func_name,
                            'tracked_slug': tracked_slug,
                            'scratches': scratch_list,
                        })
                    elif len(exact_matches) == 1:
                        console.print(f" [green]1 scratch[/green]")
                    else:
                        console.print(f" [dim]0 exact matches[/dim]")

                    checked += 1

                except asyncio.TimeoutError:
                    console.print(f" [red]timeout[/red]")
                    errors += 1
                except Exception as e:
                    console.print(f" [red]error: {e}[/red]")
                    errors += 1

        return {
            'duplicates': duplicates,
            'name_mismatches': name_mismatches,
            'checked': checked,
            'errors': errors,
            'scratches_added': scratches_added,
        }

    results = asyncio.run(search_duplicates())

    if output_json:
        print(json.dumps(results, indent=2))
        return

    console.print(f"\n[bold]Results:[/bold]")
    console.print(f"  Checked: {results['checked']}")
    console.print(f"  Errors: {results['errors']}")
    console.print(f"  Functions with duplicates: {len(results['duplicates'])}")
    console.print(f"  Name mismatches: {len(results['name_mismatches'])}")
    if update_db:
        console.print(f"  Scratches added to DB: {results['scratches_added']}")

    # Show name mismatches first (more critical issue)
    if results['name_mismatches']:
        console.print(f"\n[red]⚠ Name mismatches (tracked scratch has wrong name):[/red]\n")

        for mismatch in results['name_mismatches']:
            func_name = mismatch['function']
            slug = mismatch['slug']
            scratch_name = mismatch['scratch_name']
            match_pct = mismatch['match_pct']

            console.print(f"[cyan]{func_name}[/cyan] ({match_pct:.1f}%)")
            console.print(f"  Tracked: {slug}")
            console.print(f"  [red]Scratch name: {scratch_name}[/red]")
            console.print(f"  [dim]Expected: {func_name}[/dim]")
            console.print()

        console.print("[bold]These need manual review - the scratch may contain wrong code![/bold]\n")

    if results['duplicates']:
        console.print(f"\n[yellow]Functions with multiple scratches:[/yellow]\n")

        for dup in results['duplicates']:
            func_name = dup['function']
            tracked_slug = dup['tracked_slug']

            console.print(f"[cyan]{func_name}[/cyan]")
            for scratch in sorted(dup['scratches'], key=lambda x: -x['match_pct']):
                slug = scratch['slug']
                pct = scratch['match_pct']
                is_tracked = scratch['is_tracked']

                if is_tracked:
                    console.print(f"  [green]★ {slug}[/green] ({pct:.1f}%) [dim]← tracked[/dim]")
                else:
                    console.print(f"  [dim]  {slug}[/dim] ({pct:.1f}%)")
            console.print()

        if update_db:
            console.print("[dim]Run 'sync dedup' to pick winners and update tracking[/dim]")
        else:
            console.print("[dim]Run with --update-db to populate scratches table, then 'sync dedup' to resolve[/dim]")

    if not results['duplicates'] and not results['name_mismatches']:
        console.print("\n[green]✓ No issues found![/green]")
