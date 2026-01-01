"""Struct commands - lookup struct layouts, field offsets, and callback signatures."""

import asyncio
import re
from pathlib import Path
from typing import Annotated, Optional

import typer
from rich.table import Table
from rich.panel import Panel

from ._common import console, get_agent_melee_root, get_local_api_url

struct_app = typer.Typer(help="Lookup struct layouts and field offsets")

# Known type issues in the headers that cause matching problems
# Format: (struct, field, declared_type, actual_type, notes)
KNOWN_TYPE_ISSUES = [
    ("Fighter.dmg", "x1894", "int", "HSD_GObj*", "Source gobj pointer, loaded with lwz then dereferenced"),
    ("Fighter.dmg", "x1898", "int", "float", "Damage rate, loaded with lfs instruction"),
    ("Fighter.dmg", "x1880", "int", "Vec3*", "Effect position pointer"),
    ("Item", "xD90", "union Struct2070", "union Struct2070", "Same as Fighter.x2070, access via xD90.x2073"),
]

# Common struct locations
STRUCT_FILES = {
    "Fighter": "src/melee/ft/types.h",
    "Item": "src/melee/it/types.h",
    "HSD_GObj": "src/sysdolphin/baselib/gobj.h",
    "ftCo_DatAttrs": "src/melee/ft/types.h",
    "CollData": "src/melee/lb/types.h",
}


def _parse_struct_fields(content: str, struct_name: str) -> list[dict]:
    """Parse struct fields from header content."""
    fields = []

    # Find struct definition
    # Match patterns like "struct Fighter {" or "struct dmg {"
    pattern = rf"struct\s+{re.escape(struct_name)}\s*\{{"
    match = re.search(pattern, content)
    if not match:
        return fields

    start = match.end()
    brace_count = 1
    end = start

    # Find matching closing brace
    for i, char in enumerate(content[start:], start):
        if char == '{':
            brace_count += 1
        elif char == '}':
            brace_count -= 1
            if brace_count == 0:
                end = i
                break

    struct_content = content[start:end]

    # Parse field lines with offset comments
    # Matches: /* fp+XXXX */ or /* +XXXX */ followed by type and name
    field_pattern = r'/\*\s*(?:fp\+)?([0-9A-Fa-fx]+)(?::(\d+))?\s*\*/\s*([^;]+?)\s*([a-zA-Z_][a-zA-Z0-9_]*(?:\[[^\]]*\])?)\s*;'

    for match in re.finditer(field_pattern, struct_content):
        offset_str = match.group(1)
        bit_offset = match.group(2)
        field_type = match.group(3).strip()
        field_name = match.group(4).strip()

        # Parse offset (hex or decimal)
        try:
            if offset_str.lower().startswith('0x'):
                offset = int(offset_str, 16)
            else:
                offset = int(offset_str, 16)  # Assume hex if no prefix
        except ValueError:
            continue

        field_info = {
            "offset": offset,
            "offset_hex": f"0x{offset:X}",
            "bit_offset": int(bit_offset) if bit_offset else None,
            "type": field_type,
            "name": field_name,
        }
        fields.append(field_info)

    return sorted(fields, key=lambda f: (f["offset"], f["bit_offset"] or 0))


def _find_struct_in_files(melee_root: Path, struct_name: str) -> tuple[Optional[Path], Optional[str]]:
    """Find which file contains a struct definition."""
    # Check known locations first
    if struct_name in STRUCT_FILES:
        path = melee_root / STRUCT_FILES[struct_name]
        if path.exists():
            return path, path.read_text()

    # Search in common type header files
    search_dirs = [
        melee_root / "src/melee/ft",
        melee_root / "src/melee/it",
        melee_root / "src/melee/lb",
        melee_root / "src/melee/gr",
        melee_root / "src/sysdolphin/baselib",
    ]

    pattern = rf"struct\s+{re.escape(struct_name)}\s*\{{"

    for search_dir in search_dirs:
        if not search_dir.exists():
            continue
        for header in search_dir.glob("*.h"):
            try:
                content = header.read_text()
                if re.search(pattern, content):
                    return header, content
            except Exception:
                continue

    return None, None


