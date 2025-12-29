#!/usr/bin/env python3
"""Pre-commit validation hook for melee decompilation commits.

Validates:
1. Implicit function declarations (like CI's Issues check)
2. symbols.txt is updated if function names changed
3. CONTRIBUTING.md coding guidelines are followed
4. clang-format has been run on C files
5. No merge conflict markers in staged files
6. Header signatures match implementations

Usage:
    python -m src.hooks.validate_commit [--fix]
"""

import argparse
import json
import re
import subprocess
import sys
from pathlib import Path
from typing import Optional

# Project paths
PROJECT_ROOT = Path(__file__).parent.parent.parent
MELEE_ROOT = PROJECT_ROOT / "melee"
SYMBOLS_FILE = MELEE_ROOT / "config" / "GALE01" / "symbols.txt"
COMPILE_COMMANDS = MELEE_ROOT / "compile_commands.json"


class ValidationError:
    """A validation error or warning."""

    def __init__(self, message: str, file: Optional[str] = None, line: Optional[int] = None, fixable: bool = False):
        self.message = message
        self.file = file
        self.line = line
        self.fixable = fixable

    def __str__(self):
        parts = []
        if self.file:
            parts.append(self.file)
            if self.line:
                parts.append(f":{self.line}")
            parts.append(": ")
        parts.append(self.message)
        return "".join(parts)


