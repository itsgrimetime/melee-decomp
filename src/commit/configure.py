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


async def should_mark_as_matching(
    file_path: str,
    melee_root: Path
) -> tuple[bool, str]:
    """Check if a file should be marked as Matching in configure.py.

    A file should only transition from NonMatching to Matching when ALL
    functions in the file are 100% matched. This prevents breaking the build
    by switching a file to Matching mode when it still has unimplemented functions.

    Args:
        file_path: Relative path to the C file (e.g., "melee/lb/lbcommand.c")
        melee_root: Path to the melee project root

    Returns:
        Tuple of (should_mark, reason):
        - (True, "") if all functions are 100% matched
        - (False, reason) if not all functions are matched, with explanation
    """
    try:
        from .splits import SplitsParser
        from .symbols import SymbolParser
        from .report import ReportParser
    except ImportError:
        # Fall back to extractor module
        from src.extractor.splits import SplitsParser
        from src.extractor.symbols import SymbolParser
        from src.extractor.report import ReportParser

    try:
        # Get all symbols
        symbol_parser = SymbolParser(melee_root)
        symbols = symbol_parser.parse_symbols()

        # Get all functions in this file
        splits_parser = SplitsParser(melee_root)
        functions_in_file = splits_parser.get_functions_in_file(file_path, symbols)

        if not functions_in_file:
            return False, f"No functions found in {file_path}"

        # Get match percentages for all functions
        report_parser = ReportParser(melee_root)
        function_matches = report_parser.get_function_matches()

        # Check each function
        unmatched = []
        for func_name in functions_in_file:
            match_data = function_matches.get(func_name)
            if match_data is None:
                unmatched.append(f"{func_name} (no match data)")
            elif match_data.fuzzy_match_percent < 100.0:
                unmatched.append(f"{func_name} ({match_data.fuzzy_match_percent:.1f}%)")

        if unmatched:
            if len(unmatched) <= 3:
                reason = f"Not all functions matched: {', '.join(unmatched)}"
            else:
                reason = f"{len(unmatched)} functions not fully matched (e.g., {', '.join(unmatched[:3])}...)"
            return False, reason

        return True, ""

    except FileNotFoundError as e:
        # If we can't check, be conservative and don't mark as Matching
        return False, f"Could not verify: {e}"
    except Exception as e:
        return False, f"Error checking file status: {e}"


async def get_file_path_from_function(
    function_name: str,
    melee_root: Path
) -> str | None:
    """Find the file path containing a specific function.

    This searches through the melee source directory to find which file
    contains the given function (either as a definition or a stub marker).

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
                definition_pattern = re.compile(
                    rf'^\s*(?:static\s+)?(?:inline\s+)?[\w\*\s]+\s+{re.escape(function_name)}\s*\(',
                    re.MULTILINE
                )

                # Also look for stub markers like:
                # /// #FunctionName
                stub_pattern = re.compile(
                    rf'^///\s*#\s*{re.escape(function_name)}\s*$',
                    re.MULTILINE
                )

                if definition_pattern.search(content) or stub_pattern.search(content):
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
