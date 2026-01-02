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
    skip_regressions: Annotated[
        bool, typer.Option("--skip-regressions",
                           help="Skip build and regression check (faster)")
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
    - Match regressions (runs ninja by default)

    File Issues:
    - Forbidden files modified (.gitkeep files, orig/ placeholders)

    PR/Commit Issues:
    - Local scratch URLs in commits (must use production decomp.me URLs)
    """
    from src.hooks.validate_commit import CommitValidator

    validator = CommitValidator(melee_root=DEFAULT_MELEE_ROOT)
    errors, warnings, _check_results = validator.run(skip_regressions=skip_regressions)

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
    """Install git pre-commit hooks for validation.

    Installs two hooks:
    1. melee-decomp repo: Runs pytest tests, plus melee validation if submodule changes
    2. melee submodule: Runs melee validation (shared by all worktrees)
    """
    project_root = Path(__file__).parent.parent.parent

    # 1. Install hook for melee-decomp repo
    repo_hooks_dir = project_root / ".git" / "hooks"
    repo_pre_commit = repo_hooks_dir / "pre-commit"

    repo_hook_content = f'''#!/bin/sh
# Pre-commit hook for melee-decomp
# Installed by: melee-agent hook install

cd "{project_root}"

# Run Python tests
echo "Running tests..."
python -m pytest tests/ -x -q --tb=short
TEST_EXIT=$?

if [ $TEST_EXIT -ne 0 ]; then
    echo ""
    echo "\\033[31mTests failed - commit blocked\\033[0m"
    exit 1
fi

# Only run melee validation if there are melee/ submodule changes staged
if git diff --cached --name-only | grep -q "^melee/"; then
    echo "Melee submodule changes detected - running validation..."
    python -m src.hooks.validate_commit --verbose
    exit $?
fi

echo "\\033[32m✓ Tests passed\\033[0m"
exit 0
'''

    if repo_pre_commit.exists() and not force:
        console.print(f"[yellow]Pre-commit hook already exists at {repo_pre_commit}[/yellow]")
        console.print("[dim]Use --force to overwrite[/dim]")
        raise typer.Exit(1)

    repo_pre_commit.write_text(repo_hook_content)
    repo_pre_commit.chmod(repo_pre_commit.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
    console.print(f"[green]✓ Installed melee-decomp pre-commit hook[/green]")

    # 2. Install hook for melee submodule
    submodule_hooks_dir = project_root / ".git" / "modules" / "melee" / "hooks"

    if not submodule_hooks_dir.exists():
        console.print("[yellow]Melee submodule hooks dir not found - skipping submodule hook[/yellow]")
        console.print("[dim]Run 'git submodule update --init' to initialize[/dim]")
        return

    submodule_pre_commit = submodule_hooks_dir / "pre-commit"

    submodule_hook_content = f'''#!/bin/sh
# Pre-commit hook for melee decompilation
# Installed by: melee-agent hook install
# All worktrees share this hook automatically.

# Capture the worktree root before changing directories
# This is needed because worktrees have different staged files
WORKTREE_ROOT="$(git rev-parse --show-toplevel)"

cd "{project_root}"

# Run validation, passing the worktree root so it can check staged files correctly
python -m src.hooks.validate_commit --worktree "$WORKTREE_ROOT"

# Exit with validation result
exit $?
'''

    submodule_pre_commit.write_text(submodule_hook_content)
    submodule_pre_commit.chmod(submodule_pre_commit.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
    console.print(f"[green]✓ Installed melee submodule pre-commit hook[/green]")


@hook_app.command("uninstall")
def hook_uninstall():
    """Remove git pre-commit hooks installed by melee-agent."""
    project_root = Path(__file__).parent.parent.parent
    removed_any = False

    # 1. Remove melee-decomp repo hook
    repo_pre_commit = project_root / ".git" / "hooks" / "pre-commit"
    if repo_pre_commit.exists():
        content = repo_pre_commit.read_text()
        if "melee-agent" in content or "validate_commit" in content:
            repo_pre_commit.unlink()
            console.print("[green]✓ Removed melee-decomp pre-commit hook[/green]")
            removed_any = True
        else:
            console.print("[yellow]melee-decomp hook exists but wasn't installed by melee-agent[/yellow]")

    # 2. Remove melee submodule hook
    submodule_pre_commit = project_root / ".git" / "modules" / "melee" / "hooks" / "pre-commit"
    if submodule_pre_commit.exists():
        content = submodule_pre_commit.read_text()
        if "melee-agent" in content or "validate_commit" in content:
            submodule_pre_commit.unlink()
            console.print("[green]✓ Removed melee submodule pre-commit hook[/green]")
            removed_any = True
        else:
            console.print("[yellow]melee submodule hook exists but wasn't installed by melee-agent[/yellow]")

    if not removed_any:
        console.print("[yellow]No melee-agent hooks found to remove[/yellow]")
