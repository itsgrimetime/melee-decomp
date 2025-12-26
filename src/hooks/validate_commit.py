#!/usr/bin/env python3
"""Pre-commit validation hook for melee decompilation commits.

Validates:
1. 100% matches are in scratches.txt and not duplicates
2. Scratch IDs are production decomp.me slugs (not local)
3. symbols.txt is updated if function names changed
4. CONTRIBUTING.md guidelines are followed

Usage:
    python -m src.hooks.validate_commit [--fix] [--no-production-check]
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
SCRATCHES_FILE = MELEE_ROOT / "config" / "GALE01" / "scratches.txt"
SYMBOLS_FILE = MELEE_ROOT / "config" / "GALE01" / "symbols.txt"
SLUG_MAP_FILE = PROJECT_ROOT / "config" / "scratches_slug_map.json"

# Production decomp.me
PRODUCTION_DECOMP_ME = "https://decomp.me"


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

    def __init__(self, melee_root: Path = MELEE_ROOT, check_production: bool = True):
        self.melee_root = melee_root
        self.check_production = check_production
        self.errors: list[ValidationError] = []
        self.warnings: list[ValidationError] = []

        # Load slug map (production_slug -> local info)
        self.slug_map = self._load_slug_map()
        # Invert to get local_slug -> production_slug
        self.local_to_production = {
            v.get("local_slug"): k
            for k, v in self.slug_map.items()
            if v.get("local_slug")
        }

    def _load_slug_map(self) -> dict:
        """Load the production slug mapping."""
        if not SLUG_MAP_FILE.exists():
            return {}
        try:
            with open(SLUG_MAP_FILE, "r") as f:
                return json.load(f)
        except (json.JSONDecodeError, IOError):
            return {}

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

    def validate_scratches_txt(self) -> None:
        """Validate only NEW scratches.txt entries (from staged changes)."""
        if not SCRATCHES_FILE.exists():
            return

        # Get the staged diff for scratches.txt
        diff = self._get_staged_diff("melee/config/GALE01/scratches.txt")
        if not diff:
            return  # No changes to scratches.txt

        # Parse the full file to check for duplicates against existing entries
        content = SCRATCHES_FILE.read_text()
        existing_slugs: set[str] = set()
        existing_funcs_100pct: dict[str, list[str]] = {}  # func_name -> [slugs]

        entry_pattern = re.compile(
            r'^(?P<name>\w+)\s*=\s*(?P<match>[\d.]+%|OK):(?P<status>\S+);\s*//'
            r'(?:\s*author:(?P<author>\S+))?'
            r'(?:\s*id:(?P<slug>\w+))?',
            re.MULTILINE
        )

        for match in entry_pattern.finditer(content):
            slug = match.group("slug")
            func_name = match.group("name")
            match_pct = match.group("match")
            if slug:
                existing_slugs.add(slug)
            if match_pct in ("OK", "100%"):
                if func_name not in existing_funcs_100pct:
                    existing_funcs_100pct[func_name] = []
                if slug:
                    existing_funcs_100pct[func_name].append(slug)

        # Now check only the ADDED lines in the diff
        added_entries: list[tuple[str, str, str]] = []  # (func_name, slug, match_pct)

        for line in diff.split("\n"):
            if not line.startswith("+") or line.startswith("+++"):
                continue

            content_line = line[1:]  # Remove + prefix
            match = entry_pattern.match(content_line.strip())
            if not match:
                continue

            func_name = match.group("name")
            slug = match.group("slug")
            match_pct = match.group("match")

            if not slug:
                continue

            added_entries.append((func_name, slug, match_pct))

            # Check if this is a local slug (not synced to production)
            if slug in self.local_to_production:
                prod_slug = self.local_to_production[slug]
                self.errors.append(ValidationError(
                    f"Local slug '{slug}' should be production slug '{prod_slug}'",
                    str(SCRATCHES_FILE), fixable=True
                ))

            # Check for duplicate slug being added
            if slug in existing_slugs:
                # Only warn if we're adding a duplicate (the slug already exists)
                # Count how many times this slug appears in added entries
                added_count = sum(1 for _, s, _ in added_entries if s == slug)
                if added_count > 1:
                    self.errors.append(ValidationError(
                        f"Duplicate scratch ID '{slug}' being added",
                        str(SCRATCHES_FILE)
                    ))

            # Check for duplicate 100% match for same function
            if match_pct in ("OK", "100%"):
                if func_name in existing_funcs_100pct:
                    existing_100_slugs = existing_funcs_100pct[func_name]
                    # Only warn if adding a NEW 100% entry (different slug)
                    if slug not in existing_100_slugs:
                        self.warnings.append(ValidationError(
                            f"Adding another 100% match for '{func_name}' (existing: {existing_100_slugs[:2]})",
                            str(SCRATCHES_FILE)
                        ))

            # Check if slug exists on production (for new 100% matches)
            if match_pct in ("OK", "100%") and slug not in self.slug_map:
                if self.check_production:
                    self.warnings.append(ValidationError(
                        f"New 100% match '{func_name}' (id:{slug}) not in slug map - sync to production first",
                        str(SCRATCHES_FILE)
                    ))

    def validate_production_slugs(self) -> None:
        """Verify new scratch IDs exist on production decomp.me (via HTTP)."""
        if not self.check_production:
            return

        # Get newly added scratches.txt entries from staged diff
        diff = self._get_staged_diff("melee/config/GALE01/scratches.txt")
        if not diff:
            return

        # Find added entries with 100% match that aren't in our slug map
        slugs_to_check = []
        for line in diff.split("\n"):
            if line.startswith("+") and not line.startswith("+++"):
                content = line[1:]
                if "100%" in content or "= OK:" in content:
                    slug_match = re.search(r'\bid:(\w{5})\b', content)
                    if slug_match:
                        slug = slug_match.group(1)
                        # Skip if already in slug map (known production slug)
                        if slug not in self.slug_map and slug not in self.local_to_production:
                            slugs_to_check.append(slug)

        if not slugs_to_check:
            return

        # Verify slugs exist on production (limit to avoid slowdown)
        import httpx

        try:
            with httpx.Client(timeout=10.0) as client:
                for slug in slugs_to_check[:3]:
                    try:
                        resp = client.get(f"{PRODUCTION_DECOMP_ME}/api/scratch/{slug}")
                        if resp.status_code == 404:
                            self.errors.append(ValidationError(
                                f"Scratch '{slug}' not found on production - run 'melee-agent sync production' first",
                                str(SCRATCHES_FILE)
                            ))
                    except Exception:
                        pass  # Network errors shouldn't block
        except Exception:
            pass

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

    def run(self) -> tuple[list[ValidationError], list[ValidationError]]:
        """Run all validations."""
        self.validate_scratches_txt()
        self.validate_production_slugs()
        self.validate_symbols_txt()
        self.validate_coding_style()
        self.validate_clang_format()
        return self.errors, self.warnings


def main():
    parser = argparse.ArgumentParser(description="Validate commit against project guidelines")
    parser.add_argument("--fix", action="store_true", help="Attempt to fix issues")
    parser.add_argument("--no-production-check", action="store_true",
                        help="Skip production decomp.me verification")
    parser.add_argument("--verbose", "-v", action="store_true", help="Show all warnings")
    args = parser.parse_args()

    validator = CommitValidator(check_production=not args.no_production_check)
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
