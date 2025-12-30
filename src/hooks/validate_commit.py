#!/usr/bin/env python3
"""Pre-commit validation hook for melee decompilation commits.

Validates:
1. Implicit function declarations (like CI's Issues check)
2. symbols.txt is updated if function names changed
3. CONTRIBUTING.md coding guidelines are followed
4. clang-format has been run on C files
5. No merge conflict markers in staged files
6. Header signatures match implementations
7. No local scratch URLs in commit messages (must use production URLs)

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

# Local decomp.me server URL patterns
LOCAL_URL_PATTERNS = [
    r'https?://nzxt-discord\.local[:/]',
    r'https?://10\.200\.0\.1[:/]',
    r'https?://localhost:8000[:/]',
    r'https?://127\.0\.0\.1:8000[:/]',
]
LOCAL_URL_REGEX = re.compile('|'.join(LOCAL_URL_PATTERNS))

# Pattern to extract slug from a decomp.me URL
SCRATCH_URL_PATTERN = re.compile(r'https?://[^/]+/scratch/([a-zA-Z0-9]+)')

# Project paths
PROJECT_ROOT = Path(__file__).parent.parent.parent
MELEE_ROOT = PROJECT_ROOT / "melee"


def get_slug_mapping() -> dict[str, str]:
    """Get mapping of local slugs to production slugs from database.

    Returns:
        Dict mapping local_slug -> production_slug
    """
    try:
        from src.db import get_db
        db = get_db()

        mapping = {}
        with db.connection() as conn:
            cursor = conn.execute(
                "SELECT local_slug, production_slug FROM sync_state"
            )
            for row in cursor.fetchall():
                mapping[row['local_slug']] = row['production_slug']
        return mapping
    except Exception:
        return {}


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
                    self.errors.append(ValidationError(
                        f"New functions need symbols.txt update: {missing[:5]}",
                        str(SYMBOLS_FILE)
                    ))

    def validate_coding_style(self) -> None:
        """Check CONTRIBUTING.md coding guidelines and doldecomp/melee PR review standards."""
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

                    # Skip if line is a comment
                    stripped = content.strip()
                    if stripped.startswith("//") or stripped.startswith("/*") or stripped.startswith("*"):
                        continue

                    # Check for TRUE/FALSE instead of true/false
                    # PR feedback: "Use `true` and `false`, not `TRUE` and `FALSE`"
                    if re.search(r'\bTRUE\b', content):
                        self.errors.append(ValidationError(
                            "Use 'true' not 'TRUE' (lowercase boolean literals required)",
                            c_file, line_num
                        ))
                    if re.search(r'\bFALSE\b', content):
                        self.errors.append(ValidationError(
                            "Use 'false' not 'FALSE' (lowercase boolean literals required)",
                            c_file, line_num
                        ))

                    # Check for floating point literals without F suffix
                    # Pattern: number with decimal but no F/L suffix
                    float_matches = re.findall(r'\b\d+\.\d+(?![FLfl\w])\b', content)
                    for fm in float_matches:
                        # Skip if in a comment
                        if "//" in content:
                            comment_start = content.index("//")
                            if content.index(fm) > comment_start:
                                continue
                        self.errors.append(ValidationError(
                            f"Float literal '{fm}' missing F suffix (use {fm}F for f32)",
                            c_file, line_num
                        ))

                    # Check for lowercase hex (0xabc instead of 0xABC)
                    hex_matches = re.findall(r'0x[0-9a-fA-F]+', content)
                    for hm in hex_matches:
                        # Only flag if there are lowercase letters in hex digits
                        hex_part = hm[2:]  # Remove 0x prefix
                        if any(c.islower() and c.isalpha() for c in hex_part):
                            self.errors.append(ValidationError(
                                f"Hex literal '{hm}' should use uppercase (e.g., 0x{hex_part.upper()})",
                                c_file, line_num
                            ))
                            break  # Only report once per line

                    # Check for raw struct pointer arithmetic (PR feedback)
                    # Pattern: *(type*)((u8*)ptr + offset) or similar
                    if re.search(r'\*\s*\([^)]+\*\)\s*\(\s*\([^)]+\*\)\s*\w+\s*\+', content):
                        self.errors.append(ValidationError(
                            "Raw pointer arithmetic for struct access - use M2C_FIELD or fill in struct fields",
                            c_file, line_num
                        ))

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
                self.errors.append(ValidationError(
                    "clang-format would make changes - run 'git clang-format' before committing",
                    fixable=True
                ))
        except FileNotFoundError:
            # git-clang-format not installed
            pass
        except subprocess.CalledProcessError:
            pass

    def validate_forbidden_files(self) -> None:
        """Check for files that should never be modified.

        PR feedback: Some files like orig/GALE01/sys/.gitkeep should never
        be touched - they're placeholders for the build system.
        """
        forbidden_patterns = [
            "orig/GALE01/sys/.gitkeep",
            "orig/GALE01/asm/.gitkeep",
            "orig/GALE01/bin/.gitkeep",
            ".gitkeep",  # Generally .gitkeep files shouldn't be modified
        ]

        staged_files = self._get_staged_files()
        for f in staged_files:
            for pattern in forbidden_patterns:
                if f.endswith(pattern):
                    self.errors.append(ValidationError(
                        f"File should not be modified: {f} - revert this change",
                        f
                    ))
                    break

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

    def validate_extern_declarations(self) -> None:
        """Check for unnecessary file-scope extern declarations.

        PR feedback: extern declarations should be avoided - prefer including
        headers or creating them. Raw extern declarations are hard to maintain.
        """
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
                if line.startswith("@@"):
                    match = re.search(r'\+(\d+)', line)
                    if match:
                        line_num = int(match.group(1)) - 1
                    continue

                if line.startswith("+") and not line.startswith("+++"):
                    line_num += 1
                    content = line[1:].strip()

                    # Check for new extern declarations at file scope
                    # Pattern: extern Type symbol_name; (not in function)
                    if re.match(r'^extern\s+(?:static\s+)?\w+[\w\s\*]*\s+\w+\s*[;\[]', content):
                        # Skip if it's a function declaration (has parentheses)
                        if '(' not in content:
                            self.errors.append(ValidationError(
                                "New extern declaration - include proper header instead",
                                c_file, line_num
                            ))

                elif not line.startswith("-"):
                    line_num += 1

    def validate_symbol_renames(self) -> None:
        """Check for suspicious symbol renames (descriptive name -> address name).

        PR feedback: Don't rename descriptive symbols like `ItemStateTable_GShell`
        to address-based names like `it_803F5BA8`.
        """
        staged_files = self._get_staged_files()

        # Check for symbol renames in both C files and headers
        code_files = [f for f in staged_files if f.endswith((".c", ".h")) and "melee/" in f]

        if not code_files:
            return

        for code_file in code_files:
            diff = self._get_staged_diff(code_file)
            if not diff:
                continue

            # Look for lines where a descriptive name is removed and address name added
            removed_names = set()
            added_names = set()

            for line in diff.split("\n"):
                if line.startswith("-") and not line.startswith("---"):
                    # Look for symbol names being removed
                    names = re.findall(r'\b([A-Z][a-zA-Z0-9_]*(?:Table|State|Data|Info|List|Array)[a-zA-Z0-9_]*)\b', line)
                    removed_names.update(names)

                elif line.startswith("+") and not line.startswith("+++"):
                    # Look for address-based names being added
                    names = re.findall(r'\b((?:fn|it|ft|gr|lb|gm|if|mp|vi)_[0-9A-Fa-f]{8})\b', line)
                    added_names.update(names)

            # If we removed descriptive names and added address names, error
            if removed_names and added_names:
                self.errors.append(ValidationError(
                    f"Bad rename: removed descriptive names {list(removed_names)[:3]}, "
                    f"added address-based names {list(added_names)[:3]} - keep descriptive names",
                    code_file
                ))

    def validate_local_urls_in_commits(self) -> None:
        """Check for local decomp.me URLs in pending commit messages.

        PR feedback: Commit messages should use production decomp.me URLs,
        not local server URLs like nzxt-discord.local or 10.200.0.1.

        This check only runs when there are staged melee submodule changes,
        indicating we're about to commit decompilation work.
        """
        # Only check if we're staging melee submodule changes
        staged_files = self._get_staged_files()
        melee_changes = [f for f in staged_files if f.startswith("melee/")]
        if not melee_changes:
            return

        # Get all pending commits in melee submodule (not yet pushed to upstream)
        if not self.melee_root.exists():
            return

        try:
            result = subprocess.run(
                ["git", "log", "--oneline", "upstream/master..HEAD", "--format=%H %s"],
                capture_output=True, text=True, check=True,
                cwd=self.melee_root
            )
            commits = result.stdout.strip().split("\n") if result.stdout.strip() else []
        except subprocess.CalledProcessError:
            return

        if not commits:
            return

        # Get local->production slug mapping
        slug_mapping = get_slug_mapping()

        for commit_line in commits:
            if not commit_line.strip():
                continue

            parts = commit_line.split(" ", 1)
            if len(parts) < 2:
                continue

            commit_hash = parts[0][:8]
            commit_subject = parts[1]

            # Get full commit message
            try:
                result = subprocess.run(
                    ["git", "log", "-1", "--format=%B", parts[0]],
                    capture_output=True, text=True, check=True,
                    cwd=self.melee_root
                )
                full_message = result.stdout
            except subprocess.CalledProcessError:
                full_message = commit_subject

            # Check for local URLs
            if LOCAL_URL_REGEX.search(full_message):
                # Find all scratch URLs and check if they have production mappings
                local_urls = SCRATCH_URL_PATTERN.findall(full_message)
                unmapped_slugs = []

                for slug in local_urls:
                    if slug not in slug_mapping:
                        unmapped_slugs.append(slug)

                if unmapped_slugs:
                    self.errors.append(ValidationError(
                        f"Commit {commit_hash} has local scratch URL(s) without production mapping: {unmapped_slugs}. "
                        f"Run 'melee-agent sync production' first to sync scratches.",
                    ))
                else:
                    # Has mapping but URL not replaced
                    self.errors.append(ValidationError(
                        f"Commit {commit_hash} has local scratch URL(s) - amend to use production URLs. "
                        f"Local slugs found: {local_urls}",
                    ))

    def run(self) -> tuple[list[ValidationError], list[ValidationError]]:
        """Run all validations."""
        self.validate_forbidden_files()
        self.validate_conflict_markers()
        self.validate_header_signatures()
        self.validate_implicit_declarations()
        self.validate_symbols_txt()
        self.validate_coding_style()
        self.validate_extern_declarations()
        self.validate_symbol_renames()
        self.validate_local_urls_in_commits()
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
