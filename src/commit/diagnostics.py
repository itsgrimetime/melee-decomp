"""Diagnostics for commit errors - suggest fixes for common issues."""

import re
from pathlib import Path
from typing import Optional


# Common type -> header mappings for Melee codebase
TYPE_TO_HEADER = {
    # baselib types
    "HSD_GObj": "<baselib/gobj.h>",
    "HSD_JObj": "<baselib/jobj.h>",
    "HSD_DObj": "<baselib/dobj.h>",
    "HSD_MObj": "<baselib/mobj.h>",
    "HSD_TObj": "<baselib/tobj.h>",
    "HSD_AObj": "<baselib/aobj.h>",
    "HSD_FObj": "<baselib/fobj.h>",
    "HSD_PObj": "<baselib/pobj.h>",
    "HSD_CObj": "<baselib/cobj.h>",
    "HSD_LObj": "<baselib/lobj.h>",
    "HSD_WObj": "<baselib/wobj.h>",
    "HSD_Pad": "<baselib/pad.h>",
    "HSD_Archive": "<baselib/archive.h>",

    # Melee fighter types
    "Fighter": "<melee/ft/forward.h>",
    "Fighter_GObj": "<melee/ft/forward.h>",
    "ftCo_GObj": "<melee/ft/forward.h>",
    "ftCo_Fighter": "<melee/ft/forward.h>",
    "FighterData": "<melee/ft/ftdata.h>",
    "FtCmd2": "<melee/ft/ftcmd.h>",
    "CommandInfo": "<melee/ft/ftcmd.h>",
    "ftCmd_Acmd": "<melee/ft/ftcmd.h>",

    # Melee item types
    "Item": "<melee/it/forward.h>",
    "Item_GObj": "<melee/it/forward.h>",
    "ItemData": "<melee/it/itdata.h>",

    # Melee lb types
    "ColorOverlay": "<melee/lb/lb_00F9.h>",
    "lb_UnkAnimStruct": "<melee/lb/lb_00F9.h>",

    # Common math types
    "Vec2": "<dolphin/mtx/mtxtypes.h>",
    "Vec3": "<dolphin/mtx/mtxtypes.h>",
    "Mtx": "<dolphin/mtx/mtxtypes.h>",
    "Mtx44": "<dolphin/mtx/mtxtypes.h>",
    "Quaternion": "<dolphin/mtx/mtxtypes.h>",

    # Primitive types
    "s8": "<platform.h>",
    "s16": "<platform.h>",
    "s32": "<platform.h>",
    "s64": "<platform.h>",
    "u8": "<platform.h>",
    "u16": "<platform.h>",
    "u32": "<platform.h>",
    "u64": "<platform.h>",
    "f32": "<platform.h>",
    "f64": "<platform.h>",
    "BOOL": "<platform.h>",
    "bool8_t": "<platform.h>",

    # Dolphin types
    "GXColor": "<dolphin/gx/GXStruct.h>",
    "GXTexObj": "<dolphin/gx/GXTexture.h>",
    "OSAlarm": "<dolphin/os/OSAlarm.h>",
    "OSContext": "<dolphin/os/OSContext.h>",
    "OSThread": "<dolphin/os/OSThread.h>",

    # Common structs
    "CollData": "<melee/ft/ftcoll.h>",
    "HitCapsule": "<melee/ft/ftcoll.h>",
    "ECB": "<melee/ft/ftcoll.h>",
    "ftHit_UnkStruct": "<melee/ft/fthit.h>",
}

# Patterns for undeclared identifier errors
UNDECLARED_PATTERNS = [
    re.compile(r"[Ee]rror:?\s*'(\w+)'\s*undeclared", re.IGNORECASE),
    re.compile(r"[Ee]rror:?\s*unknown type name\s*'(\w+)'", re.IGNORECASE),
    re.compile(r"[Ee]rror:?\s*use of undeclared identifier\s*'(\w+)'", re.IGNORECASE),
    re.compile(r"[Ee]rror:?\s*incomplete type\s*'(?:struct\s+)?(\w+)'", re.IGNORECASE),
    re.compile(r"declaration of '(\w+)' as pointer to incomplete type", re.IGNORECASE),
]

# Patterns for function signature mismatches
SIGNATURE_PATTERNS = [
    re.compile(r"conflicting types for\s*'(\w+)'", re.IGNORECASE),
    re.compile(r"previous declaration of\s*'(\w+)'", re.IGNORECASE),
    re.compile(r"incompatible pointer type.*'(\w+)'", re.IGNORECASE),
]


