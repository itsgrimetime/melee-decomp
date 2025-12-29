"""Sync commands - sync scratches to production decomp.me."""

import asyncio
import json
import os
import time
from pathlib import Path
from typing import Annotated, Optional

import typer
from rich.table import Table

from ._common import (
    console,
    DEFAULT_MELEE_ROOT,
    PRODUCTION_COOKIES_FILE,
    PRODUCTION_DECOMP_ME,
    SLUG_MAP_FILE,
    load_slug_map,
    save_slug_map,
    load_completed_functions,
    save_completed_functions,
    db_record_sync,
    db_upsert_function,
    db_upsert_scratch,
)

# API URL from environment
_api_base = os.environ.get("DECOMP_API_BASE", "")
DEFAULT_DECOMP_ME_URL = _api_base[:-4] if _api_base.endswith("/api") else _api_base

sync_app = typer.Typer(help="Sync scratches to production decomp.me")


def _require_api_url(api_url: str) -> None:
    """Validate that API URL is configured."""
    if not api_url:
        console.print("[red]Error: DECOMP_API_BASE environment variable is required[/red]")
        raise typer.Exit(1)


def _load_production_cookies() -> dict[str, str]:
    """Load production cookies from cache file."""
    if not PRODUCTION_COOKIES_FILE.exists():
        return {}
    try:
        with open(PRODUCTION_COOKIES_FILE, 'r') as f:
            return json.load(f)
    except (json.JSONDecodeError, IOError):
        return {}


def _save_production_cookies(cookies: dict[str, str]) -> None:
    """Save production cookies to cache file."""
    PRODUCTION_COOKIES_FILE.parent.mkdir(parents=True, exist_ok=True)
    with open(PRODUCTION_COOKIES_FILE, 'w') as f:
        json.dump(cookies, f, indent=2)


def _prompt_cf_clearance() -> str:
    """Prompt user for cf_clearance cookie."""
    console.print("\n[bold cyan]Cloudflare Authentication Required[/bold cyan]")
    console.print("The production decomp.me site requires a cf_clearance cookie.")
    console.print("\n[bold]To get this cookie:[/bold]")
    console.print("1. Open https://decomp.me in your browser")
    console.print("2. Complete the Cloudflare challenge if prompted")
    console.print("3. Open DevTools (F12) -> Application -> Cookies -> decomp.me")
    console.print("4. Copy the value of 'cf_clearance'\n")

    cf_clearance = typer.prompt("Enter cf_clearance cookie value")
    return cf_clearance.strip()


@sync_app.command("status")
def sync_status():
    """Check cf_clearance cookie status and test connection to production."""
    cookies = _load_production_cookies()

    if not cookies.get('cf_clearance'):
        console.print("[yellow]No cf_clearance cookie cached[/yellow]")
        console.print("[dim]Run 'melee-agent sync auth' to configure[/dim]")
        return

    console.print(f"[green]cf_clearance cookie cached[/green]")
    console.print(f"[dim]Cookie file: {PRODUCTION_COOKIES_FILE}[/dim]")

    console.print("\n[dim]Testing connection to production...[/dim]")
    import httpx

    try:
        with httpx.Client(
            cookies={'cf_clearance': cookies['cf_clearance']},
            headers={
                'User-Agent': 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10.15; rv:146.0) Gecko/20100101 Firefox/146.0',
            },
            follow_redirects=True,
            timeout=10.0,
        ) as client:
            resp = client.get(f"{PRODUCTION_DECOMP_ME}/api/compiler")
            if resp.status_code == 200:
                console.print("[green]Successfully connected to production decomp.me[/green]")
            elif resp.status_code == 403:
                console.print("[red]cf_clearance cookie expired or invalid[/red]")
                console.print("[dim]Run 'melee-agent sync auth' to refresh[/dim]")
            else:
                console.print(f"[yellow]Unexpected response: {resp.status_code}[/yellow]")
    except Exception as e:
        console.print(f"[red]Connection failed: {e}[/red]")


