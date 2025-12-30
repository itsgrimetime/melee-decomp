"""Hook commands - Git hook management and commit validation."""

import stat
from pathlib import Path
from typing import Annotated

import typer

from ._common import console, DEFAULT_MELEE_ROOT

hook_app = typer.Typer(help="Git hook management and commit validation")


@hook_app.command("validate")
def hook_validate(
    fix: Annotated[
        bool, typer.Option("--fix", help="Attempt to fix issues automatically")
    ] = False,
    verbose: Annotated[
        bool, typer.Option("--verbose", "-v", help="Show all warnings")
    ] = False,
):
    """Validate staged changes against project guidelines.

    All checks are ERRORS that block commits (based on doldecomp/melee PR feedback):

    Code Style:
    - TRUE/FALSE instead of true/false (lowercase required)
    - Float literals missing F suffix (1.0 should be 1.0F)
    - Lowercase hex literals (0xabc should be 0xABC)
    - clang-format would make changes

    Type/Struct Issues:
    - Raw pointer arithmetic for struct access (use M2C_FIELD)

    Symbol Issues:
    - New extern declarations (include proper headers instead)
    - Descriptive symbol renamed to address-based name
    - New functions need symbols.txt update

    Build Issues:
    - Implicit function declarations (uses clang)
    - Header signatures don't match implementations
    - Merge conflict markers in code

    File Issues:
    - Forbidden files modified (.gitkeep files, orig/ placeholders)

    PR/Commit Issues:
    - Local scratch URLs in commits (must use production decomp.me URLs)
    """
    from src.hooks.validate_commit import CommitValidator

    validator = CommitValidator(melee_root=DEFAULT_MELEE_ROOT)
    errors, warnings = validator.run()

    if warnings and verbose:
        console.print("\n[yellow]Warnings:[/yellow]")
        for w in warnings:
            console.print(f"  ⚠ {w}")

    if errors:
        console.print("\n[red]Errors (must fix before commit):[/red]")
        for e in errors:
            console.print(f"  ✗ {e}")

        if fix:
            console.print("\n[cyan]Attempting fixes...[/cyan]")
            console.print("  Auto-fix not yet implemented")

        console.print(f"\n[red]Validation failed: {len(errors)} error(s)[/red]")
        raise typer.Exit(1)

    if warnings:
        console.print(f"\n[yellow]{len(warnings)} warning(s)[/yellow]")
        if not verbose:
            console.print("  [dim]Run with --verbose to see details[/dim]")

    console.print("\n[green]✓ Validation passed[/green]")


@hook_app.command("install")
def hook_install(
    force: Annotated[
        bool, typer.Option("--force", "-f", help="Overwrite existing hooks")
    ] = False,
):
    """Install git pre-commit hook for validation.

    Creates a pre-commit hook that runs 'melee-agent hook validate' before each commit.
    """
    # Navigate to project root (parent of src/cli)
    project_root = Path(__file__).parent.parent.parent
    hooks_dir = project_root / ".git" / "hooks"
    pre_commit_path = hooks_dir / "pre-commit"

    hook_content = '''#!/bin/sh
# Pre-commit hook for melee-decomp validation
# Installed by: melee-agent hook install

# Run validation
python -m src.hooks.validate_commit --verbose

# Exit with validation result
exit $?
'''

    if pre_commit_path.exists() and not force:
        console.print(f"[yellow]Pre-commit hook already exists at {pre_commit_path}[/yellow]")
        console.print("[dim]Use --force to overwrite[/dim]")
        raise typer.Exit(1)

    pre_commit_path.write_text(hook_content)
    # Make executable
    pre_commit_path.chmod(pre_commit_path.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)

    console.print(f"[green]Installed pre-commit hook at {pre_commit_path}[/green]")
    console.print("\n[dim]The hook will run 'melee-agent hook validate' before each commit.[/dim]")


@hook_app.command("uninstall")
def hook_uninstall():
    """Remove git pre-commit hook."""
    project_root = Path(__file__).parent.parent.parent
    hooks_dir = project_root / ".git" / "hooks"
    pre_commit_path = hooks_dir / "pre-commit"

    if not pre_commit_path.exists():
        console.print("[yellow]No pre-commit hook installed[/yellow]")
        return

    # Check if it's our hook
    content = pre_commit_path.read_text()
    if "melee-agent" not in content and "validate_commit" not in content:
        console.print("[yellow]Pre-commit hook exists but wasn't installed by melee-agent[/yellow]")
        console.print("[dim]Remove manually if desired[/dim]")
        raise typer.Exit(1)

    pre_commit_path.unlink()
    console.print(f"[green]Removed pre-commit hook[/green]")
