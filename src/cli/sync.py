"""Sync commands - sync scratches to production decomp.me."""

import asyncio
import json
import os
import re
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Annotated, Any, Optional

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


def _parse_scratches_txt(scratches_file: Path) -> list[dict[str, Any]]:
    """Parse scratches.txt to extract committed match entries."""
    entries = []
    if not scratches_file.exists():
        return entries

    pattern = re.compile(
        r'^(?P<name>\w+)\s*=\s*(?P<match>[\d.]+%|OK):(?P<status>\w+);\s*//'
        r'(?:\s*author:(?P<author>\S+))?'
        r'(?:\s*id:(?P<slug>\w+))?'
        r'(?:\s*parent:(?P<parent>\w+))?',
        re.MULTILINE
    )

    content = scratches_file.read_text()
    for match in pattern.finditer(content):
        entry = {
            'name': match.group('name'),
            'match_percent': match.group('match'),
            'status': match.group('status'),
            'author': match.group('author') or 'unknown',
            'slug': match.group('slug'),
            'parent': match.group('parent'),
        }
        if entry['slug']:
            entries.append(entry)

    return entries


def _update_scratches_txt_slug(scratches_file: Path, old_slug: str, new_slug: str) -> bool:
    """Replace a scratch slug in scratches.txt."""
    content = scratches_file.read_text()
    new_content = re.sub(
        rf'\bid:{re.escape(old_slug)}\b',
        f'id:{new_slug}',
        content
    )
    new_content = re.sub(
        rf'\bparent:{re.escape(old_slug)}\b',
        f'parent:{new_slug}',
        new_content
    )
    if new_content != content:
        scratches_file.write_text(new_content)
        return True
    return False


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

    scratches_file = melee_root / "config" / "GALE01" / "scratches.txt"

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

                            # Update scratches.txt if the local slug exists there
                            if _update_scratches_txt_slug(scratches_file, local_slug, prod_slug):
                                console.print(f"[dim]  Updated scratches.txt: {local_slug} -> {prod_slug}[/dim]")

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


@sync_app.command("replace-author")
def sync_replace_author(
    from_author: Annotated[
        str, typer.Argument(help="Author name to replace")
    ],
    to_author: Annotated[
        str, typer.Argument(help="New author name")
    ],
    melee_root: Annotated[
        Path, typer.Option("--melee-root", "-m", help="Path to melee submodule")
    ] = DEFAULT_MELEE_ROOT,
    dry_run: Annotated[
        bool, typer.Option("--dry-run", help="Show what would change without modifying")
    ] = False,
):
    """Bulk replace author names in scratches.txt."""
    scratches_file = melee_root / "config" / "GALE01" / "scratches.txt"

    if not scratches_file.exists():
        console.print(f"[red]scratches.txt not found at {scratches_file}[/red]")
        raise typer.Exit(1)

    content = scratches_file.read_text(encoding='utf-8')
    pattern = re.compile(rf'\bauthor:{re.escape(from_author)}\b')
    matches = pattern.findall(content)
    count = len(matches)

    if count == 0:
        console.print(f"[yellow]No entries found with author:{from_author}[/yellow]")
        return

    console.print(f"Found [bold]{count}[/bold] entries with author:{from_author}")

    if dry_run:
        console.print(f"\n[cyan]DRY RUN[/cyan] - Would replace author:{from_author} -> author:{to_author}")
        lines = content.split('\n')
        shown = 0
        for line in lines:
            if pattern.search(line):
                func_match = re.match(r'^(\w+)\s*=', line)
                func_name = func_match.group(1) if func_match else "?"
                console.print(f"  {func_name}")
                shown += 1
                if shown >= 10:
                    remaining = count - shown
                    if remaining > 0:
                        console.print(f"  [dim]... and {remaining} more[/dim]")
                    break
        return

    new_content = pattern.sub(f'author:{to_author}', content)
    now = datetime.now(timezone.utc).isoformat().replace('+00:00', 'Z')

    def update_timestamp(line: str) -> str:
        if f'author:{to_author}' in line:
            if 'updated:' in line:
                line = re.sub(r'updated:\S+', f'updated:{now}', line)
            else:
                line = line.rstrip() + f' updated:{now}'
        return line

    lines = new_content.split('\n')
    updated_lines = [update_timestamp(line) for line in lines]
    new_content = '\n'.join(updated_lines)

    scratches_file.write_text(new_content, encoding='utf-8')
    console.print(f"[green]Updated {count} entries: author:{from_author} -> author:{to_author}[/green]")


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