@sync_app.command("auth")
def sync_auth(
    cf_clearance: Annotated[
        Optional[str], typer.Option("--cf-clearance", help="cf_clearance cookie value")
    ] = None,
    session_id: Annotated[
        Optional[str], typer.Option("--session-id", help="sessionid cookie for authenticated uploads")
    ] = None,
):
    """Configure authentication for production decomp.me."""
    cookies = _load_production_cookies()

    if cf_clearance:
        cookies['cf_clearance'] = cf_clearance.strip()
    else:
        cookies['cf_clearance'] = _prompt_cf_clearance()

    if session_id:
        cookies['sessionid'] = session_id.strip()
    elif not cookies.get('sessionid'):
        if typer.confirm("Do you want to add a sessionid cookie? (allows uploads under your account)"):
            console.print("\n[bold]To get your sessionid:[/bold]")
            console.print("1. Log into https://decomp.me with GitHub")
            console.print("2. Open DevTools -> Application -> Cookies -> decomp.me")
            console.print("3. Copy the value of 'sessionid'\n")
            cookies['sessionid'] = typer.prompt("Enter sessionid cookie value").strip()

    _save_production_cookies(cookies)
    console.print(f"\n[green]Cookies saved to {PRODUCTION_COOKIES_FILE}[/green]")
    sync_status()


@sync_app.command("list")
def sync_list(
    min_match: Annotated[
        float, typer.Option("--min-match", help="Minimum match percentage to include")
    ] = 95.0,
    limit: Annotated[
        int, typer.Option("--limit", "-n", help="Maximum entries to show")
    ] = 50,
):
    """List completed functions that can be synced to production.

    Reads from completed_functions.json (our structured tracking file).
    """
    completed = load_completed_functions()
    slug_map = load_slug_map()

    # Build set of already-synced local slugs
    synced_local_slugs = {v.get('local_slug') for v in slug_map.values()}

    # Filter and prepare entries
    entries = []
    for func_name, info in completed.items():
        match_pct = info.get('match_percent', 0)
        local_slug = info.get('scratch_slug', '')

        if match_pct < min_match:
            continue
        if not local_slug:
            continue

        entries.append({
            'name': func_name,
            'match_pct': match_pct,
            'slug': local_slug,
            'synced': local_slug in synced_local_slugs,
        })

    entries.sort(key=lambda x: -x['match_pct'])
    entries = entries[:limit]

    if not entries:
        console.print("[yellow]No matching functions found[/yellow]")
        return

    table = Table(title=f"Functions to Sync (>= {min_match}% match)")
    table.add_column("Function", style="cyan")
    table.add_column("Match %", justify="right")
    table.add_column("Local Slug")
    table.add_column("Status")

    synced_count = 0
    for entry in entries:
        if entry['synced']:
            synced_count += 1
        table.add_row(
            entry['name'],
            f"{entry['match_pct']:.1f}%",
            entry['slug'],
            "[green]synced[/green]" if entry['synced'] else "[yellow]pending[/yellow]",
        )

    console.print(table)
    pending = len(entries) - synced_count
    console.print(f"\n[dim]Found {len(entries)} functions ({pending} pending, {synced_count} already synced)[/dim]")