class CommitValidator:
    """Validates a commit against project guidelines."""

    def __init__(self, melee_root: Path = MELEE_ROOT):
        self.melee_root = melee_root
        self.errors: list[ValidationError] = []
        self.warnings: list[ValidationError] = []

    def _get_staged_files(self) -> list[str]:
        """Get list of staged files."""
        try:
            result = subprocess.run(
                ["git", "diff", "--cached", "--name-only"],
                capture_output=True, text=True, check=True,
                cwd=PROJECT_ROOT
            )
            return result.stdout.strip().split("\n") if result.stdout.strip() else []
        except subprocess.CalledProcessError:
            return []

    def _get_staged_diff(self, file_path: str) -> str:
        """Get the staged diff for a file."""
        try:
            result = subprocess.run(
                ["git", "diff", "--cached", file_path],
                capture_output=True, text=True, check=True,
                cwd=PROJECT_ROOT
            )
            return result.stdout
        except subprocess.CalledProcessError:
            return ""

    def _load_compile_commands(self) -> dict[str, list[str]]:
        """Load compile_commands.json and return file -> args mapping."""
        if not COMPILE_COMMANDS.exists():
            return {}

        try:
            with open(COMPILE_COMMANDS) as f:
                commands = json.load(f)

            # Build mapping from file path to compiler arguments
            file_args = {}
            for entry in commands:
                file_path = entry.get("file", "")
                args = entry.get("arguments", [])
                if file_path and args:
                    # Normalize to relative path from melee root
                    if file_path.startswith(str(MELEE_ROOT)):
                        rel_path = file_path[len(str(MELEE_ROOT)) + 1:]
                        file_args[rel_path] = args
            return file_args
        except (json.JSONDecodeError, IOError):
            return {}

    def validate_implicit_declarations(self) -> None:
        """Check for implicit function declarations using clang.

        This mirrors the CI 'Issues' check that catches missing includes.
        """
        staged_files = self._get_staged_files()

        # Handle both parent repo (melee/src/...) and submodule (src/...) contexts
        c_files = []
        for f in staged_files:
            if f.endswith(".c"):
                if f.startswith("melee/src/"):
                    c_files.append(f)
                elif f.startswith("src/melee/"):
                    # Running from within melee submodule
                    c_files.append("melee/" + f)

        # Also check melee submodule for changes if not already found
        if not c_files:
            try:
                result = subprocess.run(
                    ["git", "diff", "--cached", "--name-only"],
                    capture_output=True, text=True, check=True,
                    cwd=MELEE_ROOT
                )
                submodule_files = result.stdout.strip().split("\n") if result.stdout.strip() else []
                for f in submodule_files:
                    if f.startswith("src/melee/") and f.endswith(".c"):
                        c_files.append("melee/" + f)
            except subprocess.CalledProcessError:
                pass

        if not c_files:
            return

        # Check if clang is available
        try:
            subprocess.run(["clang", "--version"], capture_output=True, check=True)
        except (FileNotFoundError, subprocess.CalledProcessError):
            self.warnings.append(ValidationError(
                "clang not found - skipping implicit declaration check"
            ))
            return

        # Load compile commands for proper include paths
        compile_commands = self._load_compile_commands()
        if not compile_commands:
            self.warnings.append(ValidationError(
                "compile_commands.json not found - run 'ninja' in melee/ first"
            ))
            return

        for c_file in c_files:
            # Get the path relative to melee/ for display, and src/ path for compile_commands
            if c_file.startswith("melee/"):
                display_path = c_file
                # compile_commands uses paths like "src/melee/ft/ftcoll.c"
                src_path = c_file[6:]  # Remove "melee/" prefix -> "src/melee/ft/ftcoll.c"
            else:
                display_path = c_file
                src_path = c_file

            # Find matching compile command
            args = compile_commands.get(src_path)
            if not args:
                continue

            # Build clang command for syntax check only
            # Filter to just include paths and defines, add our warning flag
            clang_args = ["clang", "-fsyntax-only", "-Werror=implicit-function-declaration"]

            for arg in args[1:]:  # Skip the compiler name
                if arg.startswith("-I") or arg.startswith("-D") or arg.startswith("-nostdinc"):
                    clang_args.append(arg)
                elif arg.startswith("--target="):
                    clang_args.append(arg)
                elif arg == "-fno-builtin":
                    clang_args.append(arg)

            # Add the source file
            clang_args.append(str(MELEE_ROOT / src_path))

            # Run clang
            result = subprocess.run(
                clang_args,
                capture_output=True,
                text=True,
                cwd=MELEE_ROOT
            )

            # Parse errors from stderr
            if result.returncode != 0:
                for line in result.stderr.split("\n"):
                    # Match both old and new clang message formats
                    if "implicit declaration of function" in line or "call to undeclared function" in line:
                        # Extract file:line: message
                        match = re.match(r"([^:]+):(\d+):\d+: (?:error|warning): (.+)", line)
                        if match:
                            file_path = match.group(1)
                            line_num = int(match.group(2))
                            message = match.group(3)
                            # Make path relative
                            if str(MELEE_ROOT) in file_path:
                                file_path = "melee/" + file_path.split(str(MELEE_ROOT) + "/")[1]
                            self.errors.append(ValidationError(
                                message,
                                file_path,
                                line_num
                            ))

    def validate_symbols_txt(self) -> None:
        """Check if symbols.txt needs updating for new function names."""
        staged_files = self._get_staged_files()

        # Look for modified C files in melee/src/
        c_files = [f for f in staged_files if f.startswith("melee/src/") and f.endswith(".c")]
        if not c_files:
            return

        # Check if symbols.txt was also modified
        symbols_modified = "melee/config/GALE01/symbols.txt" in staged_files

        # Get function names from staged C file changes
        new_functions = []
        for c_file in c_files:
            diff = self._get_staged_diff(c_file)
            # Look for new function definitions (very rough heuristic)
            for line in diff.split("\n"):
                if line.startswith("+") and not line.startswith("+++"):
                    # Pattern for function definition start
                    if re.search(r'^[\+]\s*(?:static\s+)?(?:inline\s+)?(?:\w+\s+)+(\w+)\s*\([^)]*\)\s*\{?', line):
                        func_match = re.search(r'(\w+)\s*\([^)]*\)', line)
                        if func_match:
                            func_name = func_match.group(1)
                            # Skip common non-function matches
                            if func_name not in ("if", "while", "for", "switch", "return", "sizeof"):
                                new_functions.append(func_name)

        if new_functions and not symbols_modified:
            # Check if these functions are already in symbols.txt
            if SYMBOLS_FILE.exists():
                symbols_content = SYMBOLS_FILE.read_text()
                missing = [f for f in new_functions if f not in symbols_content]
                if missing:
                    self.warnings.append(ValidationError(
                        f"New functions may need symbols.txt update: {missing[:5]}",
                        str(SYMBOLS_FILE)
                    ))

    def validate_coding_style(self) -> None:
        """Check CONTRIBUTING.md coding guidelines."""
        staged_files = self._get_staged_files()
        c_files = [f for f in staged_files if f.startswith("melee/src/") and f.endswith(".c")]

        if not c_files:
            return

        for c_file in c_files:
            diff = self._get_staged_diff(c_file)
            if not diff:
                continue

            line_num = 0
            for line in diff.split("\n"):
                # Track line numbers in the new file
                if line.startswith("@@"):
                    match = re.search(r'\+(\d+)', line)
                    if match:
                        line_num = int(match.group(1)) - 1
                    continue

                if line.startswith("+") and not line.startswith("+++"):
                    line_num += 1
                    content = line[1:]  # Remove + prefix

                    # Check for implicit NULL checks
                    # Pattern: if (!ptr) or if (ptr) without explicit NULL
                    if re.search(r'\bif\s*\(\s*!\s*\w+\s*\)', content):
                        # Could be a bool, so just warn
                        pass  # Skip - too many false positives

                    # Check for floating point literals without F suffix
                    # Pattern: number with decimal but no F/L suffix
                    float_matches = re.findall(r'\b\d+\.\d+(?![FLfl])\b', content)
                    for fm in float_matches:
                        # Skip if in a comment
                        if "//" in content and content.index("//") < content.index(fm):
                            continue
                        self.warnings.append(ValidationError(
                            f"Float literal '{fm}' missing F suffix (use {fm}F for f32)",
                            c_file, line_num
                        ))

                    # Check for lowercase hex (0xabc instead of 0xABC)
                    hex_matches = re.findall(r'0x[0-9a-f]+', content.lower())
                    for hm in hex_matches:
                        if any(c.islower() for c in hm[2:] if c.isalpha()):
                            actual = re.search(r'0[xX][0-9a-fA-F]+', content)
                            if actual and any(c.islower() for c in actual.group()[2:] if c.isalpha()):
                                self.warnings.append(ValidationError(
                                    f"Hex literal should use uppercase (0xABC not 0xabc)",
                                    c_file, line_num
                                ))
                                break

                elif not line.startswith("-"):
                    line_num += 1

    def validate_clang_format(self) -> None:
        """Check if clang-format was run on staged C files."""
        staged_files = self._get_staged_files()
        c_files = [f for f in staged_files if f.startswith("melee/src/") and f.endswith(".c")]

        if not c_files:
            return

        # Check if git clang-format would make changes
        try:
            result = subprocess.run(
                ["git", "clang-format", "--diff"],
                capture_output=True, text=True,
                cwd=PROJECT_ROOT
            )
            if result.stdout.strip() and "no modified files" not in result.stdout.lower():
                self.warnings.append(ValidationError(
                    "clang-format would make changes - run 'git clang-format' before committing",
                    fixable=True
                ))
        except FileNotFoundError:
            # git-clang-format not installed
            pass
        except subprocess.CalledProcessError:
            pass

    def validate_conflict_markers(self) -> None:
        """Check for merge conflict markers in staged files."""
        staged_files = self._get_staged_files()

        # Check C and header files
        code_files = [f for f in staged_files if f.endswith((".c", ".h"))]

        for code_file in code_files:
            # Get the staged content
            try:
                result = subprocess.run(
                    ["git", "show", f":{code_file}"],
                    capture_output=True, text=True, check=True,
                    cwd=PROJECT_ROOT
                )
                content = result.stdout
            except subprocess.CalledProcessError:
                continue

            # Check for conflict markers
            markers = ["<<<<<<<", "=======", ">>>>>>>"]
            for i, line in enumerate(content.split("\n"), 1):
                for marker in markers:
                    if line.strip().startswith(marker):
                        self.errors.append(ValidationError(
                            f"Merge conflict marker found: {marker}",
                            code_file, i
                        ))

    def validate_header_signatures(self) -> None:
        """Check that header declarations match implementations.

        Detects when a header has UNK_RET/UNK_PARAMS but the implementation
        has a concrete signature, which causes CI failures with -requireprotos.
        """
        staged_files = self._get_staged_files()

        # Find staged C files
        c_files = [f for f in staged_files if f.endswith(".c") and "melee/src/" in f]

        if not c_files:
            return

        for c_file in c_files:
            # Get the staged content
            try:
                result = subprocess.run(
                    ["git", "show", f":{c_file}"],
                    capture_output=True, text=True, check=True,
                    cwd=PROJECT_ROOT
                )
                c_content = result.stdout
            except subprocess.CalledProcessError:
                continue

            # Find function implementations (non-static, at start of line)
            # Pattern: type name(params) { or type name(params)\n{
            func_pattern = re.compile(
                r'^(?!static\s)(\w+(?:\s*\*)?)\s+(\w+)\s*\(([^)]*)\)\s*(?:\{|$)',
                re.MULTILINE
            )

            implementations = {}
            for match in func_pattern.finditer(c_content):
                return_type = match.group(1).strip()
                func_name = match.group(2)
                params = match.group(3).strip()

                # Skip main and other special functions
                if func_name in ("main", "if", "while", "for", "switch"):
                    continue

                implementations[func_name] = {
                    "return_type": return_type,
                    "params": params
                }

            if not implementations:
                continue

            # Find the corresponding header file
            header_file = c_file.replace(".c", ".h")

            # Get header content (try staged first, then working tree)
            header_content = None
            try:
                result = subprocess.run(
                    ["git", "show", f":{header_file}"],
                    capture_output=True, text=True, check=True,
                    cwd=PROJECT_ROOT
                )
                header_content = result.stdout
            except subprocess.CalledProcessError:
                # Try reading from working tree
                header_path = PROJECT_ROOT / header_file
                if header_path.exists():
                    header_content = header_path.read_text()

            if not header_content:
                continue

            # Check each implementation against header declaration
            for func_name, impl in implementations.items():
                # Look for the function in the header
                # Pattern: matches declarations like "/* addr */ UNK_RET name(UNK_PARAMS);"
                decl_pattern = re.compile(
                    rf'(/\*[^*]*\*/\s*)?(UNK_RET|{re.escape(impl["return_type"])})\s+{re.escape(func_name)}\s*\(([^)]*)\)\s*;',
                    re.MULTILINE
                )

                match = decl_pattern.search(header_content)
                if match:
                    header_return = match.group(2)
                    header_params = match.group(3).strip()

                    # Check for UNK_RET/UNK_PARAMS mismatch
                    has_unk = "UNK_RET" in header_return or "UNK_PARAMS" in header_params
                    impl_has_concrete = impl["return_type"] != "UNK_RET" and impl["params"] != "UNK_PARAMS"

                    if has_unk and impl_has_concrete:
                        self.errors.append(ValidationError(
                            f"Header signature mismatch: {func_name} declared as "
                            f"'{header_return} {func_name}({header_params})' in header "
                            f"but implemented as '{impl['return_type']} {func_name}({impl['params']})'",
                            header_file
                        ))

    def run(self) -> tuple[list[ValidationError], list[ValidationError]]:
        """Run all validations."""
        self.validate_conflict_markers()
        self.validate_header_signatures()
        self.validate_implicit_declarations()
        self.validate_symbols_txt()
        self.validate_coding_style()
        self.validate_clang_format()
        return self.errors, self.warnings


def main():
    parser = argparse.ArgumentParser(description="Validate commit against project guidelines")
    parser.add_argument("--fix", action="store_true", help="Attempt to fix issues")
    parser.add_argument("--verbose", "-v", action="store_true", help="Show all warnings")
    args = parser.parse_args()

    validator = CommitValidator()
    errors, warnings = validator.run()

    # Print results
    if warnings and args.verbose:
        print("\n\033[33mWarnings:\033[0m")
        for w in warnings:
            print(f"  ⚠ {w}")

    if errors:
        print("\n\033[31mErrors (must fix before commit):\033[0m")
        for e in errors:
            print(f"  ✗ {e}")

        if args.fix:
            print("\n\033[36mAttempting fixes...\033[0m")
            # TODO: Implement auto-fixes
            print("  Auto-fix not yet implemented")

        print(f"\n\033[31mCommit blocked: {len(errors)} error(s)\033[0m")
        sys.exit(1)

    if warnings:
        print(f"\n\033[33m{len(warnings)} warning(s) - commit allowed\033[0m")
        if not args.verbose:
            print("  Run with --verbose to see details")

    print("\n\033[32m✓ Validation passed\033[0m")
    sys.exit(0)


if __name__ == "__main__":
    main()
