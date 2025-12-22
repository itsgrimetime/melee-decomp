"""Update source files with matched code."""

import re
from pathlib import Path
from typing import Optional


async def update_source_file(
    file_path: str,
    function_name: str,
    new_code: str,
    melee_root: Path
) -> bool:
    """Replace function implementation in source file.

    Args:
        file_path: Relative path to the C file (e.g., "melee/lb/lbcommand.c")
        function_name: Name of the function to replace
        new_code: The new function implementation
        melee_root: Path to the melee project root

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

        # Extract the old function
        old_function = content[func_start:func_end]

        # Ensure new_code is properly formatted and doesn't have extra whitespace
        new_code = new_code.strip()

        # Replace the function
        new_content = content[:func_start] + new_code + content[func_end:]

        # Write the updated content back
        full_path.write_text(new_content, encoding='utf-8')

        print(f"Successfully updated function '{function_name}' in {file_path}")
        return True

    except Exception as e:
        print(f"Error updating source file: {e}")
        return False


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
        scratches_path = melee_root / "config" / "GALE01" / "scratches.txt"

        if not scratches_path.exists():
            print(f"Error: scratches.txt not found at {scratches_path}")
            return False

        # Create the new entry
        # Format: FunctionName = 100%:MATCHED; // author:agent id:XXXXX
        new_entry = f"{function_name} = 100%:MATCHED; // author:{author} id:{scratch_id}\n"

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