@sync_app.command("production")
def sync_production(
    melee_root: Annotated[
        Path, typer.Option("--melee-root", "-m", help="Path to melee submodule")
    ] = DEFAULT_MELEE_ROOT,
    local_url: Annotated[
        str, typer.Option("--local-url", help="Local decomp.me instance URL")
    ] = DEFAULT_DECOMP_ME_URL,
    min_match: Annotated[
        float, typer.Option("--min-match", help="Minimum match percentage to sync")
    ] = 95.0,
    limit: Annotated[
        int, typer.Option("--limit", "-n", help="Maximum scratches to sync")
    ] = 10,
    dry_run: Annotated[
        bool, typer.Option("--dry-run", help="Show what would be synced without syncing")
    ] = False,
    force: Annotated[
        bool, typer.Option("--force", "-f", help="Re-sync even if already exists on production")
    ] = False,
):
    """Sync completed functions from local instance to production decomp.me.

    Reads from completed_functions.json to find functions with local slugs
    that haven't been synced to production yet.
    """
    _require_api_url(local_url)

    prod_cookies = _load_production_cookies()
    if not prod_cookies.get('cf_clearance'):
        console.print("[red]No cf_clearance cookie configured[/red]")
        console.print("[dim]Run 'melee-agent sync auth' first[/dim]")
        raise typer.Exit(1)

    completed = load_completed_functions()
    slug_map = load_slug_map()

    # Build set of already-synced local slugs
    synced_local_slugs = {v.get('local_slug') for v in slug_map.values()}

    # Filter entries from completed_functions.json
    to_sync = []
    already_synced_list = []
    for func_name, info in completed.items():
        match_pct = info.get('match_percent', 0)
        local_slug = info.get('scratch_slug', '')

        if match_pct < min_match:
            continue
        if not local_slug:
            continue

        entry = {
            'name': func_name,
            'slug': local_slug,
            'match_pct': match_pct,
        }

        if local_slug in synced_local_slugs:
            already_synced_list.append(entry)
        else:
            to_sync.append(entry)

    to_sync.sort(key=lambda x: -x['match_pct'])
    to_sync = to_sync[:limit]

    if not to_sync and not force:
        if already_synced_list:
            console.print(f"[yellow]All {len(already_synced_list)} functions already synced[/yellow]")
            console.print("[dim]Use --force to re-sync[/dim]")
        else:
            console.print("[yellow]No functions to sync[/yellow]")
        return

    if force and not to_sync:
        to_sync = already_synced_list[:limit]

    console.print(f"[bold]Syncing {len(to_sync)} functions to production...[/bold]\n")

    if dry_run:
        console.print("[cyan]DRY RUN - no changes will be made[/cyan]\n")

    from src.client import DecompMeAPIClient

    synced_file = PRODUCTION_COOKIES_FILE.parent / "synced_scratches.json"
    synced = {}
    if synced_file.exists():
        try:
            with open(synced_file, 'r') as f:
                synced = json.load(f)
        except (json.JSONDecodeError, IOError):
            pass

    async def do_sync():
        results = {"success": 0, "skipped": 0, "failed": 0, "details": []}

        async with DecompMeAPIClient(base_url=local_url) as local_client:
            import httpx

            prod_cookies_obj = httpx.Cookies()
            prod_cookies_obj.set("cf_clearance", prod_cookies['cf_clearance'], domain="decomp.me")
            if prod_cookies.get('sessionid'):
                prod_cookies_obj.set("sessionid", prod_cookies['sessionid'], domain="decomp.me")

            async with httpx.AsyncClient(
                base_url=PRODUCTION_DECOMP_ME,
                timeout=60.0,
                cookies=prod_cookies_obj,
                headers={
                    'User-Agent': 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10.15; rv:146.0) Gecko/20100101 Firefox/146.0',
                    'Accept': 'application/json',
                },
                follow_redirects=True,
            ) as prod_client:
                for entry in to_sync:
                    func_name = entry['name']
                    local_slug = entry['slug']
                    match_pct = entry['match_pct']

                    if local_slug in synced and not force:
                        console.print(f"[dim]Skipping {func_name} ({local_slug}) - already synced[/dim]")
                        results['skipped'] += 1
                        continue

                    console.print(f"[cyan]Syncing {func_name}[/cyan] ({local_slug}) - {match_pct:.1f}%")

                    # Search for existing scratches on production with same function name
                    skip_creation = False
                    try:
                        search_resp = await prod_client.get(
                            '/api/scratch',
                            params={'search': func_name, 'platform': 'gc_wii', 'page_size': 5}
                        )
                        if search_resp.status_code == 200:
                            search_data = search_resp.json()
                            existing = search_data.get('results', [])
                            # Look for exact name match with 100% score (score=0 means perfect)
                            exact_match = None
                            for existing_scratch in existing:
                                existing_name = existing_scratch.get('name', '')
                                existing_score = existing_scratch.get('score', -1)
                                if existing_name == func_name and existing_score == 0:
                                    exact_match = existing_scratch
                                    break

                            if exact_match and not force:
                                existing_slug = exact_match.get('slug', '')
                                console.print(f"[yellow]  Found existing 100% match on production: {existing_slug}[/yellow]")
                                console.print(f"[dim]  Linking instead of creating (use --force to create anyway)[/dim]")
                                # Record the mapping
                                current_slug_map = load_slug_map()
                                current_slug_map[existing_slug] = {
                                    'local_slug': local_slug,
                                    'function': func_name,
                                    'match_percent': 100.0,
                                    'synced_at': time.time(),
                                    'note': 'linked to existing production scratch',
                                }
                                save_slug_map(current_slug_map)
                                # Update completed_functions.json
                                current_completed = load_completed_functions()
                                if func_name in current_completed:
                                    current_completed[func_name]['production_slug'] = existing_slug
                                    save_completed_functions(current_completed)

                                # Also write to state database (non-blocking)
                                db_record_sync(local_slug, existing_slug, func_name)
                                db_upsert_scratch(existing_slug, 'production', PRODUCTION_DECOMP_ME, function_name=func_name, match_percent=100.0)
                                db_upsert_function(func_name, production_scratch_slug=existing_slug)

                                results['success'] += 1
                                results['details'].append({
                                    'function': func_name,
                                    'local_slug': local_slug,
                                    'production_slug': existing_slug,
                                    'action': 'linked_existing',
                                })
                                skip_creation = True
                            elif existing:
                                # No exact 100% match, but found related scratches
                                best = existing[0]
                                console.print(f"[dim]  Found {len(existing)} existing scratch(es), best: {best.get('name')} (score={best.get('score', '?')})[/dim]")
                    except Exception as e:
                        console.print(f"[dim]  Warning: Could not search production: {e}[/dim]")

                    if skip_creation:
                        continue

                    if dry_run:
                        results['success'] += 1
                        continue

                    try:
                        local_scratch = await local_client.get_scratch(local_slug)

                        create_data = {
                            'name': local_scratch.name,
                            'compiler': local_scratch.compiler,
                            'platform': local_scratch.platform,
                            'compiler_flags': local_scratch.compiler_flags,
                            'diff_flags': local_scratch.diff_flags,
                            'source_code': local_scratch.source_code,
                            'context': local_scratch.context,
                            'diff_label': local_scratch.diff_label,
                            'target_asm': '',
                        }

                        try:
                            import zipfile
                            import io
                            export_data = await local_client.export_scratch(local_slug, target_only=True)
                            with zipfile.ZipFile(io.BytesIO(export_data)) as zf:
                                for name in zf.namelist():
                                    if 'target' in name.lower() and name.endswith('.s'):
                                        create_data['target_asm'] = zf.read(name).decode('utf-8')
                                        break
                        except Exception as e:
                            console.print(f"[yellow]  Warning: Could not export target ASM: {e}[/yellow]")

                        if not create_data['target_asm']:
                            console.print(f"[yellow]  Warning: No target ASM, scratch may not work correctly[/yellow]")

                        resp = await prod_client.post('/api/scratch', json=create_data)

                        if resp.status_code == 201 or resp.status_code == 200:
                            prod_data = resp.json()
                            prod_slug = prod_data.get('slug', 'unknown')
                            console.print(f"[green]  Created: {PRODUCTION_DECOMP_ME}/scratch/{prod_slug}[/green]")

                            # Update slug map
                            current_slug_map = load_slug_map()
                            current_slug_map[prod_slug] = {
                                'local_slug': local_slug,
                                'function': func_name,
                                'match_percent': match_pct,
                                'synced_at': time.time(),
                            }
                            save_slug_map(current_slug_map)

                            # Update completed_functions.json with production slug
                            current_completed = load_completed_functions()
                            if func_name in current_completed:
                                current_completed[func_name]['production_slug'] = prod_slug
                                save_completed_functions(current_completed)

                            # Also write to state database (non-blocking)
                            db_record_sync(local_slug, prod_slug, func_name)
                            db_upsert_scratch(prod_slug, 'production', PRODUCTION_DECOMP_ME, function_name=func_name, match_percent=match_pct)
                            db_upsert_function(func_name, production_scratch_slug=prod_slug)

                            synced[local_slug] = {
                                'production_slug': prod_slug,
                                'function': func_name,
                                'match_percent': match_pct,
                                'timestamp': time.time(),
                            }
                            results['success'] += 1
                            results['details'].append({
                                'function': func_name,
                                'local_slug': local_slug,
                                'production_slug': prod_slug,
                            })
                        elif resp.status_code == 403:
                            console.print(f"[red]  Failed: Cloudflare blocked (cf_clearance expired?)[/red]")
                            results['failed'] += 1
                            break
                        else:
                            error_text = resp.text[:200]
                            console.print(f"[red]  Failed: {resp.status_code} - {error_text}[/red]")
                            results['failed'] += 1

                    except Exception as e:
                        console.print(f"[red]  Error: {e}[/red]")
                        results['failed'] += 1

        return results

    results = asyncio.run(do_sync())

    if not dry_run:
        with open(synced_file, 'w') as f:
            json.dump(synced, f, indent=2)

    console.print(f"\n[bold]Sync Complete[/bold]")
    console.print(f"  Success: {results['success']}")
    console.print(f"  Skipped: {results['skipped']}")
    console.print(f"  Failed: {results['failed']}")

    if results['details']:
        console.print("\n[bold]Synced scratches:[/bold]")
        for detail in results['details']:
            console.print(f"  {detail['function']}: {PRODUCTION_DECOMP_ME}/scratch/{detail['production_slug']}")


