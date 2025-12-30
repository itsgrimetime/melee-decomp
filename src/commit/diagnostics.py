"""Diagnostics for commit errors - suggest fixes for common issues."""

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional


@dataclass
class CompilerError:
    """Structured representation of a compiler error."""
    file_path: str = ""
    line_number: int = 0
    column: int = 0
    error_type: str = ""  # e.g., "error", "warning", "linker error"
    message: str = ""
    context_line: str = ""  # The source line if available
    raw_output: str = ""


@dataclass
class DiagnosticResult:
    """Complete diagnostic result for build failures."""
    errors: list[CompilerError] = field(default_factory=list)
    header_mismatch: Optional[dict] = None
    undefined_symbols: list[tuple[str, str]] = field(default_factory=list)  # (symbol, suggested_header)
    linker_errors: list[str] = field(default_factory=list)
    suggestions: list[str] = field(default_factory=list)


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

# Patterns for linker errors
LINKER_PATTERNS = [
    re.compile(r"undefined reference to [`'](\w+)'", re.IGNORECASE),
    re.compile(r"unresolved external symbol[:\s]*(\w+)", re.IGNORECASE),
    re.compile(r"Link Error.*Undefined:\s*(\w+)", re.IGNORECASE),
]

# Pattern to extract file/line from MWCC compiler output
# MWCC format examples:
#   ### mwcc Compiler ...
#   #   File: src/melee/ft/chara/ftKirby/ftKb_Init.c
#   # ----------------------------------------
#   #      118:   void ftKb_SpecialN_800F1CD8(HSD_GObj* gobj) {
#   #   Error:                                        ^^^^
#   # identifier expected
MWCC_FILE_PATTERN = re.compile(r"#\s*File:\s*(.+?)(?:\s*$|\n)", re.MULTILINE)
MWCC_LINE_PATTERN = re.compile(r"#\s*(\d+):\s*(.+?)(?:\s*$|\n)", re.MULTILINE)
MWCC_ERROR_MARKER = re.compile(r"#\s*Error:\s*(\^+)", re.MULTILINE)

# Pattern for clang/gcc style errors (file:line:col: error: message)
CLANG_ERROR_PATTERN = re.compile(
    r"([^\s:]+):(\d+):(\d+):\s*(error|warning):\s*(.+?)(?:\n|$)",
    re.MULTILINE
)


def parse_mwcc_errors(error_output: str) -> list[CompilerError]:
    """Parse MWCC compiler output into structured errors.

    MWCC outputs errors in a specific format:
        ### mwcc Compiler ...
        #   File: path/to/file.c
        # ----------------------------------------
        #      123:   code line here
        #   Error:              ^^^^
        # error message here

    Args:
        error_output: Raw compiler output

    Returns:
        List of structured CompilerError objects
    """
    errors = []
    current_file = ""
    current_line = 0
    current_context = ""

    lines = error_output.split('\n')
    i = 0
    while i < len(lines):
        line = lines[i]

        # Check for file marker
        file_match = MWCC_FILE_PATTERN.search(line)
        if file_match:
            current_file = file_match.group(1).strip()
            i += 1
            continue

        # Check for line number + context
        line_match = MWCC_LINE_PATTERN.search(line)
        if line_match:
            current_line = int(line_match.group(1))
            current_context = line_match.group(2).strip()
            i += 1
            continue

        # Check for error marker (^^^^)
        error_marker_match = MWCC_ERROR_MARKER.search(line)
        if error_marker_match:
            # Next line should be the error message
            error_msg = ""
            column = line.find('^') - line.find(':') if '^' in line else 0

            # Look ahead for error message
            if i + 1 < len(lines):
                next_line = lines[i + 1].strip()
                # Skip lines that are just markers or comments
                if next_line and not next_line.startswith('#   Error:') and not next_line.startswith('---'):
                    # Remove leading "# " if present
                    if next_line.startswith('#'):
                        next_line = next_line[1:].strip()
                    error_msg = next_line

            if current_file and error_msg:
                errors.append(CompilerError(
                    file_path=current_file,
                    line_number=current_line,
                    column=max(0, column),
                    error_type="error",
                    message=error_msg,
                    context_line=current_context,
                    raw_output=f"{line}\n{error_msg}" if error_msg else line,
                ))
            i += 1
            continue

        i += 1

    # Also try clang/gcc style parsing as fallback
    for match in CLANG_ERROR_PATTERN.finditer(error_output):
        errors.append(CompilerError(
            file_path=match.group(1),
            line_number=int(match.group(2)),
            column=int(match.group(3)),
            error_type=match.group(4),
            message=match.group(5),
            context_line="",
            raw_output=match.group(0),
        ))

    return errors


