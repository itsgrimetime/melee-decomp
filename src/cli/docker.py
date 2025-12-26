"""Docker commands - manage local decomp.me instance."""

import subprocess
from pathlib import Path
from typing import Annotated

import typer

from ._common import console

docker_app = typer.Typer(help="Manage local decomp.me instance")


@docker_app.command("up")
def docker_up(
    port: Annotated[int, typer.Option("--port", "-p", help="API port")] = 8000,
    detach: Annotated[bool, typer.Option("--detach", "-d", help="Run in background")] = True,
):
    """Start local decomp.me instance."""
    docker_dir = Path(__file__).parent.parent.parent / "docker"
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
    docker_dir = Path(__file__).parent.parent.parent / "docker"

    cmd = ["docker", "compose", "-f", str(docker_dir / "docker-compose.yml"), "down"]
    subprocess.run(cmd)
    console.print("[green]decomp.me stopped[/green]")


@docker_app.command("status")
def docker_status():
    """Check status of local decomp.me instance."""
    docker_dir = Path(__file__).parent.parent.parent / "docker"

    cmd = ["docker", "compose", "-f", str(docker_dir / "docker-compose.yml"), "ps"]
    subprocess.run(cmd)