@struct_app.command("show")
def struct_show(
    struct_name: Annotated[str, typer.Argument(help="Name of the struct (e.g., Fighter, Item, dmg)")],
    offset: Annotated[
        Optional[str], typer.Option("--offset", "-o", help="Filter to fields near this offset (hex)")
    ] = None,
    search: Annotated[
        Optional[str], typer.Option("--search", "-s", help="Search for field by name pattern")
    ] = None,
):
    """Show struct layout with field offsets.

    Examples:
        melee-agent struct show Fighter
        melee-agent struct show Fighter --offset 0x1898
        melee-agent struct show dmg --search x189
    """
    melee_root = get_agent_melee_root()

    path, content = _find_struct_in_files(melee_root, struct_name)
    if not content:
        console.print(f"[red]Struct '{struct_name}' not found[/red]")
        console.print(f"[dim]Searched in: {melee_root}[/dim]")
        raise typer.Exit(1)

    fields = _parse_struct_fields(content, struct_name)
    if not fields:
        console.print(f"[yellow]Struct '{struct_name}' found but no fields parsed[/yellow]")
        console.print(f"[dim]File: {path}[/dim]")
        raise typer.Exit(1)

    # Filter by offset if specified
    target_offset = None
    if offset:
        try:
            target_offset = int(offset, 16) if offset.startswith("0x") else int(offset, 16)
        except ValueError:
            console.print(f"[red]Invalid offset: {offset}[/red]")
            raise typer.Exit(1)

        # Show fields within ±0x20 of target
        fields = [f for f in fields if abs(f["offset"] - target_offset) <= 0x20]

    # Filter by name pattern if specified
    if search:
        pattern = re.compile(search, re.IGNORECASE)
        fields = [f for f in fields if pattern.search(f["name"]) or pattern.search(f["type"])]

    if not fields:
        console.print(f"[yellow]No matching fields found[/yellow]")
        raise typer.Exit(0)

    # Build table
    table = Table(title=f"struct {struct_name}")
    table.add_column("Offset", style="cyan", justify="right")
    table.add_column("Type", style="green")
    table.add_column("Name", style="yellow")
    table.add_column("Notes", style="dim")

    for field in fields:
        offset_str = field["offset_hex"]
        if field["bit_offset"] is not None:
            offset_str += f":{field['bit_offset']}"

        # Check for known type issues
        notes = ""
        for issue in KNOWN_TYPE_ISSUES:
            issue_struct, issue_field, _, actual_type, issue_notes = issue
            if struct_name in issue_struct and field["name"] == issue_field:
                notes = f"⚠️ Should be {actual_type}"
                break

        table.add_row(offset_str, field["type"], field["name"], notes)

    console.print(table)
    console.print(f"\n[dim]Source: {path}[/dim]")


@struct_app.command("issues")
def struct_issues(
    struct_filter: Annotated[
        Optional[str], typer.Argument(help="Filter issues by struct name")
    ] = None,
):
    """Show known type issues in struct definitions.

    These are fields where the declared type in the header doesn't match
    what the assembly actually expects. Use workaround casts when matching.
    """
    issues = KNOWN_TYPE_ISSUES
    if struct_filter:
        issues = [i for i in issues if struct_filter.lower() in i[0].lower()]

    if not issues:
        console.print("[green]No known type issues found[/green]")
        return

    table = Table(title="Known Type Issues")
    table.add_column("Struct.Field", style="cyan")
    table.add_column("Declared", style="red")
    table.add_column("Actual", style="green")
    table.add_column("Notes", style="dim")

    for struct_field, field_name, declared, actual, notes in issues:
        table.add_row(f"{struct_field}.{field_name}", declared, actual, notes)

    console.print(table)
    console.print("\n[bold]Workaround Example:[/bold]")
    console.print("""
[dim]When x1898 is declared as int but used as float:[/dim]
[green]#define DMG_X1898(fp) (*(float*)&(fp)->dmg.x1898)[/green]

[dim]When x1894 is declared as int but used as pointer:[/dim]
[green]#define DMG_X1894(fp) ((HSD_GObj*)(fp)->dmg.x1894)[/green]
""")


