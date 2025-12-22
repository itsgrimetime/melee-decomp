"""
CLI interface for the Melee Decomp Agent tooling.

Commands:
- extract: List and extract unmatched functions
- scratch: Manage decomp.me scratches
- match: Run the matching agent loop
- commit: Commit matched functions and create PRs
"""

import asyncio
from pathlib import Path
from typing import Annotated, Optional

import typer
from rich.console import Console
from rich.table import Table

app = typer.Typer(
    name="melee-agent",
    help="Agent tooling for contributing to the Melee decompilation project",
)
console = Console()

# Default paths
DEFAULT_MELEE_ROOT = Path(__file__).parent.parent / "melee"
DEFAULT_DECOMP_ME_URL = "http://localhost:8000"


# ============================================================================
# Extract Commands
# ============================================================================

extract_app = typer.Typer(help="Extract and list unmatched functions")
app.add_typer(extract_app, name="extract")


@extract_app.command("list")
def extract_list(
    melee_root: Annotated[
        Path, typer.Option("--melee-root", "-m", help="Path to melee submodule")
    ] = DEFAULT_MELEE_ROOT,
    min_match: Annotated[
        float, typer.Option("--min-match", help="Minimum match percentage")
    ] = 0.0,
    max_match: Annotated[
        float, typer.Option("--max-match", help="Maximum match percentage")
    ] = 0.99,
    min_size: Annotated[
        int, typer.Option("--min-size", help="Minimum function size in bytes")
    ] = 0,
    max_size: Annotated[
        int, typer.Option("--max-size", help="Maximum function size in bytes")
    ] = 10000,
    limit: Annotated[
        int, typer.Option("--limit", "-n", help="Maximum number of results")
    ] = 20,
):
    """List unmatched functions from the melee project."""
    from src.extractor import extract_unmatched_functions

    result = asyncio.run(extract_unmatched_functions(melee_root))

    # Filter and limit functions
    functions = [
        f for f in result.functions
        if min_match <= f.current_match <= max_match
        and min_size <= f.size_bytes <= max_size
    ]
    functions = sorted(functions, key=lambda f: -f.current_match)[:limit]

    table = Table(title="Unmatched Functions")
    table.add_column("Name", style="cyan")
    table.add_column("File", style="green")
    table.add_column("Match %", justify="right")
    table.add_column("Size", justify="right")
    table.add_column("Address", style="dim")

    for func in functions:
        table.add_row(
            func.name,
            func.file_path,
            f"{func.current_match * 100:.1f}%",
            f"{func.size_bytes}",
            func.address,
        )

    console.print(table)
    console.print(f"\n[dim]Found {len(functions)} functions (from {result.total_functions} total)[/dim]")


@extract_app.command("get")
def extract_get(
    function_name: Annotated[str, typer.Argument(help="Name of the function to extract")],
    melee_root: Annotated[
        Path, typer.Option("--melee-root", "-m", help="Path to melee submodule")
    ] = DEFAULT_MELEE_ROOT,
    output: Annotated[
        Optional[Path], typer.Option("--output", "-o", help="Output file for ASM")
    ] = None,
):
    """Extract a specific function's ASM and context."""
    from src.extractor import extract_function

    func = asyncio.run(extract_function(melee_root, function_name))

    if func is None:
        console.print(f"[red]Function '{function_name}' not found[/red]")
        raise typer.Exit(1)

    console.print(f"[bold cyan]{func.name}[/bold cyan]")
    console.print(f"File: {func.file_path}")
    console.print(f"Address: {func.address}")
    console.print(f"Size: {func.size_bytes} bytes")
    console.print(f"Match: {func.current_match * 100:.1f}%")
    console.print("\n[bold]Assembly:[/bold]")
    if func.asm:
        console.print(func.asm[:2000] + ("..." if len(func.asm) > 2000 else ""))
    else:
        console.print("[yellow]ASM not available (project needs to be built first)[/yellow]")

    if output:
        if func.asm:
            output.write_text(func.asm)
            console.print(f"\n[green]ASM written to {output}[/green]")
        else:
            console.print("[red]Cannot write output - ASM not available[/red]")


# ============================================================================
# Scratch Commands
# ============================================================================

scratch_app = typer.Typer(help="Manage decomp.me scratches")
app.add_typer(scratch_app, name="scratch")


