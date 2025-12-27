"""Stub commands - manage stub markers in source files."""

import re
from pathlib import Path
from typing import Annotated, Optional, Tuple, List

import typer

from ._common import console, DEFAULT_MELEE_ROOT
from src.extractor import SymbolParser, SplitsParser


stub_app = typer.Typer(help="Manage stub markers in source files")


def _extract_address_from_name(function_name: str) -> Optional[int]:
    """Extract hex address from function name if present.

    Function names often end with their address, e.g.:
    - lbRefract_800225D4 -> 0x800225D4
    - Camera_8002C1A8 -> 0x8002C1A8
    - fn_8001E910 -> 0x8001E910

    Returns:
        Integer address if found, None otherwise.
    """
    # Pattern: underscore followed by 8 hex digits at end of name
    match = re.search(r'_([0-9A-Fa-f]{8})$', function_name)
    if match:
        return int(match.group(1), 16)
    return None


def _get_function_address(function_name: str, melee_root: Path) -> Optional[int]:
    """Get the address of a function.

    First tries to extract from function name, then falls back to symbols.txt.

    Returns:
        Integer address if found, None otherwise.
    """
    # Try extracting from name first (faster)
    addr = _extract_address_from_name(function_name)
    if addr:
        return addr

    # Fall back to symbols.txt
    try:
        parser = SymbolParser(melee_root)
        symbol = parser.get_function_symbol(function_name)
        if symbol:
            return int(symbol.address, 16)
    except (FileNotFoundError, ValueError):
        pass

    return None


def _get_source_file_for_address(address: int, melee_root: Path) -> Optional[str]:
    """Find the source file that contains a function address.

    Uses splits.txt to map addresses to source files.

    Returns:
        Relative file path (e.g., "melee/lb/lbrefract.c") or None.
    """
    try:
        parser = SplitsParser(melee_root)
        return parser.get_file_for_address(address)
    except FileNotFoundError:
        return None


def _find_existing_stub_or_function(
    content: str,
    function_name: str
) -> Optional[Tuple[int, str]]:
    """Check if function already exists in file (as stub or definition).

    Returns:
        Tuple of (line_number, type) where type is "stub" or "definition",
        or None if not found.
    """
    lines = content.split('\n')

    # Check for stub marker
    stub_pattern = re.compile(rf'^///\s*#\s*{re.escape(function_name)}\s*$')

    # Check for function definition
    # Pattern matches things like: void func_name( or s32 func_name(
    def_pattern = re.compile(
        rf'^\s*(?:static\s+)?(?:inline\s+)?[\w\*\s]+\s+{re.escape(function_name)}\s*\('
    )

    for i, line in enumerate(lines):
        if stub_pattern.match(line):
            return (i + 1, "stub")
        if def_pattern.match(line):
            return (i + 1, "definition")

    return None


def _parse_stubs_and_functions(
    content: str,
    melee_root: Path
) -> List[Tuple[int, int, str, str]]:
    """Parse all stubs and function definitions from file content.

    Returns:
        List of (line_number, address, type, name) tuples, sorted by address.
        type is "stub" or "definition"
    """
    lines = content.split('\n')
    items = []

    # Pattern for stub markers: /// #function_name
    stub_pattern = re.compile(r'^///\s*#\s*(\w+)\s*$')

    # Pattern for function definitions (simplified - we just need the name)
    # Matches: return_type function_name(
    func_pattern = re.compile(
        r'^(?:static\s+)?(?:inline\s+)?[\w\*]+\s+(\w+)\s*\('
    )

    for i, line in enumerate(lines):
        # Check for stub
        stub_match = stub_pattern.match(line)
        if stub_match:
            func_name = stub_match.group(1)
            addr = _get_function_address(func_name, melee_root)
            if addr:
                items.append((i + 1, addr, "stub", func_name))
            continue

        # Check for function definition
        func_match = func_pattern.match(line)
        if func_match:
            func_name = func_match.group(1)
            addr = _get_function_address(func_name, melee_root)
            if addr:
                items.append((i + 1, addr, "definition", func_name))

    # Sort by address
    items.sort(key=lambda x: x[1])
    return items