@sync_app.command("slugs")
def sync_slugs(
    output_json: Annotated[
        bool, typer.Option("--json", help="Output as JSON")
    ] = False,
):
    """Show the local->production slug mapping."""
    slug_map = load_slug_map()

    if not slug_map:
        console.print("[yellow]No slug mappings found[/yellow]")
        console.print("[dim]Run 'sync production' to sync scratches and create mappings[/dim]")
        return

    if output_json:
        print(json.dumps(slug_map, indent=2))
    else:
        table = Table(title="Local -> Production Slug Mapping")
        table.add_column("Production Slug", style="cyan")
        table.add_column("Local Slug", style="dim")
        table.add_column("Function")
        table.add_column("Match %", justify="right")

        for prod_slug, info in sorted(slug_map.items(), key=lambda x: x[1].get('function', '')):
            table.add_row(
                prod_slug,
                info.get('local_slug', '?'),
                info.get('function', '?'),
                f"{info.get('match_percent', 0):.1f}%",
            )

        console.print(table)
        console.print(f"\n[dim]{len(slug_map)} mappings stored in {SLUG_MAP_FILE}[/dim]")


@sync_app.command("clear")
def sync_clear():
    """Clear cached cookies and sync history."""
    if PRODUCTION_COOKIES_FILE.exists():
        PRODUCTION_COOKIES_FILE.unlink()
        console.print(f"[green]Removed {PRODUCTION_COOKIES_FILE}[/green]")

    synced_file = PRODUCTION_COOKIES_FILE.parent / "synced_scratches.json"
    if synced_file.exists():
        synced_file.unlink()
        console.print(f"[green]Removed {synced_file}[/green]")

    if SLUG_MAP_FILE.exists():
        SLUG_MAP_FILE.unlink()
        console.print(f"[green]Removed {SLUG_MAP_FILE}[/green]")


