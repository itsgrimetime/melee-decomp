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
import os
import re
import signal
import subprocess
import sys
from pathlib import Path
from typing import Optional


# Default timeout for validation (5 minutes)
DEFAULT_VALIDATION_TIMEOUT = 300


def _timeout_handler(signum, frame):
    """Signal handler for validation timeout."""
    print("\n\033[31m✗ Validation timed out\033[0m")
    print("  The pre-commit hook took too long to complete.")
    print("  This may indicate a hanging process or very slow build.")
    print("\n  Options:")
    print("    - Check for hung processes: ps aux | grep ninja")
    print("    - Increase timeout: --timeout 600")
    print("    - Skip slow checks: --skip-regressions")
    sys.exit(124)  # Standard timeout exit code

# Try to import tree-sitter based analyzer for better detection
try:
    from src.hooks.c_analyzer import (
        analyze_diff_additions,
        TREE_SITTER_AVAILABLE,
    )
except ImportError:
    TREE_SITTER_AVAILABLE = False
    analyze_diff_additions = None

# Local decomp.me server URL patterns (private subnets, loopback, .local domains)
LOCAL_URL_PATTERNS = [
    r'https?://[^/]*\.local[:/]',                              # .local domains
    r'https?://localhost[:/]',                                  # localhost
    r'https?://127\.\d{1,3}\.\d{1,3}\.\d{1,3}[:/]',            # 127.x.x.x (loopback)
    r'https?://10\.\d{1,3}\.\d{1,3}\.\d{1,3}[:/]',             # 10.x.x.x (Class A private)
    r'https?://172\.(1[6-9]|2\d|3[0-1])\.\d{1,3}\.\d{1,3}[:/]', # 172.16-31.x.x (Class B private)
    r'https?://192\.168\.\d{1,3}\.\d{1,3}[:/]',                # 192.168.x.x (Class C private)
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


class CheckResult:
    """Result of a single validation check."""

    def __init__(self, name: str, status: str, errors: int = 0, detail: str = ""):
        """
        Args:
            name: Human-readable check name
            status: One of "passed", "failed", "skipped", "n/a"
            errors: Number of errors found (for failed checks)
            detail: Optional detail message (e.g., why skipped)
        """
        self.name = name
        self.status = status
        self.errors = errors
        self.detail = detail

    def __str__(self):
        if self.status == "passed":
            symbol = "\033[32m✓\033[0m"
        elif self.status == "failed":
            symbol = "\033[31m✗\033[0m"
        elif self.status == "skipped":
            symbol = "\033[33m⊘\033[0m"
        else:  # n/a
            symbol = "\033[90m-\033[0m"

        line = f"  {symbol} {self.name}"
        if self.detail:
            line += f" \033[90m({self.detail})\033[0m"
        return line


class CommitValidator:
    """Validates a commit against project guidelines."""

    def __init__(self, melee_root: Path = MELEE_ROOT, worktree_path: Optional[str] = None):
        self.melee_root = melee_root
        self.worktree_path = Path(worktree_path) if worktree_path else None
        self.errors: list[ValidationError] = []
        self.warnings: list[ValidationError] = []

    def validate_worktree_directory(self) -> None:
        """Check that cwd matches the expected worktree for staged files.

        Prevents committing to the wrong branch when agents lose track of
        their working directory during complex multi-directory operations.
        """
        staged_files = self._get_staged_files()

        # Find C files in melee/src/melee/
        c_files = [f for f in staged_files if f.startswith("melee/src/melee/") and f.endswith(".c")]
        if not c_files:
            return

        # Get the expected subdirectory from the first C file
        try:
            from src.cli.worktree_utils import (
                get_subdirectory_key,
                get_subdirectory_worktree_path,
                MELEE_WORKTREES_DIR,
            )
        except ImportError:
            # worktree_utils not available, skip check
            return

        # Get the subdirectory key for the first staged C file
        first_c_file = c_files[0]
        subdir_key = get_subdirectory_key(first_c_file)
        expected_worktree = get_subdirectory_worktree_path(subdir_key)

        # Check current working directory
        cwd = Path.cwd()

        # Case 1: We're in a worktree
        try:
            cwd.relative_to(MELEE_WORKTREES_DIR)
            # Check if it's the RIGHT worktree
            if not cwd.is_relative_to(expected_worktree):
                # Wrong worktree!
                current_worktree = cwd
                # Find the worktree root
                for parent in [cwd] + list(cwd.parents):
                    if parent.is_relative_to(MELEE_WORKTREES_DIR) and (parent / ".git").exists():
                        current_worktree = parent
                        break

                self.errors.append(ValidationError(
                    f"Wrong worktree! You're in {current_worktree.name} but staging files for {expected_worktree.name}. "
                    f"Run 'cd {expected_worktree}' first.",
                ))
            return
        except ValueError:
            pass  # Not in worktrees dir

        # Case 2: We're in the main melee submodule - warn if worktree exists
        if expected_worktree.exists():
            # Get current branch
            try:
                result = subprocess.run(
                    ["git", "branch", "--show-current"],
                    capture_output=True, text=True, check=True,
                    cwd=self.melee_root
                )
                current_branch = result.stdout.strip()
            except subprocess.CalledProcessError:
                current_branch = "unknown"

            expected_branch = f"subdirs/{subdir_key}"

            if current_branch != expected_branch:
                self.warnings.append(ValidationError(
                    f"Committing in main repo (branch: {current_branch}) but worktree exists at {expected_worktree}. "
                    f"Consider using the worktree to keep changes isolated."
                ))

    def _get_staged_files(self) -> list[str]:
        """Get list of staged files.

        Returns paths prefixed with 'melee/' for consistency with parent repo expectations,
        even when running from a worktree where paths are relative to worktree root.
        """
        # Determine which directory to run git commands in
        git_cwd = self.worktree_path if self.worktree_path else PROJECT_ROOT

        try:
            result = subprocess.run(
                ["git", "diff", "--cached", "--name-only"],
                capture_output=True, text=True, check=True,
                cwd=git_cwd
            )
            files = result.stdout.strip().split("\n") if result.stdout.strip() else []

            # If running from a worktree, paths are relative to worktree root (e.g., src/melee/...)
            # Prefix with 'melee/' for consistency with parent repo path expectations
            if self.worktree_path:
                files = [f"melee/{f}" for f in files]

            return files
        except subprocess.CalledProcessError:
            return []

    def _get_staged_diff(self, file_path: str) -> str:
        """Get the staged diff for a file."""
        git_cwd = self.worktree_path if self.worktree_path else PROJECT_ROOT

        # If running from worktree, strip the 'melee/' prefix we added in _get_staged_files
        actual_path = file_path
        if self.worktree_path and file_path.startswith("melee/"):
            actual_path = file_path[6:]  # Remove 'melee/' prefix

        try:
            result = subprocess.run(
                ["git", "diff", "--cached", actual_path],
                capture_output=True, text=True, check=True,
                cwd=git_cwd
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
            # Filter to just include paths and defines, add our warning flags
            clang_args = [
                "clang", "-fsyntax-only",
                "-Werror=implicit-function-declaration",
                "-Werror=typedef-redefinition",
            ]

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
        """Check CONTRIBUTING.md coding guidelines and doldecomp/melee PR review standards.

        Uses tree-sitter for AST-based detection when available, falling back to
        regex patterns otherwise.
        """
        staged_files = self._get_staged_files()
        # Check both .c and .h files for coding style
        code_files = [f for f in staged_files if f.startswith("melee/src/") and f.endswith((".c", ".h"))]

        if not code_files:
            return

        for c_file in code_files:
            diff = self._get_staged_diff(c_file)
            if not diff:
                continue

            # Use tree-sitter based analysis when available
            if TREE_SITTER_AVAILABLE and analyze_diff_additions is not None:
                issues = analyze_diff_additions(diff)
                for issue in issues:
                    self.errors.append(ValidationError(
                        f"{issue.message}: {issue.snippet}" +
                        (f" ({issue.suggestion})" if issue.suggestion else ""),
                        c_file,
                        issue.line
                    ))
            else:
                # Fallback to regex-based detection
                self._validate_coding_style_regex(c_file, diff)

    def _validate_coding_style_regex(self, c_file: str, diff: str) -> None:
        """Regex-based coding style validation (fallback when tree-sitter unavailable)."""
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
                # Patterns to catch:
                #   *(f32*)((u8*)fp + 0x844)
                #   *(s32*)((char*)ptr + 0x28)
                #   *(u32*)((u8*)cmd->u + 8)
                #   *((type*)(ptr + offset))
                ptr_arith_match = re.search(
                    r'\*\s*\(([^)]+\*)\)\s*\(\s*\(([^)]+\*)\)\s*([^+]+)\s*\+\s*([^)]+)\)',
                    content
                )
                if ptr_arith_match:
                    cast_type = ptr_arith_match.group(1).strip()
                    ptr_expr = ptr_arith_match.group(3).strip()
                    offset = ptr_arith_match.group(4).strip()
                    self.errors.append(ValidationError(
                        f"Raw pointer arithmetic for struct access - use M2C_FIELD({ptr_expr}, {offset}, {cast_type}) instead",
                        c_file, line_num
                    ))

            elif not line.startswith("-"):
                line_num += 1

    def validate_clang_format(self) -> None:
        """Auto-format staged C/H files with clang-format.

        This runs clang-format automatically and re-stages any changes,
        making formatting transparent to the committer.
        """
        staged_files = self._get_staged_files()
        # Check both .c and .h files for clang-format
        code_files = [f for f in staged_files if f.startswith("melee/src/") and f.endswith((".c", ".h"))]

        if not code_files:
            return

        git_cwd = self.worktree_path if self.worktree_path else PROJECT_ROOT

        # Get the actual file paths (strip melee/ prefix for worktrees)
        actual_files = []
        for f in code_files:
            if self.worktree_path and f.startswith("melee/"):
                actual_files.append(f[6:])  # Remove 'melee/' prefix
            else:
                actual_files.append(f)

        try:
            # Run clang-format on staged files (formats working tree)
            format_result = subprocess.run(
                ["git", "clang-format", "HEAD", "--"] + actual_files,
                capture_output=True, text=True,
                cwd=git_cwd
            )

            # Check if any files were modified
            if format_result.returncode == 0:
                output = format_result.stdout.strip()
                if output and "clang-format did not modify" not in output.lower():
                    # Re-stage the formatted files
                    subprocess.run(
                        ["git", "add"] + actual_files,
                        capture_output=True,
                        cwd=git_cwd
                    )
                    # Count formatted files from output
                    formatted_count = len([
                        line for line in output.split("\n")
                        if line.strip() and "changed" in line.lower()
                    ])
                    if formatted_count > 0:
                        self.warnings.append(ValidationError(
                            f"Auto-formatted {formatted_count} file(s) with clang-format"
                        ))
        except FileNotFoundError:
            # git-clang-format not installed - skip silently
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
        git_cwd = self.worktree_path if self.worktree_path else PROJECT_ROOT

        # Check C and header files
        code_files = [f for f in staged_files if f.endswith((".c", ".h"))]

        for code_file in code_files:
            # Strip melee/ prefix if running from worktree
            actual_path = code_file
            if self.worktree_path and code_file.startswith("melee/"):
                actual_path = code_file[6:]

            # Get the staged content
            try:
                result = subprocess.run(
                    ["git", "show", f":{actual_path}"],
                    capture_output=True, text=True, check=True,
                    cwd=git_cwd
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
        git_cwd = self.worktree_path if self.worktree_path else PROJECT_ROOT

        # Find staged C files
        c_files = [f for f in staged_files if f.endswith(".c") and "melee/src/" in f]

        if not c_files:
            return

        for c_file in c_files:
            # Strip melee/ prefix if running from worktree
            actual_path = c_file
            if self.worktree_path and c_file.startswith("melee/"):
                actual_path = c_file[6:]

            # Get the staged content
            try:
                result = subprocess.run(
                    ["git", "show", f":{actual_path}"],
                    capture_output=True, text=True, check=True,
                    cwd=git_cwd
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
            actual_header_path = header_file
            if self.worktree_path and header_file.startswith("melee/"):
                actual_header_path = header_file[6:]

            # Get header content (try staged first, then working tree)
            header_content = None
            try:
                result = subprocess.run(
                    ["git", "show", f":{actual_header_path}"],
                    capture_output=True, text=True, check=True,
                    cwd=git_cwd
                )
                header_content = result.stdout
            except subprocess.CalledProcessError:
                # Try reading from working tree
                if self.worktree_path:
                    header_path = self.worktree_path / actual_header_path
                else:
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

        This check looks for cases where a descriptive name is REPLACED by an
        address-based name (i.e., removed from one line, address name added in
        similar position). It avoids false positives from line reformatting.
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

            # Collect all descriptive names from removed lines and added lines
            removed_descriptive = set()
            added_descriptive = set()
            added_address = set()

            for line in diff.split("\n"):
                if line.startswith("-") and not line.startswith("---"):
                    # Look for descriptive symbol names being removed
                    names = re.findall(r'\b([A-Z][a-zA-Z0-9_]*(?:Table|State|Data|Info|List|Array)[a-zA-Z0-9_]*)\b', line)
                    removed_descriptive.update(names)

                elif line.startswith("+") and not line.startswith("+++"):
                    # Track descriptive names on added lines (to exclude reformatting)
                    names = re.findall(r'\b([A-Z][a-zA-Z0-9_]*(?:Table|State|Data|Info|List|Array)[a-zA-Z0-9_]*)\b', line)
                    added_descriptive.update(names)
                    # Look for address-based names being added
                    addr_names = re.findall(r'\b((?:fn|it|ft|gr|lb|gm|if|mp|vi)_[0-9A-Fa-f]{8})\b', line)
                    added_address.update(addr_names)

            # Only flag if descriptive names were ACTUALLY removed (not just reformatted)
            # A name is "actually removed" if it's on a removed line but NOT on any added line
            actually_removed = removed_descriptive - added_descriptive

            # Only error if we actually removed descriptive names AND added address-based names
            if actually_removed and added_address:
                self.errors.append(ValidationError(
                    f"Bad rename: removed descriptive names {list(actually_removed)[:3]}, "
                    f"added address-based names {list(added_address)[:3]} - keep descriptive names",
                    code_file
                ))

    def validate_local_urls_in_commits(self) -> None:
        """Check for local decomp.me URLs in pending commit messages.

        PR feedback: Commit messages should use production decomp.me URLs,
        not local server URLs like nzxt-discord.local or 10.200.0.1.

        NOTE: This is a pre-commit hook, so it can only check EXISTING commits,
        not the commit being created (that message doesn't exist yet).
        Historical commits are reported as warnings, not errors, since they
        can't be fixed without rewriting history.
        """
        # Only check if we're staging melee submodule changes
        staged_files = self._get_staged_files()
        melee_changes = [f for f in staged_files if f.startswith("melee/")]
        if not melee_changes:
            return

        # Get pending commits in melee submodule (not yet pushed to upstream)
        # Only check the MOST RECENT few commits to avoid noise from old history
        if not self.melee_root.exists():
            return

        try:
            result = subprocess.run(
                ["git", "log", "--oneline", "upstream/master..HEAD", "--format=%H %s", "-n", "5"],
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

                # Use warnings instead of errors since these are historical commits
                # that can't be changed without rewriting history
                if unmapped_slugs:
                    self.warnings.append(ValidationError(
                        f"Commit {commit_hash} has local scratch URL(s) without production mapping: {unmapped_slugs}. "
                        f"Run 'melee-agent sync production' before pushing.",
                    ))
                else:
                    # Has mapping but URL not replaced
                    self.warnings.append(ValidationError(
                        f"Commit {commit_hash} has local scratch URL(s) - consider amending to use production URLs. "
                        f"Local slugs found: {local_urls}",
                    ))

    def validate_match_regressions(self) -> None:
        """Check for match percentage regressions after building.

        Compares the current report.json against a rebuild with staged changes
        to detect any functions that regressed in match percentage.
        """
        staged_files = self._get_staged_files()

        # Only run if there are staged changes to melee source or symbols
        melee_changes = [f for f in staged_files
                         if f.startswith("melee/src/") or f == "melee/config/GALE01/symbols.txt"]
        if not melee_changes:
            return

        report_file = self.melee_root / "build" / "GALE01" / "report.json"

        # Load current report (pre-build state)
        if not report_file.exists():
            self.warnings.append(ValidationError(
                "No report.json found - run 'ninja' first to enable regression detection"
            ))
            return

        try:
            with open(report_file) as f:
                old_report = json.load(f)
        except (json.JSONDecodeError, IOError) as e:
            self.warnings.append(ValidationError(
                f"Failed to load report.json: {e}"
            ))
            return

        # Build mapping of function -> match percentage
        old_matches: dict[str, float] = {}
        for unit in old_report.get("units", []):
            for func in unit.get("functions", []):
                name = func.get("name")
                match_pct = func.get("fuzzy_match_percent")
                if name and match_pct is not None:
                    old_matches[name] = match_pct

        # Run ninja to rebuild with staged changes
        try:
            result = subprocess.run(
                ["ninja"],
                capture_output=True,
                text=True,
                cwd=self.melee_root,
                timeout=300  # 5 minute timeout
            )
            if result.returncode != 0:
                self.warnings.append(ValidationError(
                    "Build failed - cannot check for regressions"
                ))
                return
        except subprocess.TimeoutExpired:
            self.warnings.append(ValidationError(
                "Build timed out - cannot check for regressions"
            ))
            return
        except FileNotFoundError:
            self.warnings.append(ValidationError(
                "ninja not found - cannot check for regressions"
            ))
            return

        # Load new report
        try:
            with open(report_file) as f:
                new_report = json.load(f)
        except (json.JSONDecodeError, IOError):
            self.warnings.append(ValidationError(
                "Failed to load report.json after build"
            ))
            return

        # Compare and find regressions
        regressions = []
        for unit in new_report.get("units", []):
            for func in unit.get("functions", []):
                name = func.get("name")
                new_pct = func.get("fuzzy_match_percent")

                if name and name in old_matches:
                    old_pct = old_matches[name]
                    # Regression: went from some match to lower match
                    # or went from matched to unmatched (None)
                    if old_pct is not None and old_pct > 0:
                        if new_pct is None or new_pct < old_pct:
                            new_display = f"{new_pct:.1f}%" if new_pct else "0%"
                            regressions.append(
                                f"{name}: {old_pct:.1f}% → {new_display}"
                            )

        if regressions:
            for reg in regressions[:5]:  # Limit to first 5
                self.errors.append(ValidationError(
                    f"Match regression: {reg}"
                ))
            if len(regressions) > 5:
                self.errors.append(ValidationError(
                    f"... and {len(regressions) - 5} more regressions"
                ))

    def run(self, skip_regressions: bool = False) -> tuple[list[ValidationError], list[ValidationError], list[CheckResult]]:
        """Run all validations.

        Args:
            skip_regressions: If True, skip the build and regression check.
                             By default, runs ninja and checks for match regressions.

        Returns:
            Tuple of (errors, warnings, check_results)
        """
        check_results = []
        staged_files = self._get_staged_files()
        c_files = [f for f in staged_files if f.endswith(".c") and "melee/" in f]
        h_files = [f for f in staged_files if f.endswith(".h") and "melee/" in f]
        code_files = c_files + h_files  # Both C and header files
        melee_changes = [f for f in staged_files if f.startswith("melee/")]

        def run_check(name: str, method, condition: bool = True, skip_reason: str = ""):
            """Run a check and record its result."""
            if not condition:
                check_results.append(CheckResult(name, "n/a", detail=skip_reason))
                return

            errors_before = len(self.errors)
            method()
            errors_after = len(self.errors)
            new_errors = errors_after - errors_before

            if new_errors > 0:
                check_results.append(CheckResult(name, "failed", errors=new_errors))
            else:
                check_results.append(CheckResult(name, "passed"))

        # Run checks with appropriate conditions
        run_check("Worktree directory", self.validate_worktree_directory,
                  bool(c_files), "no C files")
        run_check("Forbidden files", self.validate_forbidden_files)
        run_check("Conflict markers", self.validate_conflict_markers,
                  bool(code_files), "no C/H files")
        run_check("Header signatures", self.validate_header_signatures,
                  bool(c_files), "no C files")
        run_check("Implicit declarations", self.validate_implicit_declarations,
                  bool(c_files), "no C files")
        run_check("Symbols.txt", self.validate_symbols_txt,
                  bool(c_files), "no C files")
        run_check("Coding style", self.validate_coding_style,
                  bool(code_files), "no C/H files")
        run_check("Extern declarations", self.validate_extern_declarations,
                  bool(c_files), "no C files")
        run_check("Symbol renames", self.validate_symbol_renames,
                  bool(code_files), "no C/H files")
        run_check("Local URLs", self.validate_local_urls_in_commits,
                  bool(melee_changes), "no melee changes")
        run_check("clang-format", self.validate_clang_format,
                  bool(code_files), "no C/H files")

        if skip_regressions:
            check_results.append(CheckResult("Match regressions", "skipped", detail="--skip-regressions"))
        else:
            run_check("Match regressions", self.validate_match_regressions,
                      bool(melee_changes), "no melee changes")

        return self.errors, self.warnings, check_results


def main():
    parser = argparse.ArgumentParser(description="Validate commit against project guidelines")
    parser.add_argument("--fix", action="store_true", help="Attempt to fix issues")
    parser.add_argument("--verbose", "-v", action="store_true", help="Show all warnings")
    parser.add_argument("--skip-regressions", action="store_true",
                        help="Skip build and regression check (faster but less thorough)")
    parser.add_argument("--quiet", "-q", action="store_true",
                        help="Only show errors, not check status")
    parser.add_argument("--worktree", type=str, default=None,
                        help="Path to the git worktree (for running git commands in correct context)")
    parser.add_argument("--timeout", type=int, default=DEFAULT_VALIDATION_TIMEOUT,
                        help=f"Timeout in seconds (default: {DEFAULT_VALIDATION_TIMEOUT})")
    args = parser.parse_args()

    # Set up timeout signal handler (Unix only)
    if hasattr(signal, 'SIGALRM') and args.timeout > 0:
        signal.signal(signal.SIGALRM, _timeout_handler)
        signal.alarm(args.timeout)

    # Determine the melee root - use worktree if provided, otherwise default
    if args.worktree:
        melee_root = Path(args.worktree)
    else:
        melee_root = MELEE_ROOT

    validator = CommitValidator(melee_root=melee_root, worktree_path=args.worktree)
    errors, warnings, check_results = validator.run(skip_regressions=args.skip_regressions)

    # Print check results (unless quiet mode)
    if not args.quiet:
        print("\033[1mPre-commit checks:\033[0m")
        for result in check_results:
            print(result)

    # Print warnings if verbose
    if warnings and args.verbose:
        print("\n\033[33mWarnings:\033[0m")
        for w in warnings:
            print(f"  ⚠ {w}")

    # Print errors
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

    # Summary
    passed = sum(1 for r in check_results if r.status == "passed")
    skipped = sum(1 for r in check_results if r.status in ("skipped", "n/a"))

    if warnings:
        print(f"\n\033[33m{len(warnings)} warning(s) - commit allowed\033[0m")
        if not args.verbose:
            print("  Run with --verbose to see details")

    if not args.quiet:
        print(f"\n\033[32m✓ All checks passed ({passed} passed, {skipped} skipped)\033[0m")
    else:
        print("\033[32m✓ Validation passed\033[0m")

    # Cancel the alarm on successful completion
    if hasattr(signal, 'SIGALRM'):
        signal.alarm(0)

    sys.exit(0)


if __name__ == "__main__":
    main()