def extract_linker_errors(error_output: str) -> list[str]:
    """Extract undefined symbol names from linker errors.

    Args:
        error_output: Raw linker output

    Returns:
        List of undefined symbol names
    """
    symbols = set()
    for pattern in LINKER_PATTERNS:
        for match in pattern.finditer(error_output):
            symbols.add(match.group(1))
    return list(symbols)


def find_header_for_function(function_name: str, melee_root: Path) -> Optional[str]:
    """Find which header declares a given function.

    Args:
        function_name: Function to search for
        melee_root: Path to melee project root

    Returns:
        Relative path to header file, or None if not found
    """
    import subprocess

    try:
        # Use grep to find the function declaration in headers
        result = subprocess.run(
            ["grep", "-rln", f"{function_name}(", str(melee_root / "src")],
            capture_output=True,
            text=True,
            timeout=10,
        )

        for line in result.stdout.strip().split('\n'):
            if line.endswith('.h'):
                # Return relative path from src/
                if "/src/" in line:
                    return line.split("/src/", 1)[1]
                return line

    except (subprocess.TimeoutExpired, subprocess.SubprocessError):
        pass

    return None


def get_header_line_number(header_path: Path, function_name: str) -> Optional[int]:
    """Get the line number where a function is declared in a header.

    Args:
        header_path: Path to header file
        function_name: Function to find

    Returns:
        Line number (1-indexed) or None
    """
    if not header_path.exists():
        return None

    try:
        content = header_path.read_text()
        lines = content.split('\n')

        for i, line in enumerate(lines, 1):
            if function_name in line and '(' in line and ';' in line:
                return i
    except Exception:
        pass

    return None


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


