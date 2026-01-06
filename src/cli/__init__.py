"""
CLI interface for the Melee Decomp Agent tooling.

This package provides a modular CLI structure with separate modules for each
command group: extract, scratch, claim, complete, commit, docker, sync, pr,
audit, and hook.

Usage:
    python -m src.cli <command>
    melee-agent <command>
"""

from pathlib import Path

from dotenv import load_dotenv

# Load .env from project root (gitignored, contains local config like DECOMP_API_BASE)
load_dotenv(Path(__file__).parent.parent.parent / ".env")

import typer

# Import sub-apps from modules
from .extract import extract_app
from .scratch import scratch_app
from .claim import claim_app
from .complete import complete_app
from .commit import commit_app
from .docker import docker_app
from .sync import sync_app
from .pr import pr_app
from .audit import audit_app
from .hook import hook_app
from .struct import struct_app
from .stub import stub_app
from .worktree import worktree_app
from .workflow import workflow_app
from .state import state_app
from .analytics import analytics_app
from .setup import setup_app
from .compilers import list_compilers

# Import common utilities for backward compatibility
from ._common import (
    console,
    DEFAULT_MELEE_ROOT,
)

# Create main app
app = typer.Typer(
    name="melee-agent",
    help="Agent tooling for contributing to the Melee decompilation project",
)

# Register sub-apps
app.add_typer(extract_app, name="extract")
app.add_typer(scratch_app, name="scratch")
app.add_typer(claim_app, name="claim")
app.add_typer(complete_app, name="complete")
app.add_typer(commit_app, name="commit")
app.add_typer(docker_app, name="docker")
app.add_typer(sync_app, name="sync")
app.add_typer(pr_app, name="pr")
app.add_typer(audit_app, name="audit")
app.add_typer(hook_app, name="hook")
app.add_typer(struct_app, name="struct")
app.add_typer(stub_app, name="stub")
app.add_typer(worktree_app, name="worktree")
app.add_typer(workflow_app, name="workflow")
app.add_typer(state_app, name="state")
app.add_typer(analytics_app, name="analytics")
app.add_typer(setup_app, name="setup")

# Register standalone commands
app.command("compilers")(list_compilers)


def main():
    """Entry point for the CLI."""
    app()


if __name__ == "__main__":
    main()
