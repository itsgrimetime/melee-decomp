"""Update source files with matched code."""

import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional, Tuple


class CodeValidationError(Exception):
    """Raised when code validation fails."""
    pass


def validate_function_code(code: str, function_name: str) -> Tuple[bool, str]:
    """Validate that code represents a complete, well-formed function.

    Checks:
    1. Braces are balanced
    2. Code contains the target function
    3. Code doesn't appear to be mid-statement (starts reasonably)
    4. Warns if multiple function definitions found

    Args:
        code: The code to validate
        function_name: Expected function name

    Returns:
        Tuple of (is_valid, error_or_warning_message)
    """
    code = code.strip()

    if not code:
        return False, "Code is empty"

    # Check for balanced braces
    open_braces = code.count('{')
    close_braces = code.count('}')
    if open_braces != close_braces:
        return False, f"Unbalanced braces: {open_braces} '{{' vs {close_braces} '}}'"

    # Check that target function is present
    func_pattern = re.compile(
        rf'\b{re.escape(function_name)}\s*\([^)]*\)\s*\{{',
        re.MULTILINE
    )
    if not func_pattern.search(code):
        return False, f"Function '{function_name}' not found in code"

    # Check for signs of mid-statement insertion
    # Code shouldn't start with operators, closing braces, or statements like 'case' or 'break'
    bad_starts = [
        (r'^\s*[+\-*/&|^%]=', "Code starts with assignment operator"),
        (r'^\s*\}', "Code starts with closing brace"),
        (r'^\s*case\s+', "Code starts with 'case' (mid-switch insertion)"),
        (r'^\s*break\s*;', "Code starts with 'break'"),
        (r'^\s*default\s*:', "Code starts with 'default' (mid-switch insertion)"),
        (r'^\s*else\s*[{\n]', "Code starts with 'else' (mid-if insertion)"),
        (r'^\s*\)', "Code starts with closing parenthesis"),
    ]

    for pattern, msg in bad_starts:
        if re.match(pattern, code):
            return False, msg

    # Count function definitions (rough heuristic)
    # Pattern: something that looks like "type name(params) {"
    func_def_pattern = re.compile(
        r'(?:^|\n)\s*(?:static\s+)?(?:inline\s+)?(?:\w+\s+)+\w+\s*\([^)]*\)\s*\{',
        re.MULTILINE
    )
    func_defs = func_def_pattern.findall(code)
    if len(func_defs) > 1:
        # This is a warning, not an error - could be intentional (helper functions)
        return True, f"Warning: Found {len(func_defs)} function definitions in code"

    return True, ""


def _extract_function_from_code(code: str, function_name: str) -> Optional[str]:
    """Extract just the target function from code that may contain helper definitions.

    Args:
        code: Full code that may contain struct definitions, forward declarations, etc.
        function_name: The function to extract

    Returns:
        The extracted function or None if not found
    """
    # Pattern to find the function definition
    # Match: return_type function_name(params) {
    func_pattern = re.compile(
        rf'^([^\n]*?)\s+{re.escape(function_name)}\s*\([^)]*\)[^{{]*\{{',
        re.MULTILINE
    )

    match = func_pattern.search(code)
    if not match:
        return None

    func_start = match.start()

    # Find the matching closing brace
    brace_count = 0
    func_end = None
    in_function = False

    for i in range(match.end() - 1, len(code)):
        if code[i] == '{':
            brace_count += 1
            in_function = True
        elif code[i] == '}':
            brace_count -= 1
            if in_function and brace_count == 0:
                func_end = i + 1
                break

    if func_end is None:
        return None

    return code[func_start:func_end]