@struct_app.command("offset")
def struct_offset(
    offset: Annotated[str, typer.Argument(help="Offset to look up (hex, e.g., 0x1898)")],
    struct_name: Annotated[
        str, typer.Option("--struct", "-s", help="Struct to search in")
    ] = "Fighter",
):
    """Look up what field is at a specific offset.

    Useful when reading assembly and seeing an offset like 0x1898(r30).

    Examples:
        melee-agent struct offset 0x1898
        melee-agent struct offset 0x1898 --struct Fighter
        melee-agent struct offset 0xD93 --struct Item
    """
    try:
        target = int(offset, 16) if offset.startswith("0x") else int(offset, 16)
    except ValueError:
        console.print(f"[red]Invalid offset: {offset}[/red]")
        raise typer.Exit(1)

    melee_root = get_agent_melee_root()
    path, content = _find_struct_in_files(melee_root, struct_name)

    if not content:
        console.print(f"[red]Struct '{struct_name}' not found[/red]")
        raise typer.Exit(1)

    fields = _parse_struct_fields(content, struct_name)

    # Find exact match or closest containing field
    exact_match = None
    containing_field = None

    for field in fields:
        if field["offset"] == target:
            exact_match = field
            break
        elif field["offset"] < target:
            containing_field = field

    if exact_match:
        console.print(f"[green]Exact match at 0x{target:X}:[/green]")
        console.print(f"  Type: [cyan]{exact_match['type']}[/cyan]")
        console.print(f"  Name: [yellow]{exact_match['name']}[/yellow]")

        # Check for known issues
        for issue in KNOWN_TYPE_ISSUES:
            if struct_name in issue[0] and exact_match["name"] == issue[1]:
                console.print(f"\n[red]⚠️ TYPE ISSUE:[/red] Declared as {issue[2]}, actually {issue[3]}")
                console.print(f"[dim]{issue[4]}[/dim]")
    elif containing_field:
        inner_offset = target - containing_field["offset"]
        console.print(f"[yellow]No exact match. Offset 0x{target:X} is within:[/yellow]")
        console.print(f"  Field: [cyan]{containing_field['type']}[/cyan] [yellow]{containing_field['name']}[/yellow]")
        console.print(f"  Base offset: 0x{containing_field['offset']:X}")
        console.print(f"  Inner offset: +0x{inner_offset:X} ({inner_offset} bytes in)")

        # If it's a nested struct, suggest looking there
        if "struct" in containing_field["type"] or containing_field["type"].startswith("Vec"):
            console.print(f"\n[dim]This is a nested type. Check the inner struct layout.[/dim]")
    else:
        console.print(f"[red]Offset 0x{target:X} not found in {struct_name}[/red]")
        if fields:
            console.print(f"[dim]Struct range: 0x{fields[0]['offset']:X} - 0x{fields[-1]['offset']:X}[/dim]")


# Common callback typedefs used in Melee
KNOWN_CALLBACKS = {
    "FtCmd2": {
        "signature": "void (*FtCmd2)(Fighter_GObj* gobj, CommandInfo* cmd, int arg2)",
        "header": "<melee/ft/ftcmd.h>",
        "description": "Command interpreter callback for fighter actions",
        "example": "static void my_callback(Fighter_GObj* gobj, CommandInfo* cmd, int arg2) {}"
    },
    "HSD_GObjEvent": {
        "signature": "void (*HSD_GObjEvent)(HSD_GObj* gobj)",
        "header": "<baselib/gobj.h>",
        "description": "Generic gobj event callback",
        "example": "static void my_event(HSD_GObj* gobj) {}"
    },
    "HSD_GObjPredicate": {
        "signature": "bool (*HSD_GObjPredicate)(HSD_GObj* gobj)",
        "header": "<baselib/gobj.h>",
        "description": "Gobj predicate for filtering",
        "example": "static bool my_predicate(HSD_GObj* gobj) { return TRUE; }"
    },
    "GObj_RenderFunc": {
        "signature": "void (*GObj_RenderFunc)(HSD_GObj* gobj, int code)",
        "header": "<baselib/gobj.h>",
        "description": "Render function callback",
        "example": "static void my_render(HSD_GObj* gobj, int code) {}"
    },
    "HSD_UserDataEvent": {
        "signature": "void (*HSD_UserDataEvent)(void* user_data)",
        "header": "<baselib/gobj.h>",
        "description": "User data cleanup callback",
        "example": "static void my_cleanup(void* user_data) {}"
    },
    "ftCo_Callback": {
        "signature": "void (*ftCo_Callback)(HSD_GObj* gobj)",
        "header": "<melee/ft/ftcommon.h>",
        "description": "Common fighter callback",
        "example": "static void my_callback(HSD_GObj* gobj) {}"
    },
}