def analyze_commit_error(
    error_output: str,
    file_path: str,
    melee_root: Optional[Path] = None,
    function_name: Optional[str] = None,
    source_code: Optional[str] = None,
) -> str:
    """Provide comprehensive analysis of a commit compilation error.

    Args:
        error_output: Raw compiler error output
        file_path: Path to the source file being compiled
        melee_root: Path to melee project root (for header lookups)
        function_name: Name of function being committed (for signature checks)
        source_code: The source code being committed (for signature extraction)

    Returns:
        Formatted diagnostic message with suggestions
    """
    import tempfile

    lines = []

    # Write full error to a temp file for agent access
    error_file = Path(tempfile.gettempdir()) / "decomp_compile_error.txt"
    try:
        error_file.write_text(error_output)
    except Exception:
        pass  # Don't fail if we can't write the file

    # Parse structured errors
    parsed_errors = parse_mwcc_errors(error_output)

    # Check for linker errors
    linker_symbols = extract_linker_errors(error_output)

    # Display structured errors with file:line info
    if parsed_errors:
        lines.append("[bold red]Compilation errors:[/bold red]")
        for err in parsed_errors[:5]:  # Show first 5 errors
            if err.file_path and err.line_number:
                lines.append(f"\n  [cyan]{err.file_path}:{err.line_number}[/cyan]")
            if err.context_line:
                lines.append(f"    [dim]{err.context_line}[/dim]")
            lines.append(f"    [red]{err.message}[/red]")

        if len(parsed_errors) > 5:
            lines.append(f"\n  [dim]... and {len(parsed_errors) - 5} more errors[/dim]")
    else:
        # Fallback to raw extraction if structured parsing fails
        output_lines = error_output.split('\n')
        error_lines = []
        for i, line in enumerate(output_lines):
            if 'error:' in line.lower() or 'Error:' in line:
                error_lines.append(line)
                for j in range(i + 1, min(i + 3, len(output_lines))):
                    next_line = output_lines[j]
                    if not next_line.strip() or '#   Error:' in next_line or \
                       '#   File:' in next_line or next_line.startswith('---'):
                        break
                    error_lines.append(next_line)

        if error_lines:
            lines.append("[bold red]Compilation errors:[/bold red]")
            for line in error_lines[:10]:
                lines.append(f"  {line.strip()}")
            if len(error_lines) > 10:
                lines.append(f"  [dim]... ({len(error_lines) - 10} more lines)[/dim]")

    # Check for linker errors (undefined symbols)
    if linker_symbols:
        lines.append("\n[bold red]Linker errors - undefined symbols:[/bold red]")
        for symbol in linker_symbols[:10]:
            lines.append(f"  [yellow]{symbol}[/yellow]")
            # Try to find where the symbol should be declared
            if melee_root:
                header = find_header_for_function(symbol, melee_root)
                if header:
                    lines.append(f"    [dim]Declared in: {header}[/dim]")
                else:
                    lines.append(f"    [dim]No declaration found - may need to add to header[/dim]")

        if len(linker_symbols) > 10:
            lines.append(f"  [dim]... and {len(linker_symbols) - 10} more[/dim]")

    # Check for header signature mismatch if we have the context
    if melee_root and function_name and source_code:
        sig_check = check_header_sync(source_code, function_name, melee_root, file_path)
        if sig_check and not sig_check.get("match"):
            lines.append("\n[bold yellow]Header signature mismatch detected:[/bold yellow]")
            lines.append(f"  [dim]Header:[/dim]         {sig_check.get('header', 'unknown')}")
            lines.append(f"  [dim]Implementation:[/dim] {sig_check.get('scratch', 'unknown')}")

            # Get line number in header
            header_path = sig_check.get("header_path")
            if header_path:
                line_num = get_header_line_number(Path(header_path), function_name)
                if line_num:
                    lines.append(f"\n  [cyan]Suggested fix:[/cyan] Update header at {header_path}:{line_num}")
                else:
                    lines.append(f"\n  [cyan]Suggested fix:[/cyan] Update header at {header_path}")

            if sig_check.get("issues"):
                lines.append("\n  [yellow]Issues:[/yellow]")
                for issue in sig_check["issues"]:
                    lines.append(f"    - {issue}")

    # Get undefined identifier suggestions
    suggestions = suggest_includes(error_output)
    conflicts = extract_conflicting_functions(error_output)

    if suggestions:
        lines.append("\n[bold cyan]Missing includes - suggested fixes:[/bold cyan]")
        # Group by header
        by_header: dict[str, list[str]] = {}
        for type_name, header in suggestions:
            if header not in by_header:
                by_header[header] = []
            by_header[header].append(type_name)

        for header, types in by_header.items():
            types_str = ", ".join(types)
            lines.append(f"  [green]#include {header}[/green]")
            lines.append(f"    [dim]Provides: {types_str}[/dim]")

    if conflicts:
        lines.append("\n[bold cyan]Signature conflicts:[/bold cyan]")
        for func in conflicts:
            lines.append(f"  [yellow]{func}()[/yellow] - signature mismatch between header and implementation")
            if melee_root:
                header = find_header_for_function(func, melee_root)
                if header:
                    header_path = melee_root / "src" / header
                    line_num = get_header_line_number(header_path, func)
                    if line_num:
                        lines.append(f"    [dim]Check: {header}:{line_num}[/dim]")
                    else:
                        lines.append(f"    [dim]Check: {header}[/dim]")

    # Show full output location
    lines.append(f"\n[dim]Full error output: {error_file}[/dim]")

    if not suggestions and not conflicts and not parsed_errors and not linker_symbols:
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


def format_signature_mismatch(comparison: dict, function_name: Optional[str] = None) -> str:
    """Format a signature mismatch as a helpful message.

    Args:
        comparison: Result from compare_signatures() or check_header_sync()
        function_name: Function name for line number lookup (optional)

    Returns:
        Formatted message string with Rich markup
    """
    lines = ["\n[bold yellow]Header signature mismatch detected:[/bold yellow]"]
    lines.append(f"  [dim]Header:[/dim]         {comparison['header']}")
    lines.append(f"  [dim]Implementation:[/dim] {comparison['scratch']}")

    if comparison.get("issues"):
        lines.append("\n  [yellow]Issues:[/yellow]")
        for issue in comparison["issues"]:
            lines.append(f"    - {issue}")

    # Show header location with line number if possible
    header_path = comparison.get('header_path')
    if header_path:
        line_num = None
        if function_name:
            line_num = get_header_line_number(Path(header_path), function_name)

        if line_num:
            lines.append(f"\n  [cyan]Suggested fix:[/cyan] Update header at {header_path}:{line_num}")
        else:
            lines.append(f"\n  [cyan]Suggested fix:[/cyan] Update header at {header_path}")
    else:
        lines.append("\n  [yellow]Consider updating the header declaration to match.[/yellow]")

    return "\n".join(lines)


# =============================================================================
# Caller Detection and Fixing
# =============================================================================

