"""Generate decompilation context for functions.

This module generates context information (includes and type definitions)
for functions, similar to the decompctx.py tool in the melee project.
"""

import re
import subprocess
from pathlib import Path
from typing import Optional


class ContextGenerator:
    """Generator for decompilation context."""

    def __init__(self, melee_root: Path):
        """
        Initialize context generator.

        Args:
            melee_root: Path to the melee project root directory
        """
        self.melee_root = Path(melee_root)
        self.src_dir = self.melee_root / "src"
        self.include_dirs = [
            self.melee_root / "include",
            self.melee_root / "src",
            self.melee_root / "build" / "GALE01" / "include",
        ]

        # Patterns from decompctx.py
        self.include_pattern = re.compile(r'^#\s*include\s*[<"](.+?)[>"]')
        self.guard_pattern = re.compile(r"^#\s*ifndef\s+(.*)$")
        self.once_pattern = re.compile(r"^#\s*pragma\s+once$")

        self.defines = set()
        self.processed_files = set()

    def generate_context(self, source_file: str) -> str:
        """
        Generate decompilation context for a source file.

        This processes all includes recursively and returns the complete
        context that can be used on decomp.me.

        Args:
            source_file: Relative path to source file (e.g., "melee/lb/lbfile.c")

        Returns:
            Context string with all includes expanded
        """
        # Reset state for new generation
        self.defines = set()
        self.processed_files = set()

        source_path = self.src_dir / source_file
        if not source_path.exists():
            raise FileNotFoundError(f"Source file not found: {source_path}")

        return self._import_c_file(source_path)

    def _import_h_file(self, include_file: str, relative_to: Path) -> str:
        """
        Import a header file.

        Args:
            include_file: Name of the include file
            relative_to: Path to search relative to

        Returns:
            Processed content of the header file
        """
        # Try relative to the current file
        rel_path = relative_to.parent / include_file
        if rel_path.exists():
            return self._import_c_file(rel_path)

        # Try include directories
        for include_dir in self.include_dirs:
            inc_path = include_dir / include_file
            if inc_path.exists():
                return self._import_c_file(inc_path)

        # File not found - return a comment
        return f'/* Failed to locate {include_file} */\n'

    def _import_c_file(self, file_path: Path) -> str:
        """
        Import a C/C++ file with include processing.

        Args:
            file_path: Path to the file

        Returns:
            Processed file content
        """
        # Normalize path
        try:
            rel_path = file_path.relative_to(self.melee_root)
        except ValueError:
            rel_path = file_path

        rel_path_str = str(rel_path)

        # Check if already processed
        if rel_path_str in self.processed_files:
            return ""

        self.processed_files.add(rel_path_str)

        try:
            with open(file_path, encoding="utf-8") as f:
                lines = list(f)
        except Exception:
            try:
                with open(file_path) as f:
                    lines = list(f)
            except Exception:
                return f'/* Failed to read {rel_path_str} */\n'

        return self._process_file(rel_path_str, lines, file_path)

    def _process_file(self, file_name: str, lines: list[str], file_path: Path) -> str:
        """
        Process file content, expanding includes.

        Args:
            file_name: Name/path of the file
            lines: Lines of the file
            file_path: Full path to the file

        Returns:
            Processed content
        """
        out_text = ""

        for idx, line in enumerate(lines):
            # Check for include guard on first line
            if idx == 0:
                guard_match = self.guard_pattern.match(line.strip())
                if guard_match:
                    if guard_match[1] in self.defines:
                        # Already included, skip this file
                        return ""
                    self.defines.add(guard_match[1])
                else:
                    once_match = self.once_pattern.match(line.strip())
                    if once_match:
                        if file_name in self.defines:
                            # Already included, skip this file
                            return ""
                        self.defines.add(file_name)

            # Check for include directive
            include_match = self.include_pattern.match(line.strip())
            if include_match and not include_match[1].endswith(".s"):
                include_file = include_match[1]
                out_text += f'/* "{file_name}" line {idx} "{include_file}" */\n'
                out_text += self._import_h_file(include_file, file_path)
                out_text += f'/* end "{include_file}" */\n'
            else:
                out_text += line

        return out_text

    def generate_context_using_tool(self, source_file: str) -> Optional[str]:
        """
        Generate context using the project's decompctx.py tool.

        This is an alternative method that calls the existing tool directly.

        Args:
            source_file: Relative path to source file

        Returns:
            Context string or None if tool execution fails
        """
        decompctx_tool = self.melee_root / "tools" / "decompctx.py"
        if not decompctx_tool.exists():
            return None

        source_path = self.src_dir / source_file
        if not source_path.exists():
            return None

        try:
            # Build include arguments
            include_args = []
            for inc_dir in self.include_dirs:
                if inc_dir.exists():
                    include_args.extend(["-I", str(inc_dir)])

            # Run decompctx.py
            cmd = [
                "python3",
                str(decompctx_tool),
                str(source_path),
                "-o", "/dev/stdout",
            ] + include_args

            result = subprocess.run(
                cmd,
                cwd=str(self.melee_root),
                capture_output=True,
                text=True,
                timeout=30,
            )

            if result.returncode == 0:
                return result.stdout
            else:
                return None

        except Exception:
            return None

    def get_web_context_url(self, source_file: str) -> str:
        """
        Get the web context URL for a source file.

        Args:
            source_file: Relative path to source file

        Returns:
            URL to the web context viewer
        """
        # The project has a web context at doldecomp.github.io/melee/ctx.html
        return f"https://doldecomp.github.io/melee/ctx.html#{source_file}"


async def generate_context(melee_root: Path, source_file: str) -> str:
    """
    Async wrapper for generating context.

    Args:
        melee_root: Path to the melee project root directory
        source_file: Relative path to source file

    Returns:
        Context string
    """
    generator = ContextGenerator(melee_root)
    return generator.generate_context(source_file)