async def update_source_file(
    file_path: str,
    function_name: str,
    new_code: str,
    melee_root: Path,
    extract_function_only: bool = False,
) -> bool:
    """Replace function implementation in source file.

    Args:
        file_path: Relative path to the C file (e.g., "melee/lb/lbcommand.c")
        function_name: Name of the function to replace
        new_code: The new function implementation (may include helper definitions)
        melee_root: Path to the melee project root
        extract_function_only: If True, extract just the target function from new_code,
            discarding any struct definitions or forward declarations. If False (default),
            the caller is responsible for providing exactly what should be inserted.
            Use False for agent-driven workflows where the agent has already decided
            what to include based on the target file context.

    Returns:
        True if successful, False otherwise
    """
    try:
        # Construct full path
        full_path = melee_root / "src" / file_path

        if not full_path.exists():
            print(f"Error: Source file not found: {full_path}")
            return False

        # Read the current file content
        content = full_path.read_text(encoding='utf-8')

        # Find the function to replace
        # Pattern matches function definitions with various return types and modifiers
        # Handles cases like:
        # - void FunctionName(args)
        # - static inline bool FunctionName(args)
        # - s32 FunctionName(args)
        function_pattern = re.compile(
            rf'^([^\n]*?)\s+{re.escape(function_name)}\s*\([^)]*\)[^{{]*\{{',
            re.MULTILINE
        )

        match = function_pattern.search(content)
        if not match:
            print(f"Error: Function '{function_name}' not found in {file_path}")
            return False

        # Find the start of the function
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
            print(f"Error: Could not find closing brace for function '{function_name}'")
            return False

        # Extract the old function (for reference/logging)
        old_function = content[func_start:func_end]

        # Process new_code based on extraction mode
        new_code = new_code.strip()

        if extract_function_only:
            # Extract just the target function, discarding helper definitions
            extracted_function = _extract_function_from_code(new_code, function_name)
            if extracted_function is None:
                print(f"Warning: Could not extract function '{function_name}' from new code, using as-is")
                extracted_function = new_code
            code_to_insert = extracted_function
        else:
            # Use the code as-is - caller is responsible for content
            # This mode is for agent-driven workflows where the agent has already
            # analyzed the target file and decided what to include
            code_to_insert = new_code

        # Validate the code before inserting
        is_valid, validation_msg = validate_function_code(code_to_insert, function_name)
        if not is_valid:
            print(f"Error: Code validation failed: {validation_msg}")
            return False
        if validation_msg:  # Warning case
            print(f"  {validation_msg}")

        # Replace the function
        new_content = content[:func_start] + code_to_insert + content[func_end:]

        # Write the updated content back
        full_path.write_text(new_content, encoding='utf-8')

        print(f"Successfully updated function '{function_name}' in {file_path}")
        return True

    except Exception as e:
        print(f"Error updating source file: {e}")
        return False


def validate_scratches_entry(function_name: str, scratch_id: str, author: str) -> tuple[bool, str]:
    """Validate inputs for a scratches.txt entry.

    Returns:
        Tuple of (is_valid, error_message). error_message is empty if valid.
    """
    # Function name: alphanumeric, underscore, no spaces
    if not re.match(r'^[a-zA-Z_][a-zA-Z0-9_]*$', function_name):
        return False, f"Invalid function name '{function_name}': must be a valid C identifier"

    # Scratch ID: 5 alphanumeric characters (decomp.me format)
    if not re.match(r'^[a-zA-Z0-9]{5}$', scratch_id):
        return False, f"Invalid scratch ID '{scratch_id}': must be exactly 5 alphanumeric characters"

    # Author: no spaces or special chars
    if not re.match(r'^[a-zA-Z0-9_-]+$', author):
        return False, f"Invalid author '{author}': must be alphanumeric with underscores/dashes"

    return True, ""


async def update_scratches_txt(
    function_name: str,
    scratch_id: str,
    melee_root: Path,
    author: str = "agent"
) -> bool:
    """Add/update entry in scratches.txt.

    Args:
        function_name: Name of the matched function
        scratch_id: The decomp.me scratch ID
        melee_root: Path to the melee project root
        author: Author name (default: "agent")

    Returns:
        True if successful, False otherwise
    """
    try:
        # Validate inputs first
        is_valid, error_msg = validate_scratches_entry(function_name, scratch_id, author)
        if not is_valid:
            print(f"Error: {error_msg}")
            return False

        scratches_path = melee_root / "config" / "GALE01" / "scratches.txt"

        if not scratches_path.exists():
            print(f"Error: scratches.txt not found at {scratches_path}")
            return False

        # Create the new entry with timestamps
        # Format: FunctionName = 100%:MATCHED; // author:agent id:XXXXX updated:TIMESTAMP created:TIMESTAMP
        now = datetime.now(timezone.utc).isoformat().replace('+00:00', 'Z')
        new_entry = f"{function_name} = 100%:MATCHED; // author:{author} id:{scratch_id} updated:{now} created:{now}\n"

        # Read existing content
        content = scratches_path.read_text(encoding='utf-8')

        # Check if an entry for this function already exists
        pattern = re.compile(rf'^{re.escape(function_name)}\s*=.*id:{re.escape(scratch_id)}', re.MULTILINE)

        if pattern.search(content):
            print(f"Entry for '{function_name}' with id '{scratch_id}' already exists in scratches.txt")
            return True

        # Append the new entry
        with open(scratches_path, 'a', encoding='utf-8') as f:
            f.write(new_entry)

        print(f"Successfully added entry for '{function_name}' to scratches.txt")
        return True

    except Exception as e:
        print(f"Error updating scratches.txt: {e}")
        return False