def _find_insertion_line(
    content: str,
    target_address: int,
    melee_root: Path
) -> Tuple[int, str]:
    """Find the line number where a stub should be inserted.

    Stubs and functions should be in address order. This finds the correct
    position to maintain that ordering.

    Returns:
        Tuple of (line_number, context_description)
        line_number is 1-indexed and indicates where to insert BEFORE.
    """
    items = _parse_stubs_and_functions(content, melee_root)

    if not items:
        # No existing stubs or functions with addresses
        # Insert after includes (find last #include line or start of file)
        lines = content.split('\n')
        last_include_line = 0
        for i, line in enumerate(lines):
            if line.startswith('#include'):
                last_include_line = i + 1

        # Insert after includes with a blank line
        insert_line = last_include_line + 1 if last_include_line > 0 else 1
        return (insert_line, "after includes (first stub in file)")

    # Find position based on address ordering
    for i, (line_num, addr, item_type, name) in enumerate(items):
        if target_address < addr:
            # Insert before this item
            return (line_num, f"before {name} (0x{addr:08X})")

    # Target address is after all existing items
    # Insert after the last item
    last_line, last_addr, last_type, last_name = items[-1]

    # Find the end of the last item
    lines = content.split('\n')
    if last_type == "stub":
        # Stub is a single line, insert after it
        insert_line = last_line + 1
    else:
        # Function definition - need to find closing brace
        brace_count = 0
        in_function = False
        for i in range(last_line - 1, len(lines)):
            line = lines[i]
            brace_count += line.count('{')
            brace_count -= line.count('}')
            if '{' in line:
                in_function = True
            if in_function and brace_count == 0:
                insert_line = i + 2  # After closing brace + blank line
                break
        else:
            insert_line = len(lines)

    return (insert_line, f"after {last_name} (0x{last_addr:08X})")


def _insert_stub_at_line(content: str, line_num: int, function_name: str) -> str:
    """Insert a stub marker at the specified line.

    Ensures proper blank line formatting around the stub.

    Args:
        content: File content
        line_num: 1-indexed line number to insert before
        function_name: Name of the function

    Returns:
        Modified file content with stub inserted.
    """
    lines = content.split('\n')
    stub_line = f"/// #{function_name}"

    # Adjust for 0-indexed list
    insert_idx = line_num - 1

    # Ensure we don't go past end of file
    if insert_idx >= len(lines):
        insert_idx = len(lines)

    # Check if we need blank lines for formatting
    needs_blank_before = False
    needs_blank_after = False

    if insert_idx > 0:
        prev_line = lines[insert_idx - 1].strip()
        # Need blank before if previous line is not blank and not a stub
        if prev_line and not prev_line.startswith('/// #'):
            needs_blank_before = True

    if insert_idx < len(lines):
        next_line = lines[insert_idx].strip()
        # Need blank after if next line is not blank and not a stub
        if next_line and not next_line.startswith('/// #'):
            needs_blank_after = True

    # Build the insertion
    insertion = []
    if needs_blank_before:
        insertion.append('')
    insertion.append(stub_line)
    if needs_blank_after:
        insertion.append('')

    # Insert lines
    for i, new_line in enumerate(insertion):
        lines.insert(insert_idx + i, new_line)

    return '\n'.join(lines)