@sync_app.command("validate")
def sync_validate(
    local_url: Annotated[
        str, typer.Option("--local-url", "-u", help="Local decomp.me instance URL")
    ] = DEFAULT_DECOMP_ME_URL,
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
    _require_api_url(local_url)

    completed = load_completed_functions()
    slug_map = load_slug_map()
    synced_local_slugs = {v.get('local_slug') for v in slug_map.values()}

    # Find candidates to validate
    candidates = []
    for func_name, info in completed.items():
        match_pct = info.get('match_percent', 0)
        local_slug = info.get('scratch_slug', '')

        if match_pct < min_match:
            continue
        if not local_slug:
            continue
        if local_slug in synced_local_slugs:
            continue  # Already synced, skip

        candidates.append({
            'function': func_name,
            'slug': local_slug,
            'recorded_match': match_pct,
        })

    candidates.sort(key=lambda x: -x['recorded_match'])
    candidates = candidates[:limit]

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
            for candidate in candidates:
                func_name = candidate['function']
                slug = candidate['slug']
                recorded_match = candidate['recorded_match']

                issues = []

                try:
                    scratch = await client.get_scratch(slug)

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
                    else:
                        results['valid'].append(entry)

                except Exception as e:
                    results['errors'].append({
                        'function': func_name,
                        'slug': slug,
                        'error': str(e),
                    })

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


@sync_app.command("dedup")
def sync_dedup(
    local_url: Annotated[
        str, typer.Option("--local-url", "-u", help="Local decomp.me instance URL")
    ] = DEFAULT_DECOMP_ME_URL,
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

    Updates completed_functions.json to use the winning scratch.
    """
    _require_api_url(local_url)

    completed = load_completed_functions()

    # Find functions with multiple candidate scratches
    # This requires checking the database for multiple scratches per function
    from src.db import get_db
    db = get_db()

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
            for dup in duplicates:
                func_name = dup['function_name']
                slugs = dup['slugs'].split(',')

                # Fetch all scratches for this function
                scratch_info = []
                for slug in slugs:
                    try:
                        scratch = await client.get_scratch(slug)
                        match_pct = (scratch.max_score - scratch.score) / scratch.max_score * 100 if scratch.max_score > 0 else 0
                        scratch_info.append({
                            'slug': slug,
                            'name': scratch.name,
                            'match_pct': match_pct,
                            'name_matches': scratch.name == func_name,
                            'code_has_func': func_name in scratch.source_code,
                        })
                    except Exception as e:
                        scratch_info.append({
                            'slug': slug,
                            'error': str(e),
                        })

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

        # Update completed_functions if needed
        if func_name in completed:
            current_slug = completed[func_name].get('scratch_slug')
            if current_slug != winner['slug']:
                console.print(f"  [yellow]→ Updating from {current_slug} to {winner['slug']}[/yellow]")
                if not dry_run:
                    completed[func_name]['scratch_slug'] = winner['slug']
                    completed[func_name]['match_percent'] = winner['match_pct']
                changes_made += 1
        console.print()

    if changes_made > 0 and not dry_run:
        save_completed_functions(completed)
        console.print(f"[green]Updated {changes_made} function(s) in completed_functions.json[/green]")
    elif dry_run and changes_made > 0:
        console.print(f"[yellow]Would update {changes_made} function(s) (dry run)[/yellow]")