def extract_undefined_identifiers(error_output: str) -> list[str]:
    """Extract undefined identifier names from compiler error output."""
    identifiers = set()

    for pattern in UNDECLARED_PATTERNS:
        for match in pattern.finditer(error_output):
            identifiers.add(match.group(1))

    return list(identifiers)


def extract_conflicting_functions(error_output: str) -> list[str]:
    """Extract function names with signature conflicts from error output."""
    functions = set()

    for pattern in SIGNATURE_PATTERNS:
        for match in pattern.finditer(error_output):
            functions.add(match.group(1))

    return list(functions)


def suggest_includes(error_output: str) -> list[tuple[str, str]]:
    """Suggest includes based on compilation errors.

    Returns list of (type_name, suggested_include) tuples.
    """
    suggestions = []
    identifiers = extract_undefined_identifiers(error_output)

    for ident in identifiers:
        if ident in TYPE_TO_HEADER:
            suggestions.append((ident, TYPE_TO_HEADER[ident]))

    return suggestions


def format_diagnostic_message(error_output: str) -> Optional[str]:
    """Format a helpful diagnostic message for compilation errors.

    Returns None if no suggestions available.
    """
    suggestions = suggest_includes(error_output)
    conflicts = extract_conflicting_functions(error_output)

    if not suggestions and not conflicts:
        return None

    lines = []

    if suggestions:
        lines.append("\n[bold cyan]Suggested fixes:[/bold cyan]")
        # Group by header
        by_header: dict[str, list[str]] = {}
        for type_name, header in suggestions:
            if header not in by_header:
                by_header[header] = []
            by_header[header].append(type_name)

        for header, types in by_header.items():
            types_str = ", ".join(types)
            lines.append(f"  [yellow]Add:[/yellow] #include {header}")
            lines.append(f"       [dim](provides: {types_str})[/dim]")

    if conflicts:
        lines.append("\n[bold cyan]Signature conflicts:[/bold cyan]")
        for func in conflicts:
            lines.append(f"  [yellow]Check:[/yellow] {func}() signature in header vs scratch")
            lines.append(f"       [dim]May need to update header declaration[/dim]")

    return "\n".join(lines)


def analyze_commit_error(error_output: str, file_path: str) -> str:
    """Provide comprehensive analysis of a commit compilation error.

    Args:
        error_output: Raw compiler error output
        file_path: Path to the source file being compiled

    Returns:
        Formatted diagnostic message with suggestions
    """
    lines = []

    # Extract actual error lines for display
    error_lines = [l for l in error_output.split('\n')
                   if 'error:' in l.lower() or 'Error:' in l]
    if error_lines:
        lines.append("[red]Compilation errors:[/red]")
        for line in error_lines[:5]:  # Show first 5 errors
            lines.append(f"  {line.strip()}")

    # Add suggestions
    diagnostic = format_diagnostic_message(error_output)
    if diagnostic:
        lines.append(diagnostic)
    else:
        lines.append("\n[dim]No automatic suggestions available.[/dim]")
        lines.append("[dim]Check the scratch context for required types/functions.[/dim]")

    return "\n".join(lines)


# =============================================================================
# Header Signature Sync Detection
# =============================================================================

def extract_function_signature(code: str, function_name: str) -> Optional[str]:
    """Extract the function signature from source code.

    Args:
        code: Source code containing the function
        function_name: Name of the function

    Returns:
        Function signature string like "void func(int a, float b)" or None
    """
    # Pattern to match function definition (not just declaration)
    # Handles: return_type [modifiers] func_name(params)
    pattern = re.compile(
        rf'^([^\n{{;]*?)\s*\b{re.escape(function_name)}\s*\(([^)]*)\)\s*\{{',
        re.MULTILINE
    )

    match = pattern.search(code)
    if not match:
        return None

    return_type = match.group(1).strip()
    params = match.group(2).strip()

    # Clean up return type (remove 'static', 'inline', etc. for comparison)
    return_type = re.sub(r'\b(static|inline)\s+', '', return_type).strip()

    return f"{return_type} {function_name}({params})"


def extract_header_declaration(header_path: Path, function_name: str) -> Optional[str]:
    """Extract function declaration from a header file.

    Args:
        header_path: Path to the header file
        function_name: Name of the function

    Returns:
        Declaration string or None if not found
    """
    if not header_path.exists():
        return None

    content = header_path.read_text()

    # Pattern for function declaration in header
    # Handles: /* addr */ return_type func_name(params);
    pattern = re.compile(
        rf'(?:/\*[^*]*\*/\s*)?([^\n;{{]*?)\s*\b{re.escape(function_name)}\s*\(([^)]*)\)\s*;',
        re.MULTILINE
    )

    match = pattern.search(content)
    if not match:
        return None

    return_type = match.group(1).strip()
    params = match.group(2).strip()

    # Clean up return type
    return_type = re.sub(r'\b(extern)\s+', '', return_type).strip()

    return f"{return_type} {function_name}({params})"