@struct_app.command("callback")
def struct_callback(
    name: Annotated[Optional[str], typer.Argument(help="Callback type name (e.g., FtCmd2)")] = None,
    search: Annotated[
        Optional[str], typer.Option("--search", "-s", help="Search for callback by pattern")
    ] = None,
    slug: Annotated[
        Optional[str], typer.Option("--slug", help="Search scratch context for callback type")
    ] = None,
    api_url: Annotated[Optional[str], typer.Option("--api-url", help="Decomp.me API URL (auto-detected)")] = None,
):
    """Look up callback function signatures.

    When a function takes a callback parameter, use this to find the expected signature.

    Examples:
        melee-agent struct callback FtCmd2
        melee-agent struct callback --search Cmd
        melee-agent struct callback --slug abc123 --search lb_80014258
    """
    # If slug provided, search context for function and extract callback param
    if slug:
        if not search:
            console.print("[red]--search is required when using --slug[/red]")
            raise typer.Exit(1)

        api_url = api_url or get_local_api_url()
        from src.client import DecompMeAPIClient

        async def get():
            async with DecompMeAPIClient(base_url=api_url) as client:
                return await client.get_scratch(slug)

        scratch = asyncio.run(get())

        # Search for the function declaration
        pattern = re.compile(rf'\b{re.escape(search)}\s*\([^)]+\)', re.IGNORECASE)
        for match in pattern.finditer(scratch.context):
            # Get surrounding context
            start = max(0, match.start() - 100)
            end = min(len(scratch.context), match.end() + 50)
            snippet = scratch.context[start:end]

            console.print(f"[bold cyan]Found:[/bold cyan] {match.group()}")

            # Try to extract the signature
            func_text = match.group()
            console.print(f"\n[dim]{snippet}[/dim]")

            # Look for callback parameters (typedef'd function pointers)
            for cb_name, cb_info in KNOWN_CALLBACKS.items():
                if cb_name.lower() in func_text.lower():
                    console.print(f"\n[bold green]Callback parameter: {cb_name}[/bold green]")
                    console.print(f"  Signature: [cyan]{cb_info['signature']}[/cyan]")
                    console.print(f"  Header: [yellow]{cb_info['header']}[/yellow]")
                    console.print(f"\n  [dim]Example:[/dim]")
                    console.print(f"  [green]{cb_info['example']}[/green]")
            console.print()
        return

    # List known callbacks
    if not name and not search:
        table = Table(title="Known Callback Types")
        table.add_column("Name", style="cyan")
        table.add_column("Signature", style="green")
        table.add_column("Description", style="dim")

        for cb_name, cb_info in KNOWN_CALLBACKS.items():
            table.add_row(cb_name, cb_info["signature"], cb_info["description"])

        console.print(table)
        console.print("\n[dim]Use 'melee-agent struct callback <name>' for details[/dim]")
        return

    # Search by pattern
    if search:
        matches = {k: v for k, v in KNOWN_CALLBACKS.items() if search.lower() in k.lower()}
        if not matches:
            console.print(f"[yellow]No callbacks matching '{search}'[/yellow]")
            return

        for cb_name, cb_info in matches.items():
            console.print(f"\n[bold cyan]{cb_name}[/bold cyan]")
            console.print(f"  Signature: [green]{cb_info['signature']}[/green]")
            console.print(f"  Header: [yellow]{cb_info['header']}[/yellow]")
            console.print(f"  {cb_info['description']}")
            console.print(f"\n  [dim]Example:[/dim]")
            console.print(f"  [green]{cb_info['example']}[/green]")
        return

    # Look up specific callback
    if name not in KNOWN_CALLBACKS:
        console.print(f"[red]Unknown callback type: {name}[/red]")
        console.print("[dim]Known types:[/dim]")
        for cb_name in KNOWN_CALLBACKS:
            console.print(f"  • {cb_name}")
        raise typer.Exit(1)

    cb_info = KNOWN_CALLBACKS[name]
    console.print(f"\n[bold cyan]{name}[/bold cyan]")
    console.print(f"  Signature: [green]{cb_info['signature']}[/green]")
    console.print(f"  Header: [yellow]{cb_info['header']}[/yellow]")
    console.print(f"  {cb_info['description']}")
    console.print(f"\n[bold]Example implementation:[/bold]")
    console.print(f"[green]{cb_info['example']}[/green]")
    console.print(f"\n[dim]Include {cb_info['header']} to use this type.[/dim]")