@scratch_app.command("create")
def scratch_create(
    function_name: Annotated[str, typer.Argument(help="Name of the function")],
    melee_root: Annotated[
        Path, typer.Option("--melee-root", "-m", help="Path to melee submodule")
    ] = DEFAULT_MELEE_ROOT,
    api_url: Annotated[
        str, typer.Option("--api-url", help="Decomp.me API URL")
    ] = DEFAULT_DECOMP_ME_URL,
):
    """Create a new scratch for a function on decomp.me."""
    from src.client import DecompMeAPIClient
    from src.extractor import extract_function

    async def create():
        func = await extract_function(melee_root, function_name)
        if func is None:
            console.print(f"[red]Function '{function_name}' not found[/red]")
            raise typer.Exit(1)

        async with DecompMeAPIClient(base_url=api_url) as client:
            from src.client import ScratchCreate
            scratch = await client.create_scratch(
                ScratchCreate(
                    name=func.name,
                    target_asm=func.asm,
                    context=func.context,
                    source_code="// TODO: Decompile this function\n",
                    diff_label=func.name,
                )
            )
        return scratch

    scratch = asyncio.run(create())
    console.print(f"[green]Created scratch:[/green] {api_url}/scratch/{scratch.slug}")


@scratch_app.command("compile")
def scratch_compile(
    slug: Annotated[str, typer.Argument(help="Scratch slug/ID")],
    api_url: Annotated[
        str, typer.Option("--api-url", help="Decomp.me API URL")
    ] = DEFAULT_DECOMP_ME_URL,
):
    """Compile a scratch and show the diff."""
    from src.client import DecompMeAPIClient

    async def compile_scratch():
        async with DecompMeAPIClient(base_url=api_url) as client:
            result = await client.compile_scratch(slug)
            return result

    result = asyncio.run(compile_scratch())

    if result.success:
        match_pct = (
            100.0 if result.diff_output.current_score == 0
            else (1.0 - result.diff_output.current_score / result.diff_output.max_score) * 100
        )
        console.print(f"[green]Compiled successfully![/green]")
        console.print(f"Match: {match_pct:.1f}%")
        console.print(f"Score: {result.diff_output.current_score}/{result.diff_output.max_score}")
    else:
        console.print(f"[red]Compilation failed[/red]")
        console.print(result.compiler_output)


@scratch_app.command("update")
def scratch_update(
    slug: Annotated[str, typer.Argument(help="Scratch slug/ID")],
    source_file: Annotated[Path, typer.Argument(help="Path to C source file")],
    api_url: Annotated[
        str, typer.Option("--api-url", help="Decomp.me API URL")
    ] = DEFAULT_DECOMP_ME_URL,
):
    """Update a scratch's source code from a file."""
    from src.client import DecompMeAPIClient, ScratchUpdate

    source_code = source_file.read_text()

    async def update():
        async with DecompMeAPIClient(base_url=api_url) as client:
            scratch = await client.update_scratch(slug, ScratchUpdate(source_code=source_code))
            result = await client.compile_scratch(slug)
            return scratch, result

    scratch, result = asyncio.run(update())

    if result.success and result.diff_output:
        match_pct = (
            100.0 if result.diff_output.current_score == 0
            else (1.0 - result.diff_output.current_score / result.diff_output.max_score) * 100
        )
        console.print(f"[green]Updated and compiled![/green] Match: {match_pct:.1f}%")
    else:
        console.print(f"[yellow]Updated but compilation failed[/yellow]")


# ============================================================================
# Match Commands
# ============================================================================

match_app = typer.Typer(help="Run the decompilation matching agent")
app.add_typer(match_app, name="match")


@match_app.command("run")
def match_run(
    function_name: Annotated[
        Optional[str], typer.Argument(help="Specific function to match")
    ] = None,
    melee_root: Annotated[
        Path, typer.Option("--melee-root", "-m", help="Path to melee submodule")
    ] = DEFAULT_MELEE_ROOT,
    api_url: Annotated[
        str, typer.Option("--api-url", help="Decomp.me API URL")
    ] = DEFAULT_DECOMP_ME_URL,
    max_iterations: Annotated[
        int, typer.Option("--max-iter", help="Maximum iterations per function")
    ] = 50,
    auto_commit: Annotated[
        bool, typer.Option("--auto-commit", help="Automatically commit matches")
    ] = False,
):
    """Run the agent loop to match a function."""
    from src.agent import run_matching_agent

    result = asyncio.run(
        run_matching_agent(
            function_name=function_name,
            melee_root=melee_root,
            api_url=api_url,
            max_iterations=max_iterations,
            auto_commit=auto_commit,
        )
    )

    if result.matched:
        console.print(f"[bold green]Matched {result.function_name}![/bold green]")
        console.print(f"Scratch: {api_url}/scratch/{result.scratch_slug}")
        if result.pr_url:
            console.print(f"PR: {result.pr_url}")
    else:
        console.print(f"[yellow]Could not achieve 100% match[/yellow]")
        console.print(f"Best: {result.best_match * 100:.1f}%")


