"""Setup commands for configuring the decomp environment."""

import shutil
from pathlib import Path
from typing import Annotated

import typer

from ._common import console, BASE_DOL_PATH, DECOMP_CONFIG_DIR, MELEE_WORKTREES_DIR

setup_app = typer.Typer(
    name="setup",
    help="Setup and configure the decomp environment",
)


@setup_app.command("dol")
def setup_dol(
    path: Annotated[
        Path | None,
        typer.Argument(help="Path to main.dol file to register"),
    ] = None,
    auto: Annotated[
        bool,
        typer.Option("--auto", "-a", help="Auto-detect from existing worktrees"),
    ] = False,
):
    """Register the base DOL file for builds.

    The main.dol file is required for building but is gitignored (copyrighted).
    This command copies it to a central location so all worktrees can use it.

    Examples:
        melee-agent setup dol /path/to/main.dol    # Register from a file
        melee-agent setup dol --auto               # Auto-detect from worktrees
        melee-agent setup dol                      # Show current status
    """
    # Show status if no path provided
    if path is None and not auto:
        if BASE_DOL_PATH.exists():
            size = BASE_DOL_PATH.stat().st_size
            console.print(f"[green]DOL is configured:[/green] {BASE_DOL_PATH}")
            console.print(f"[dim]Size: {size:,} bytes[/dim]")
        else:
            console.print(f"[yellow]DOL not configured[/yellow]")
            console.print(f"\nTo configure, run one of:")
            console.print(f"  melee-agent setup dol /path/to/main.dol")
            console.print(f"  melee-agent setup dol --auto")
        return

    # Auto-detect from worktrees
    if auto:
        console.print("[dim]Searching for main.dol in existing worktrees...[/dim]")
        found_path = None

        if MELEE_WORKTREES_DIR.exists():
            for wt in MELEE_WORKTREES_DIR.iterdir():
                dol_path = wt / "orig" / "GALE01" / "sys" / "main.dol"
                if dol_path.exists() and not dol_path.is_symlink():
                    found_path = dol_path
                    console.print(f"[green]Found:[/green] {dol_path}")
                    break

        if not found_path:
            console.print("[red]Could not find main.dol in any worktree[/red]")
            console.print("Please provide the path manually:")
            console.print("  melee-agent setup dol /path/to/main.dol")
            raise typer.Exit(1)

        path = found_path

    # Validate the source file
    if not path.exists():
        console.print(f"[red]File not found:[/red] {path}")
        raise typer.Exit(1)

    if not path.is_file():
        console.print(f"[red]Not a file:[/red] {path}")
        raise typer.Exit(1)

    # Check file size (main.dol should be ~4.4MB)
    size = path.stat().st_size
    if size < 1_000_000 or size > 10_000_000:
        console.print(f"[yellow]Warning: File size ({size:,} bytes) seems unusual for main.dol[/yellow]")
        if not typer.confirm("Continue anyway?"):
            raise typer.Exit(1)

    # Copy to central location
    BASE_DOL_PATH.parent.mkdir(parents=True, exist_ok=True)

    if BASE_DOL_PATH.exists():
        if not typer.confirm(f"DOL already exists at {BASE_DOL_PATH}. Overwrite?"):
            raise typer.Exit(0)

    console.print(f"[dim]Copying to {BASE_DOL_PATH}...[/dim]")
    shutil.copy2(path, BASE_DOL_PATH)

    console.print(f"[green]DOL registered successfully![/green]")
    console.print(f"[dim]Location: {BASE_DOL_PATH}[/dim]")
    console.print(f"\nNew worktrees will automatically use this DOL.")


@setup_app.command("status")
def setup_status():
    """Show current setup status."""
    console.print("[bold]Decomp Setup Status[/bold]\n")

    # DOL status
    if BASE_DOL_PATH.exists():
        size = BASE_DOL_PATH.stat().st_size
        console.print(f"[green]Base DOL:[/green] {BASE_DOL_PATH} ({size:,} bytes)")
    else:
        console.print(f"[yellow]Base DOL:[/yellow] Not configured")
        console.print(f"  Run: melee-agent setup dol --auto")

    # Config dir
    console.print(f"\n[dim]Config directory: {DECOMP_CONFIG_DIR}[/dim]")
