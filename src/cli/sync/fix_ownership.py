"""Fix ownership sync command."""

import asyncio
from typing import Annotated, Optional

import typer

from .._common import (
    console,
    PRODUCTION_DECOMP_ME,
    db_record_sync,
    db_upsert_function,
)
from ._helpers import load_production_cookies, rate_limited_request


def fix_ownership_command(
    limit: Annotated[
        int, typer.Option("--limit", "-n", help="Maximum scratches to fix")
    ] = 10,
    dry_run: Annotated[
        bool, typer.Option("--dry-run", help="Show what would be fixed without making changes")
    ] = False,
    function: Annotated[
        Optional[str], typer.Option("--function", help="Only fix this specific function")
    ] = None,
):
    """Fix ownership of synced scratches that have no owner.

    For scratches that were synced before the claim fix, this command:
    1. Finds production scratches without an owner
    2. Forks them (creating a new scratch with a claim token)
    3. Claims ownership of the fork
    4. Updates the database to point to the new slug

    Note: The original unclaimed scratch remains on production but won't be tracked.
    """
    prod_cookies = load_production_cookies()
    if not prod_cookies.get('cf_clearance'):
        console.print("[red]No cf_clearance cookie configured[/red]")
        console.print("[dim]Run 'melee-agent sync auth' first[/dim]")
        raise typer.Exit(1)

    if not prod_cookies.get('sessionid'):
        console.print("[red]No sessionid cookie configured[/red]")
        console.print("[dim]Run 'melee-agent sync auth --session-id <id>' to add it[/dim]")
        raise typer.Exit(1)

    # Query functions from database
    from src.db import get_db

    db = get_db()
    candidates = []

    with db.connection() as conn:
        query = """
            SELECT function_name, production_scratch_slug
            FROM functions
            WHERE production_scratch_slug IS NOT NULL
        """
        params = []

        if function:
            query += " AND function_name = ?"
            params.append(function)

        query += " ORDER BY function_name LIMIT ?"
        params.append(limit)

        cursor = conn.execute(query, params)
        for row in cursor.fetchall():
            candidates.append({
                'function': row['function_name'],
                'prod_slug': row['production_scratch_slug'],
            })

    if not candidates:
        console.print("[yellow]No synced functions found[/yellow]")
        return

    console.print(f"[bold]Checking {len(candidates)} synced scratches for ownership...[/bold]\n")

    import httpx

    async def do_fix():
        results = {'checked': 0, 'already_owned': 0, 'fixed': 0, 'failed': 0, 'details': []}

        prod_cookies_obj = httpx.Cookies()
        prod_cookies_obj.set("cf_clearance", prod_cookies['cf_clearance'], domain="decomp.me")
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
        ) as client:
            for candidate in candidates:
                func_name = candidate['function']
                prod_slug = candidate['prod_slug']

                console.print(f"[dim]Checking {func_name} ({prod_slug})...[/dim]", end="")
                results['checked'] += 1

                try:
                    # Check if scratch has an owner
                    resp = await rate_limited_request(client, 'get', f'/api/scratch/{prod_slug}')
                    if resp.status_code != 200:
                        console.print(f" [red]HTTP {resp.status_code}[/red]")
                        results['failed'] += 1
                        continue

                    scratch_data = resp.json()
                    owner = scratch_data.get('owner')

                    if owner is not None:
                        console.print(f" [green]already owned[/green]")
                        results['already_owned'] += 1
                        continue

                    console.print(f" [yellow]no owner[/yellow]")

                    if dry_run:
                        console.print(f"  [dim]Would fork and claim[/dim]")
                        results['fixed'] += 1
                        continue

                    # Fork the scratch (creates new scratch with claim token)
                    console.print(f"  [dim]Forking...[/dim]", end="")
                    fork_resp = await rate_limited_request(
                        client, 'post', f'/api/scratch/{prod_slug}/fork',
                        json={}
                    )

                    if fork_resp.status_code not in (200, 201):
                        console.print(f" [red]fork failed: {fork_resp.status_code}[/red]")
                        results['failed'] += 1
                        continue

                    fork_data = fork_resp.json()
                    new_slug = fork_data.get('slug')
                    claim_token = fork_data.get('claim_token')

                    if not new_slug or not claim_token:
                        console.print(f" [red]missing slug/token in response[/red]")
                        results['failed'] += 1
                        continue

                    console.print(f" [green]{new_slug}[/green]")

                    # Claim the fork
                    console.print(f"  [dim]Claiming...[/dim]", end="")
                    claim_resp = await rate_limited_request(
                        client, 'post', f'/api/scratch/{new_slug}/claim',
                        json={'token': claim_token}
                    )

                    if claim_resp.status_code != 200:
                        console.print(f" [red]claim failed: {claim_resp.status_code}[/red]")
                        results['failed'] += 1
                        continue

                    claim_result = claim_resp.json()
                    if not claim_result.get('success'):
                        console.print(f" [red]claim returned success=false[/red]")
                        results['failed'] += 1
                        continue

                    console.print(f" [green]OK[/green]")

                    # Update database to point to new slug
                    db_upsert_function(func_name, production_scratch_slug=new_slug)
                    db_record_sync(None, new_slug, func_name)

                    results['fixed'] += 1
                    results['details'].append({
                        'function': func_name,
                        'old_slug': prod_slug,
                        'new_slug': new_slug,
                    })

                except Exception as e:
                    console.print(f" [red]error: {e}[/red]")
                    results['failed'] += 1

        return results

    results = asyncio.run(do_fix())

    console.print(f"\n[bold]Results:[/bold]")
    console.print(f"  Checked: {results['checked']}")
    console.print(f"  Already owned: {results['already_owned']}")
    console.print(f"  Fixed: {results['fixed']}")
    console.print(f"  Failed: {results['failed']}")

    if results['details']:
        console.print(f"\n[bold]Fixed scratches:[/bold]")
        for detail in results['details']:
            console.print(f"  {detail['function']}: {detail['old_slug']} -> {detail['new_slug']}")