def find_callers(function_name: str, melee_root: Path) -> list[dict]:
    """Find all call sites for a function in the codebase.

    Args:
        function_name: Name of the function to find callers for
        melee_root: Path to melee project root

    Returns:
        List of dicts with: file, line, content, context
    """
    import subprocess

    callers = []
    src_dir = melee_root / "src" / "melee"

    if not src_dir.exists():
        return callers

    # Use grep to find call sites (function_name followed by '(')
    # Exclude the function definition itself
    try:
        result = subprocess.run(
            ["grep", "-rn", f"{function_name}(", str(src_dir)],
            capture_output=True,
            text=True,
            timeout=30,
        )

        for line in result.stdout.strip().split('\n'):
            if not line:
                continue

            # Parse grep output: file:line:content
            parts = line.split(':', 2)
            if len(parts) < 3:
                continue

            file_path, line_num, content = parts[0], parts[1], parts[2]

            # Skip the function definition itself
            if re.search(rf'^\s*(static\s+)?[\w\s\*]+\s+{re.escape(function_name)}\s*\([^)]*\)\s*\{{?\s*$', content):
                continue

            # Skip header declarations
            if file_path.endswith('.h'):
                continue

            # Skip comments
            if content.strip().startswith('//') or content.strip().startswith('/*'):
                continue

            callers.append({
                "file": file_path,
                "line": int(line_num),
                "content": content.strip(),
            })

    except (subprocess.TimeoutExpired, subprocess.SubprocessError):
        pass

    return callers


def check_callers_need_update(
    function_name: str,
    old_param_count: int,
    new_param_count: int,
    melee_root: Path
) -> list[dict]:
    """Check if callers need to be updated due to signature change.

    Args:
        function_name: Name of the function
        old_param_count: Number of parameters in old signature
        new_param_count: Number of parameters in new signature
        melee_root: Path to melee project root

    Returns:
        List of callers that likely need updates
    """
    if old_param_count >= new_param_count:
        # If params decreased or stayed same, callers might still work
        return []

    callers = find_callers(function_name, melee_root)
    needs_update = []

    for caller in callers:
        content = caller["content"]

        # Try to count arguments in the call
        # This is a simplified check - looks for function_name(...) and counts commas
        match = re.search(rf'{re.escape(function_name)}\s*\(([^)]*)\)', content)
        if match:
            args_str = match.group(1).strip()
            if args_str == "":
                arg_count = 0
            else:
                # Count arguments (simplified - doesn't handle nested parens perfectly)
                arg_count = args_str.count(',') + 1

            if arg_count < new_param_count:
                caller["current_args"] = arg_count
                caller["needed_args"] = new_param_count
                needs_update.append(caller)

    return needs_update


def format_caller_updates_needed(callers: list[dict], function_name: str) -> str:
    """Format a message about callers that need updating."""
    if not callers:
        return ""

    lines = [f"\n[bold yellow]Callers that need updating ({len(callers)} found):[/bold yellow]"]

    for caller in callers[:10]:  # Show max 10
        rel_path = caller["file"]
        if "/melee/" in rel_path:
            rel_path = rel_path.split("/melee/", 1)[1]

        lines.append(f"  [cyan]{rel_path}:{caller['line']}[/cyan]")
        lines.append(f"    [dim]{caller['content'][:80]}{'...' if len(caller['content']) > 80 else ''}[/dim]")

        if "current_args" in caller:
            lines.append(f"    [yellow]Has {caller['current_args']} args, needs {caller['needed_args']}[/yellow]")

    if len(callers) > 10:
        lines.append(f"\n  [dim]... and {len(callers) - 10} more callers[/dim]")

    lines.append("\n  [yellow]Fix these callers before committing, or the build will fail.[/yellow]")
    lines.append(f"  [dim]Search: grep -rn '{function_name}(' src/melee/[/dim]")

    return "\n".join(lines)


def get_header_fix_suggestion(comparison: dict) -> Optional[str]:
    """Generate exact fix for header file.

    Args:
        comparison: Result from compare_signatures()

    Returns:
        Formatted suggestion with exact replacement, or None
    """
    if comparison.get("match"):
        return None

    header_path = comparison.get("header_path", "unknown")
    old_sig = comparison.get("header", "")
    new_sig = comparison.get("scratch", "")

    if not old_sig or not new_sig:
        return None

    lines = ["\n[bold cyan]Suggested header fix:[/bold cyan]"]
    lines.append(f"  [dim]File: {header_path}[/dim]")
    lines.append(f"\n  [red]- {old_sig};[/red]")
    lines.append(f"  [green]+ {new_sig};[/green]")

    return "\n".join(lines)