@stub_app.command("add")
def stub_add(
    function_name: Annotated[str, typer.Argument(help="Name of the function to add stub for")],
    melee_root: Annotated[
        Path, typer.Option("--melee-root", "-m", help="Path to melee submodule")
    ] = DEFAULT_MELEE_ROOT,
    dry_run: Annotated[
        bool, typer.Option("--dry-run", help="Show what would be done without modifying files")
    ] = False,
):
    """Add a stub marker for a function in the correct source file.

    Stub markers (/// #function_name) are placeholders for unimplemented
    functions. They allow the commit workflow to replace them with actual
    implementations.

    The command automatically:
    1. Finds the correct source file using the function's address
    2. Determines the correct position (stubs/functions are in address order)
    3. Inserts the stub marker with proper formatting

    Example:
        melee-agent stub add lbRefract_800225D4
    """
    # Get function address
    address = _get_function_address(function_name, melee_root)
    if not address:
        console.print(f"[red]Could not determine address for function '{function_name}'[/red]")
        console.print("[dim]Function name should end with address (e.g., func_800225D4)[/dim]")
        console.print("[dim]Or function should exist in config/GALE01/symbols.txt[/dim]")
        raise typer.Exit(1)

    console.print(f"[dim]Function address: 0x{address:08X}[/dim]")

    # Find source file
    source_file = _get_source_file_for_address(address, melee_root)
    if not source_file:
        console.print(f"[red]Could not find source file for address 0x{address:08X}[/red]")
        console.print("[dim]Check that config/GALE01/splits.txt exists and contains this address range[/dim]")
        raise typer.Exit(1)

    console.print(f"[dim]Source file: {source_file}[/dim]")

    # Read file
    full_path = melee_root / "src" / source_file
    if not full_path.exists():
        console.print(f"[red]Source file not found: {full_path}[/red]")
        raise typer.Exit(1)

    content = full_path.read_text(encoding='utf-8')

    # Check if function already exists
    existing = _find_existing_stub_or_function(content, function_name)
    if existing:
        line_num, item_type = existing
        console.print(f"[yellow]Function '{function_name}' already exists as {item_type} at line {line_num}[/yellow]")
        raise typer.Exit(0)

    # Find insertion point
    insert_line, context = _find_insertion_line(content, address, melee_root)

    if dry_run:
        console.print(f"\n[bold cyan]DRY RUN[/bold cyan] - No changes will be made\n")
        console.print(f"Would add stub marker for [cyan]{function_name}[/cyan]")
        console.print(f"  File: [green]{source_file}[/green]")
        console.print(f"  Line: {insert_line} ({context})")
        console.print(f"  Marker: [dim]/// #{function_name}[/dim]")
        return

    # Insert stub
    new_content = _insert_stub_at_line(content, insert_line, function_name)

    # Write file
    full_path.write_text(new_content, encoding='utf-8')

    # Calculate actual line (may shift due to blank line insertions)
    # Re-read to find actual line number
    new_content = full_path.read_text(encoding='utf-8')
    actual_line = None
    for i, line in enumerate(new_content.split('\n')):
        if line.strip() == f"/// #{function_name}":
            actual_line = i + 1
            break

    console.print(f"[green]Added stub marker for {function_name} at line {actual_line} of src/{source_file}[/green]")