# ============================================================================
# Commit Commands
# ============================================================================

commit_app = typer.Typer(help="Commit matched functions and create PRs")
app.add_typer(commit_app, name="commit")


@commit_app.command("apply")
def commit_apply(
    function_name: Annotated[str, typer.Argument(help="Name of the matched function")],
    scratch_slug: Annotated[str, typer.Argument(help="Decomp.me scratch slug")],
    melee_root: Annotated[
        Path, typer.Option("--melee-root", "-m", help="Path to melee submodule")
    ] = DEFAULT_MELEE_ROOT,
    api_url: Annotated[
        str, typer.Option("--api-url", help="Decomp.me API URL")
    ] = DEFAULT_DECOMP_ME_URL,
    create_pr: Annotated[
        bool, typer.Option("--pr", help="Create a PR after committing")
    ] = False,
):
    """Apply a matched function to the melee project."""
    from src.client import DecompMeAPIClient
    from src.commit import CommitWorkflow

    async def apply():
        async with DecompMeAPIClient(base_url=api_url) as client:
            scratch = await client.get_scratch(scratch_slug)

            # Verify it's a match
            if scratch.score != 0:
                console.print("[red]Scratch is not a 100% match[/red]")
                raise typer.Exit(1)

            workflow = CommitWorkflow(melee_root)
            result = await workflow.apply_matched_function(
                function_name=function_name,
                source_code=scratch.source_code,
                scratch_id=scratch_slug,
                create_pr=create_pr,
            )
            return result

    result = asyncio.run(apply())

    console.print(f"[green]Applied {function_name}[/green]")
    for f in result.files_changed:
        console.print(f"  Modified: {f}")

    if result.pr_url:
        console.print(f"\n[bold]PR created:[/bold] {result.pr_url}")


@commit_app.command("format")
def commit_format(
    melee_root: Annotated[
        Path, typer.Option("--melee-root", "-m", help="Path to melee submodule")
    ] = DEFAULT_MELEE_ROOT,
):
    """Run clang-format on staged changes."""
    from src.commit import format_files

    success = asyncio.run(format_files(melee_root))

    if success:
        console.print("[green]Formatting applied[/green]")
    else:
        console.print("[red]Formatting failed[/red]")
        raise typer.Exit(1)


# ============================================================================
# Docker Commands
# ============================================================================

docker_app = typer.Typer(help="Manage local decomp.me instance")
app.add_typer(docker_app, name="docker")


@docker_app.command("up")
def docker_up(
    port: Annotated[int, typer.Option("--port", "-p", help="API port")] = 8000,
    detach: Annotated[bool, typer.Option("--detach", "-d", help="Run in background")] = True,
):
    """Start local decomp.me instance."""
    import subprocess

    docker_dir = Path(__file__).parent.parent / "docker"
    env = {"DECOMP_ME_PORT": str(port)}

    cmd = ["docker", "compose", "-f", str(docker_dir / "docker-compose.yml"), "up"]
    if detach:
        cmd.append("-d")

    console.print(f"[cyan]Starting decomp.me on port {port}...[/cyan]")
    result = subprocess.run(cmd, env={**subprocess.os.environ, **env})

    if result.returncode == 0:
        console.print(f"[green]decomp.me running at http://localhost:{port}[/green]")
    else:
        console.print("[red]Failed to start decomp.me[/red]")
        raise typer.Exit(1)


@docker_app.command("down")
def docker_down():
    """Stop local decomp.me instance."""
    import subprocess

    docker_dir = Path(__file__).parent.parent / "docker"

    cmd = ["docker", "compose", "-f", str(docker_dir / "docker-compose.yml"), "down"]
    subprocess.run(cmd)
    console.print("[green]decomp.me stopped[/green]")


@docker_app.command("status")
def docker_status():
    """Check status of local decomp.me instance."""
    import subprocess

    docker_dir = Path(__file__).parent.parent / "docker"

    cmd = ["docker", "compose", "-f", str(docker_dir / "docker-compose.yml"), "ps"]
    subprocess.run(cmd)


if __name__ == "__main__":
    app()
