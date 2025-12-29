"""Compilers command - list available compilers."""

import asyncio
import json
import os
from typing import Annotated, Optional

import typer
from rich.table import Table

from ._common import console, get_local_api_url


def list_compilers(
    platform: Annotated[
        Optional[str], typer.Argument(help="Filter by platform (e.g., gc_wii)")
    ] = None,
    api_url: Annotated[
        Optional[str], typer.Option("--api-url", help="Decomp.me API URL (auto-detected)")
    ] = None,
    output_json: Annotated[
        bool, typer.Option("--json", help="Output as JSON")
    ] = False,
):
    """List available compilers."""
    api_url = api_url or get_local_api_url()
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
