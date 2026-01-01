"""Production sync command."""

import asyncio
import json
import time
from pathlib import Path
from typing import Annotated, Optional

import typer

from .._common import (
    console,
    DEFAULT_MELEE_ROOT,
    PRODUCTION_COOKIES_FILE,
    PRODUCTION_DECOMP_ME,
    get_local_api_url,
    load_slug_map,
    save_slug_map,
    db_record_sync,
    db_upsert_function,
    db_upsert_scratch,
)
from ._helpers import load_production_cookies, rate_limited_request


def production_command(
    melee_root: Annotated[
        Path, typer.Option("--melee-root", "-m", help="Path to melee submodule")
    ] = DEFAULT_MELEE_ROOT,
    local_url: Annotated[
        Optional[str], typer.Option("--local-url", help="Local decomp.me instance URL (auto-detected if not specified)")
    ] = None,
    min_match: Annotated[
        float, typer.Option("--min-match", help="Minimum match percentage to sync")
    ] = 0.0,
    limit: Annotated[
        int, typer.Option("--limit", "-n", help="Maximum scratches to sync")
    ] = 10,
    dry_run: Annotated[
        bool, typer.Option("--dry-run", help="Show what would be synced without syncing")
    ] = False,
    force: Annotated[
        bool, typer.Option("--force", "-f", help="Re-sync even if already exists on production")
    ] = False,
    function: Annotated[
        Optional[str], typer.Option("--function", help="Only sync this specific function")
    ] = None,
):
    """Sync completed functions from local instance to production decomp.me.

    Queries the SQLite database to find functions with local slugs
    that haven't been synced to production yet.
    """
    # Auto-detect local URL if not provided
    if local_url is None:
        local_url = get_local_api_url()
        console.print(f"[dim]Auto-detected local server: {local_url}[/dim]")

    prod_cookies = load_production_cookies()
    if not prod_cookies.get('cf_clearance'):
        console.print("[red]No cf_clearance cookie configured[/red]")
        console.print("[dim]Run 'melee-agent sync auth' first[/dim]")
        raise typer.Exit(1)

    # Query functions from database
    from src.db import get_db

    db = get_db()
    to_sync = []
    already_synced_list = []

    with db.connection() as conn:
        # Build query based on options
        # Always exclude 0% (no progress) unless explicitly looking at a specific function
        effective_min = max(min_match, 0.01) if not function else min_match
        query = """
            SELECT function_name, match_percent, local_scratch_slug, production_scratch_slug
            FROM functions
            WHERE match_percent >= ?
            AND local_scratch_slug IS NOT NULL
        """
        params = [effective_min]

        if function:
            query += " AND function_name = ?"
            params.append(function)

        query += " ORDER BY match_percent DESC"
        cursor = conn.execute(query, params)

        for row in cursor.fetchall():
            entry = {
                'name': row['function_name'],
                'slug': row['local_scratch_slug'],
                'match_pct': row['match_percent'] or 0,
            }
            if row['production_scratch_slug']:
                already_synced_list.append(entry)
            else:
                to_sync.append(entry)

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

    console.print(f"[bold]Syncing {len(to_sync)} functions to production...[/bold]")
    console.print(f"[dim]  Local: {local_url}[/dim]")
    console.print(f"[dim]  Production: {PRODUCTION_DECOMP_ME}[/dim]\n")

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
                        console.print(f"[dim]  Searching production for existing scratch...[/dim]")
                        search_resp = await rate_limited_request(
                            prod_client, 'get', '/api/scratch',
                            params={'search': func_name, 'platform': 'gc_wii', 'page_size': 5}
                        )
                        console.print(f"[dim]  Search complete (status {search_resp.status_code})[/dim]")
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

                                # Update state database
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
                        console.print(f"[dim]  Fetching local scratch data...[/dim]")
                        local_scratch = await local_client.get_scratch(local_slug)
                        console.print(f"[dim]  Local scratch fetched[/dim]")

                        source_code = local_scratch.source_code

                        # Check if source code is placeholder/empty
                        is_placeholder = (
                            not source_code or
                            len(source_code.strip()) < 50 or
                            'TODO' in source_code or
                            source_code.strip().startswith('//')
                        )

                        # Only refresh context for placeholder code
                        # For real matches, keep the original context that produced the match
                        needs_fresh_context = is_placeholder

                        context = local_scratch.context
                        if not needs_fresh_context:
                            console.print(f"[dim]  Using local scratch context ({len(context):,} bytes)[/dim]")
                        if needs_fresh_context:
                            console.print(f"[yellow]  Local scratch has placeholder code, refreshing from repo...[/yellow]")
                            try:
                                from src.commit.configure import get_file_path_from_function
                                from src.commit.update import _extract_function_from_code

                                file_path = await get_file_path_from_function(func_name, melee_root)
                                if file_path:
                                    full_path = melee_root / "src" / file_path
                                    if full_path.exists():
                                        # Extract source from repo for placeholder scratches
                                        repo_content = full_path.read_text(encoding='utf-8')
                                        extracted_code = _extract_function_from_code(repo_content, func_name)
                                        if extracted_code:
                                            source_code = extracted_code
                                            console.print(f"[green]  Extracted {len(source_code)} bytes from repo[/green]")
                                        else:
                                            console.print(f"[yellow]  Could not extract function from repo[/yellow]")

                                        # Get fresh context and strip the function
                                        ctx_path = melee_root / "build" / "GALE01" / "src" / file_path.replace('.c', '.ctx')
                                        if ctx_path.exists():
                                            context = ctx_path.read_text(encoding='utf-8')
                                            original_len = len(context)

                                            # Strip function definition (but keep declaration) to avoid redefinition
                                            if func_name in context:
                                                lines = context.split('\n')
                                                filtered = []
                                                in_func = False
                                                depth = 0
                                                for line in lines:
                                                    if not in_func and func_name in line and '(' in line:
                                                        s = line.strip()
                                                        # Skip comments, control flow, and declarations (end with ;)
                                                        if s.startswith('//') or s.startswith('if') or s.startswith('while'):
                                                            filtered.append(line)
                                                            continue
                                                        # Keep declarations (prototypes) - they end with );
                                                        if s.endswith(';'):
                                                            filtered.append(line)
                                                            continue
                                                        # This is a function definition
                                                        in_func = True
                                                        depth = line.count('{') - line.count('}')
                                                        filtered.append(f'// {func_name} definition stripped')
                                                        # If no brace on this line, wait for it
                                                        if '{' not in line:
                                                            depth = 0
                                                        elif depth <= 0:
                                                            in_func = False
                                                        continue
                                                    if in_func:
                                                        depth += line.count('{') - line.count('}')
                                                        if depth <= 0:
                                                            in_func = False
                                                        continue
                                                    filtered.append(line)
                                                context = '\n'.join(filtered)

                                            console.print(f"[green]  Loaded fresh context ({len(context):,} bytes, stripped {original_len - len(context):,})[/green]")
                                        else:
                                            console.print(f"[yellow]  Context file not found: {ctx_path}[/yellow]")
                                            console.print(f"[dim]  Run 'ninja {ctx_path.relative_to(melee_root)}' to generate[/dim]")
                                    else:
                                        console.print(f"[yellow]  Source file not found: {full_path}[/yellow]")
                                else:
                                    console.print(f"[yellow]  Could not locate source file for {func_name}[/yellow]")
                            except Exception as e:
                                console.print(f"[yellow]  Could not fetch from repo: {e}[/yellow]")

                        create_data = {
                            'name': local_scratch.name,
                            'compiler': local_scratch.compiler,
                            'platform': local_scratch.platform,
                            'compiler_flags': local_scratch.compiler_flags,
                            'diff_flags': local_scratch.diff_flags,
                            'source_code': source_code,
                            'context': context,
                            'diff_label': local_scratch.diff_label,
                            'target_asm': '',
                        }

                        try:
                            import zipfile
                            import io
                            console.print(f"[dim]  Exporting target ASM...[/dim]")
                            export_data = await local_client.export_scratch(local_slug, target_only=True)
                            console.print(f"[dim]  Export complete[/dim]")
                            with zipfile.ZipFile(io.BytesIO(export_data)) as zf:
                                for name in zf.namelist():
                                    if 'target' in name.lower() and name.endswith('.s'):
                                        create_data['target_asm'] = zf.read(name).decode('utf-8')
                                        break
                        except Exception as e:
                            console.print(f"[yellow]  Warning: Could not export target ASM: {e}[/yellow]")

                        if not create_data['target_asm']:
                            console.print(f"[yellow]  Warning: No target ASM, scratch may not work correctly[/yellow]")

                        console.print(f"[dim]  Creating scratch on production...[/dim]")
                        resp = await rate_limited_request(
                            prod_client, 'post', '/api/scratch', json=create_data
                        )
                        console.print(f"[dim]  Create complete (status {resp.status_code})[/dim]")

                        if resp.status_code == 201 or resp.status_code == 200:
                            prod_data = resp.json()
                            prod_slug = prod_data.get('slug', 'unknown')
                            claim_token = prod_data.get('claim_token')
                            console.print(f"[green]  Created: {PRODUCTION_DECOMP_ME}/scratch/{prod_slug}[/green]")

                            # Claim ownership of the scratch
                            if claim_token:
                                console.print(f"[dim]  Claiming ownership...[/dim]")
                                try:
                                    claim_resp = await rate_limited_request(
                                        prod_client, 'post', f'/api/scratch/{prod_slug}/claim',
                                        json={'token': claim_token}
                                    )
                                    if claim_resp.status_code == 200:
                                        claim_result = claim_resp.json()
                                        if claim_result.get('success'):
                                            console.print(f"[green]  Ownership claimed[/green]")
                                        else:
                                            console.print(f"[yellow]  Claim returned success=false[/yellow]")
                                    else:
                                        console.print(f"[yellow]  Claim failed: {claim_resp.status_code}[/yellow]")
                                except Exception as claim_err:
                                    console.print(f"[yellow]  Claim error: {claim_err}[/yellow]")
                            else:
                                console.print(f"[yellow]  No claim_token returned, scratch will be anonymous[/yellow]")

                            # Update slug map
                            current_slug_map = load_slug_map()
                            current_slug_map[prod_slug] = {
                                'local_slug': local_slug,
                                'function': func_name,
                                'match_percent': match_pct,
                                'synced_at': time.time(),
                            }
                            save_slug_map(current_slug_map)

                            # Update state database
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
