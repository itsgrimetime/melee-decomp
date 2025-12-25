"""
Read existing source code from the melee project.

This module provides utilities for extracting the current implementation
of a function from the project's source files.
"""

import re
from pathlib import Path
from typing import Optional


def extract_function_source(
    file_path: str,
    function_name: str,
    melee_root: Path,
    include_local_types: bool = True,
) -> Optional[str]:
    """Extract the source code of a function from the project.

    Args:
        file_path: Relative path to the source file (e.g., "melee/gr/grflatzone.c")
        function_name: Name of the function to extract
        melee_root: Path to the melee project root
        include_local_types: If True, also extract file-local struct/enum definitions
                           that the function might depend on

    Returns:
        The function source code, or None if not found
    """
    full_path = melee_root / "src" / file_path

    if not full_path.exists():
        return None

    try:
        content = full_path.read_text(encoding='utf-8')
    except Exception:
        return None

    # Find the function definition
    # Pattern matches function definitions with various return types
    func_pattern = re.compile(
        rf'^([^\n]*?)\s+{re.escape(function_name)}\s*\([^)]*\)[^{{]*\{{',
        re.MULTILINE
    )

    match = func_pattern.search(content)
    if not match:
        return None

    func_start = match.start()

    # Find the matching closing brace
    brace_count = 0
    func_end = None
    in_function = False

    for i in range(match.end() - 1, len(content)):
        if content[i] == '{':
            brace_count += 1
            in_function = True
        elif content[i] == '}':
            brace_count -= 1
            if in_function and brace_count == 0:
                func_end = i + 1
                break

    if func_end is None:
        return None

    function_code = content[func_start:func_end]

    if not include_local_types:
        return function_code

    # Extract file-local type definitions that appear before the function
    # Look for static struct/union definitions, typedefs, and file-scope variables
    # that the function might reference
    local_defs = []

    # Find static structs/unions with initializers (like "} varname;")
    # These are common in decomp projects for file-local data
    static_struct_pattern = re.compile(
        r'^(static\s+)?(?:struct|union)\s*\{[^}]+\}\s+\w+\s*;',
        re.MULTILINE | re.DOTALL
    )

    # Also match anonymous struct definitions with variable names
    anon_struct_pattern = re.compile(
        r'^(?:static\s+)?(?:struct|union)\s+\w*\s*\{[^}]+\}\s+\w+\s*(?:=\s*\{[^;]+\})?\s*;',
        re.MULTILINE | re.DOTALL
    )

    # Find all such definitions before the function
    content_before = content[:func_start]

    for pattern in [static_struct_pattern, anon_struct_pattern]:
        for m in pattern.finditer(content_before):
            # Only include if within reasonable distance (last 2000 chars before function)
            if func_start - m.end() < 2000:
                local_defs.append(m.group())

    # Also look for simple struct definitions that are used in the function
    # by scanning the function body for identifiers and checking if they're defined above
    identifiers_in_func = set(re.findall(r'\b([a-zA-Z_]\w+)\b', function_code))

    # Look for typedef struct or struct definitions
    typedef_pattern = re.compile(
        r'^typedef\s+struct\s*\{[^}]+\}\s+(\w+)\s*;',
        re.MULTILINE | re.DOTALL
    )

    for m in typedef_pattern.finditer(content_before):
        if m.group(1) in identifiers_in_func:
            local_defs.append(m.group())

    # Look for extern or file-scope variable declarations that are used
    var_pattern = re.compile(
        r'^(?:static\s+)?(?:struct\s+)?(?:\w+\s+)+(\w+)\s*(?:\[[^\]]*\])?\s*;',
        re.MULTILINE
    )

    if local_defs:
        # Deduplicate and combine local definitions with the function
        unique_defs = list(dict.fromkeys(local_defs))
        return '\n'.join(unique_defs) + '\n\n' + function_code

    return function_code


def get_surrounding_context(
    file_path: str,
    function_name: str,
    melee_root: Path,
    lines_before: int = 50,
    lines_after: int = 20,
) -> Optional[str]:
    """Get the code surrounding a function for context.

    This helps the LLM understand the coding patterns used in the file.

    Args:
        file_path: Relative path to the source file
        function_name: Name of the function
        melee_root: Path to the melee project root
        lines_before: Number of lines before the function to include
        lines_after: Number of lines after the function to include

    Returns:
        Surrounding code context, or None if not found
    """
    full_path = melee_root / "src" / file_path

    if not full_path.exists():
        return None

    try:
        content = full_path.read_text(encoding='utf-8')
        lines = content.split('\n')
    except Exception:
        return None

    # Find the line where the function starts
    func_line = None
    for i, line in enumerate(lines):
        if function_name in line and '(' in line:
            func_line = i
            break

    if func_line is None:
        return None

    # Extract surrounding context
    start_line = max(0, func_line - lines_before)

    # Find end of function
    end_line = func_line
    brace_count = 0
    found_start = False
    for i in range(func_line, len(lines)):
        line = lines[i]
        for char in line:
            if char == '{':
                brace_count += 1
                found_start = True
            elif char == '}':
                brace_count -= 1
                if found_start and brace_count == 0:
                    end_line = i
                    break
        if found_start and brace_count == 0:
            break

    end_line = min(len(lines), end_line + lines_after)

    return '\n'.join(lines[start_line:end_line])


def find_similar_functions(
    file_path: str,
    melee_root: Path,
    max_functions: int = 3,
) -> list[str]:
    """Find other functions in the same file that might be useful references.

    Args:
        file_path: Relative path to the source file
        melee_root: Path to the melee project root
        max_functions: Maximum number of functions to return

    Returns:
        List of function source code snippets
    """
    full_path = melee_root / "src" / file_path

    if not full_path.exists():
        return []

    try:
        content = full_path.read_text(encoding='utf-8')
    except Exception:
        return []

    # Find all function definitions
    func_pattern = re.compile(
        r'^([a-zA-Z_]\w*\s+)+(\w+)\s*\([^)]*\)\s*\{',
        re.MULTILINE
    )

    functions = []
    for match in func_pattern.finditer(content):
        func_name = match.group(2)
        func_start = match.start()

        # Find end of function
        brace_count = 0
        func_end = None
        in_function = False

        for i in range(match.end() - 1, len(content)):
            if content[i] == '{':
                brace_count += 1
                in_function = True
            elif content[i] == '}':
                brace_count -= 1
                if in_function and brace_count == 0:
                    func_end = i + 1
                    break

        if func_end:
            func_code = content[func_start:func_end]
            # Only include reasonably-sized functions
            if 50 < len(func_code) < 2000:
                functions.append(func_code)

        if len(functions) >= max_functions:
            break

    return functions