@stub_app.command("list")
def stub_list(
    source_file: Annotated[
        Optional[str], typer.Argument(help="Source file path (relative to src/)")
    ] = None,
    melee_root: Annotated[
        Path, typer.Option("--melee-root", "-m", help="Path to melee submodule")
    ] = DEFAULT_MELEE_ROOT,
    limit: Annotated[
        int, typer.Option("--limit", "-n", help="Maximum number of results")
    ] = 50,
):
    """List stub markers in source files.

    If a source file is specified, lists stubs in that file.
    Otherwise, lists all stubs across the project.

    Example:
        melee-agent stub list melee/lb/lbrefract.c
        melee-agent stub list --limit 20
    """
    from rich.table import Table

    if source_file:
        # List stubs in specific file
        full_path = melee_root / "src" / source_file
        if not full_path.exists():
            console.print(f"[red]Source file not found: {full_path}[/red]")
            raise typer.Exit(1)

        content = full_path.read_text(encoding='utf-8')
        items = _parse_stubs_and_functions(content, melee_root)

        stubs = [(line, addr, name) for line, addr, item_type, name in items if item_type == "stub"]

        if not stubs:
            console.print(f"[yellow]No stub markers found in {source_file}[/yellow]")
            return

        table = Table(title=f"Stub Markers in {source_file}")
        table.add_column("Line", style="dim")
        table.add_column("Function", style="cyan")
        table.add_column("Address", style="green")

        for line, addr, name in stubs[:limit]:
            table.add_row(str(line), name, f"0x{addr:08X}")

        console.print(table)
        console.print(f"\n[dim]Found {len(stubs)} stub markers[/dim]")

    else:
        # List all stubs across project
        import subprocess

        result = subprocess.run(
            ["grep", "-r", "-n", "^/// #", "--include=*.c", "."],
            cwd=melee_root / "src",
            capture_output=True, text=True
        )

        if not result.stdout.strip():
            console.print("[yellow]No stub markers found in project[/yellow]")
            return

        table = Table(title="Stub Markers")
        table.add_column("File", style="green")
        table.add_column("Line", style="dim")
        table.add_column("Function", style="cyan")

        lines = result.stdout.strip().split('\n')
        count = 0
        for line in lines:
            if count >= limit:
                break
            # Parse: ./path/file.c:123:/// #func_name
            match = re.match(r'^\./(.+):(\d+):///\s*#\s*(\w+)', line)
            if match:
                file_path, line_num, func_name = match.groups()
                table.add_row(file_path, line_num, func_name)
                count += 1

        console.print(table)
        total = len(lines)
        if total > limit:
            console.print(f"\n[dim]Showing {limit} of {total} stub markers (use --limit to show more)[/dim]")
        else:
            console.print(f"\n[dim]Found {total} stub markers[/dim]")


@stub_app.command("check")
def stub_check(
    function_name: Annotated[str, typer.Argument(help="Name of the function to check")],
    melee_root: Annotated[
        Path, typer.Option("--melee-root", "-m", help="Path to melee submodule")
    ] = DEFAULT_MELEE_ROOT,
):
    """Check if a function needs a stub marker.

    Reports whether the function:
    - Already has a stub marker
    - Already has an implementation
    - Needs a stub marker to be added

    Example:
        melee-agent stub check lbRefract_800225D4
    """
    # Get function address
    address = _get_function_address(function_name, melee_root)
    if not address:
        console.print(f"[red]Could not determine address for function '{function_name}'[/red]")
        raise typer.Exit(1)

    # Find source file
    source_file = _get_source_file_for_address(address, melee_root)
    if not source_file:
        console.print(f"[red]Could not find source file for address 0x{address:08X}[/red]")
        raise typer.Exit(1)

    # Read file
    full_path = melee_root / "src" / source_file
    if not full_path.exists():
        console.print(f"[red]Source file not found: {full_path}[/red]")
        raise typer.Exit(1)

    content = full_path.read_text(encoding='utf-8')

    # Check if function exists
    existing = _find_existing_stub_or_function(content, function_name)
    if existing:
        line_num, item_type = existing
        if item_type == "stub":
            console.print(f"[green]Function has stub marker at line {line_num} of src/{source_file}[/green]")
            console.print("[dim]Ready for commit workflow to replace stub with implementation[/dim]")
        else:
            console.print(f"[green]Function is already implemented at line {line_num} of src/{source_file}[/green]")
            console.print("[dim]No stub needed - function definition exists[/dim]")
    else:
        console.print(f"[yellow]Function needs stub marker in src/{source_file}[/yellow]")
        console.print(f"[dim]Run: melee-agent stub add {function_name}[/dim]")

        # Show where it would go
        insert_line, context = _find_insertion_line(content, address, melee_root)
        console.print(f"[dim]Would be inserted at line {insert_line} ({context})[/dim]")
