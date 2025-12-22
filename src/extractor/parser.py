"""Parse configure.py to extract Object definitions and their matching status."""

import ast
import re
from pathlib import Path
from typing import Optional
from .models import ObjectStatus


class ConfigureParser:
    """Parser for configure.py to extract object file information."""

    def __init__(self, melee_root: Path):
        """
        Initialize parser.

        Args:
            melee_root: Path to the melee project root directory
        """
        self.melee_root = Path(melee_root)
        self.configure_path = self.melee_root / "configure.py"

    def parse_objects(self) -> list[ObjectStatus]:
        """
        Parse configure.py and extract all Object definitions.

        Returns:
            List of ObjectStatus instances
        """
        if not self.configure_path.exists():
            raise FileNotFoundError(f"configure.py not found at {self.configure_path}")

        with open(self.configure_path, "r", encoding="utf-8") as f:
            content = f.read()

        return self._extract_objects_from_content(content)

    def _extract_objects_from_content(self, content: str) -> list[ObjectStatus]:
        """
        Extract Object definitions from configure.py content.

        Uses regex parsing since AST parsing is complex due to the
        dynamic nature of the configuration (Matching/NonMatching variables).

        Args:
            content: Content of configure.py

        Returns:
            List of ObjectStatus instances
        """
        objects = []
        current_lib = None

        # Pattern to match library definitions: MeleeLib("name", ...)
        lib_pattern = re.compile(r'MeleeLib\(\s*"([^"]+)"')

        # Pattern to match Object definitions
        # Object(Status, "path/to/file.c")
        obj_pattern = re.compile(
            r'Object\(\s*(Matching|NonMatching|Equivalent|MatchingFor\([^)]+\))\s*,\s*"([^"]+)"\s*[,)]'
        )

        lines = content.split("\n")
        for line in lines:
            # Check for library definition
            lib_match = lib_pattern.search(line)
            if lib_match:
                current_lib = lib_match.group(1)
                continue

            # Check for Object definition
            obj_match = obj_pattern.search(line)
            if obj_match:
                status_str = obj_match.group(1)
                file_path = obj_match.group(2)

                # Normalize status
                if status_str.startswith("MatchingFor"):
                    # Treat MatchingFor as Matching for now
                    # Could be enhanced to check version
                    status = "Matching"
                else:
                    status = status_str

                objects.append(
                    ObjectStatus(
                        file_path=file_path,
                        status=status,
                        source=file_path,
                        lib=current_lib,
                    )
                )

        return objects

    def get_object_status(self, file_path: str) -> Optional[ObjectStatus]:
        """
        Get the status of a specific object file.

        Args:
            file_path: Relative path to the source file (e.g., "melee/lb/lbfile.c")

        Returns:
            ObjectStatus if found, None otherwise
        """
        objects = self.parse_objects()
        for obj in objects:
            if obj.file_path == file_path:
                return obj
        return None

    def get_non_matching_objects(self) -> list[ObjectStatus]:
        """
        Get all non-matching object files.

        Returns:
            List of ObjectStatus instances with status="NonMatching"
        """
        objects = self.parse_objects()
        return [obj for obj in objects if obj.status == "NonMatching"]

    def get_matching_objects(self) -> list[ObjectStatus]:
        """
        Get all matching object files.

        Returns:
            List of ObjectStatus instances with status="Matching"
        """
        objects = self.parse_objects()
        return [obj for obj in objects if obj.status == "Matching"]

    def get_equivalent_objects(self) -> list[ObjectStatus]:
        """
        Get all equivalent object files.

        Returns:
            List of ObjectStatus instances with status="Equivalent"
        """
        objects = self.parse_objects()
        return [obj for obj in objects if obj.status == "Equivalent"]

    def get_objects_by_lib(self, lib_name: str) -> list[ObjectStatus]:
        """
        Get all objects belonging to a specific library.

        Args:
            lib_name: Name of the library (e.g., "lb (Library)")

        Returns:
            List of ObjectStatus instances
        """
        objects = self.parse_objects()
        return [obj for obj in objects if obj.lib == lib_name]

    def get_all_libs(self) -> list[str]:
        """
        Get all unique library names.

        Returns:
            List of library names
        """
        objects = self.parse_objects()
        libs = set(obj.lib for obj in objects if obj.lib)
        return sorted(libs)


async def parse_configure(melee_root: Path) -> list[ObjectStatus]:
    """
    Async wrapper for parsing configure.py.

    Args:
        melee_root: Path to the melee project root directory

    Returns:
        List of ObjectStatus instances
    """
    parser = ConfigureParser(melee_root)
    return parser.parse_objects()