def normalize_signature(sig: str) -> str:
    """Normalize a signature for comparison (remove extra whitespace, etc.)."""
    if not sig:
        return ""
    # Remove comments
    sig = re.sub(r'/\*[^*]*\*/', '', sig)
    # Normalize whitespace
    sig = ' '.join(sig.split())
    # Remove parameter names (keep just types)
    # This is a simplified version - handles common cases
    sig = re.sub(r'(\w+)\s+\w+\s*([,)])', r'\1\2', sig)
    return sig


def compare_signatures(scratch_sig: str, header_sig: str) -> dict:
    """Compare two function signatures.

    Returns dict with:
        - match: bool - whether they match
        - scratch: str - normalized scratch signature
        - header: str - normalized header signature
        - issues: list[str] - specific differences found
    """
    result = {
        "match": False,
        "scratch": scratch_sig,
        "header": header_sig,
        "issues": [],
    }

    if not scratch_sig or not header_sig:
        result["issues"].append("Could not parse one or both signatures")
        return result

    norm_scratch = normalize_signature(scratch_sig)
    norm_header = normalize_signature(header_sig)

    if norm_scratch == norm_header:
        result["match"] = True
        return result

    # Check for common issues
    # UNK_RET / UNK_PARAMS placeholders
    if "UNK_RET" in header_sig or "UNK_PARAMS" in header_sig:
        result["issues"].append("Header uses UNK_RET/UNK_PARAMS placeholder - needs update")

    # Different return types
    scratch_ret = scratch_sig.split('(')[0].rsplit(None, 1)[0] if '(' in scratch_sig else ""
    header_ret = header_sig.split('(')[0].rsplit(None, 1)[0] if '(' in header_sig else ""
    if scratch_ret != header_ret:
        result["issues"].append(f"Return type: scratch='{scratch_ret}' vs header='{header_ret}'")

    # Different parameter count
    scratch_params = scratch_sig.split('(')[1].rstrip(')') if '(' in scratch_sig else ""
    header_params = header_sig.split('(')[1].rstrip(')') if '(' in header_sig else ""
    scratch_count = len([p for p in scratch_params.split(',') if p.strip()]) if scratch_params else 0
    header_count = len([p for p in header_params.split(',') if p.strip()]) if header_params else 0
    if scratch_count != header_count:
        result["issues"].append(f"Parameter count: scratch={scratch_count} vs header={header_count}")

    return result


def check_header_sync(
    source_code: str,
    function_name: str,
    melee_root: Path,
    file_path: str
) -> Optional[dict]:
    """Check if function signature in scratch matches header declaration.

    Args:
        source_code: The scratch source code
        function_name: Function being committed
        melee_root: Path to melee project root
        file_path: Relative path to source file (e.g., "melee/lb/lbbgflash.c")

    Returns:
        Dict with comparison results, or None if check couldn't be performed
    """
    # Extract signature from scratch
    scratch_sig = extract_function_signature(source_code, function_name)

    # Find corresponding header file
    header_path = melee_root / "src" / file_path.replace('.c', '.h')
    if not header_path.exists():
        # Try include directory
        header_path = melee_root / "include" / file_path.replace('.c', '.h')

    if not header_path.exists():
        return None

    # Extract declaration from header
    header_sig = extract_header_declaration(header_path, function_name)
    if not header_sig:
        return None

    result = compare_signatures(scratch_sig, header_sig)
    result["header_path"] = str(header_path)

    return result


def format_signature_mismatch(comparison: dict) -> str:
    """Format a signature mismatch as a helpful message."""
    lines = ["\n[bold yellow]Header signature mismatch detected:[/bold yellow]"]
    lines.append(f"  [dim]Header:[/dim]  {comparison['header']}")
    lines.append(f"  [dim]Scratch:[/dim] {comparison['scratch']}")

    if comparison.get("issues"):
        lines.append("\n  [yellow]Issues:[/yellow]")
        for issue in comparison["issues"]:
            lines.append(f"    • {issue}")

    lines.append(f"\n  [dim]Header file: {comparison.get('header_path', 'unknown')}[/dim]")
    lines.append("  [yellow]Consider updating the header declaration to match.[/yellow]")

    return "\n".join(lines)
