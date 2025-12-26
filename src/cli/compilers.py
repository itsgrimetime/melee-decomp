"""Compilers command - list available compilers."""

import asyncio
import json
import os
from typing import Annotated, Optional

import typer
from rich.table import Table

from ._common import console

# API URL from environment
_api_base = os.environ.get("DECOMP_API_BASE", "")
DEFAULT_DECOMP_ME_URL = _api_base[:-4] if _api_base.endswith("/api") else _api_base


def _require_api_url(api_url: str) -> None:
    """Validate that API URL is configured."""
    if not api_url:
        console.print("[red]Error: DECOMP_API_BASE environment variable is required[/red]")
        console.print("[dim]Set it to your decomp.me instance URL, e.g.:[/dim]")
        console.print("[dim]  export DECOMP_API_BASE=http://10.200.0.1[/dim]")
        raise typer.Exit(1)


def list_compilers(
    platform: Annotated[
        Optional[str], typer.Argument(help="Filter by platform (e.g., gc_wii)")
    ] = None,
    api_url: Annotated[
        str, typer.Option("--api-url", help="Decomp.me API URL")
    ] = DEFAULT_DECOMP_ME_URL,
    output_json: Annotated[
        bool, typer.Option("--json", help="Output as JSON")
    ] = False,
):
    """List available compilers."""
    _require_api_url(api_url)
    from src.client import DecompMeAPIClient

    async def get():
        async with DecompMeAPIClient(base_url=api_url) as client:
            return await client.list_compilers()

    compilers = asyncio.run(get())

    if platform:
        compilers = [c for c in compilers if c.platform == platform]

    if output_json:
        data = [{"id": c.id, "name": c.name, "platform": c.platform, "language": c.language} for c in compilers]
        print(json.dumps(data, indent=2))
    else:
        table = Table(title="Available Compilers")
        table.add_column("ID", style="cyan")
        table.add_column("Name")
        table.add_column("Platform")
        table.add_column("Language")

        for c in compilers:
            table.add_row(c.id, c.name, c.platform, c.language)

        console.print(table)
        console.print(f"[dim]{len(compilers)} compilers available[/dim]")
