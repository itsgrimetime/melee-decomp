"""Update configure.py to change NonMatching -> Matching."""

import re
from pathlib import Path


async def update_configure_py(
    file_path: str,
    melee_root: Path
) -> bool:
    """Change NonMatching to Matching for this file in configure.py.

    Args:
        file_path: Relative path to the C file (e.g., "melee/lb/lbcommand.c")
        melee_root: Path to the melee project root

    Returns:
        True if successful, False otherwise
    """
    try:
        configure_path = melee_root / "configure.py"

        if not configure_path.exists():
            print(f"Error: configure.py not found at {configure_path}")
            return False

        # Read the current configure.py content
        content = configure_path.read_text(encoding='utf-8')

        # Create the pattern to find the NonMatching entry
        # Match: Object(NonMatching, "path/to/file.c")
        # We need to escape special regex characters in the file path
        escaped_path = re.escape(file_path)
        pattern = re.compile(
            rf'Object\(NonMatching,\s*["\']({escaped_path})["\']',
            re.MULTILINE
        )

        match = pattern.search(content)
        if not match:
            # Check if it's already marked as Matching
            matching_pattern = re.compile(
                rf'Object\(Matching,\s*["\']({escaped_path})["\']',
                re.MULTILINE
            )
            if matching_pattern.search(content):
                print(f"File '{file_path}' is already marked as Matching in configure.py")
                return True
            else:
                print(f"Error: File '{file_path}' not found in configure.py")
                return False

        # Replace NonMatching with Matching
        new_content = pattern.sub(r'Object(Matching, "\1"', content)

        # Write the updated content back
        configure_path.write_text(new_content, encoding='utf-8')

        print(f"Successfully changed '{file_path}' from NonMatching to Matching in configure.py")
        return True

    except Exception as e:
        print(f"Error updating configure.py: {e}")
        return False


async def get_file_path_from_function(
    function_name: str,
    melee_root: Path
) -> str | None:
    """Find the file path containing a specific function.

    This searches through the melee source directory to find which file
    contains the given function.

    Args:
        function_name: Name of the function to search for
        melee_root: Path to the melee project root

    Returns:
        Relative file path if found, None otherwise
    """
    try:
        src_dir = melee_root / "src"

        if not src_dir.exists():
            print(f"Error: Source directory not found at {src_dir}")
            return None

        # Search for the function in all C files
        # Use a simple grep-like search
        for c_file in src_dir.rglob("*.c"):
            try:
                content = c_file.read_text(encoding='utf-8')

                # Look for function definition
                # Pattern matches function definitions like:
                # - void FunctionName(
                # - static s32 FunctionName(
                pattern = re.compile(
                    rf'^\s*(?:static\s+)?(?:inline\s+)?[\w\*\s]+\s+{re.escape(function_name)}\s*\(',
                    re.MULTILINE
                )

                if pattern.search(content):
                    # Return path relative to src directory
                    rel_path = c_file.relative_to(src_dir)
                    return str(rel_path)

            except (UnicodeDecodeError, PermissionError):
                # Skip files that can't be read
                continue

        print(f"Function '{function_name}' not found in any source file")
        return None

    except Exception as e:
        print(f"Error searching for function: {e}")
        return None
