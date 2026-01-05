"""Pydantic models for function metadata in the melee decompilation project."""

from typing import Optional, Literal
from pydantic import BaseModel, Field


class ObjectStatus(BaseModel):
    """Status of an object file in the decompilation project."""

    file_path: str = Field(description="Relative path in src/ directory")
    status: Literal["Matching", "NonMatching", "Equivalent"] = Field(
        description="Matching status of the object"
    )
    source: Optional[str] = Field(
        default=None,
        description="Source file path (may differ from file_path)"
    )
    lib: Optional[str] = Field(
        default=None,
        description="Library this object belongs to"
    )


class FunctionSymbol(BaseModel):
    """Function symbol information from symbols.txt."""

    name: str = Field(description="Function name")
    address: str = Field(description="Hex address like 0x800C5A30")
    size_bytes: int = Field(description="Size of function in bytes")
    section: str = Field(description="Section like .init, .text, etc.")
    scope: Optional[str] = Field(
        default=None,
        description="Scope: global, local, weak"
    )


class FunctionMatch(BaseModel):
    """Function-level match data from report.json."""

    name: str = Field(description="Function name")
    fuzzy_match_percent: float = Field(
        description="Fuzzy match percentage (0.0 to 100.0)",
        ge=0.0,
        le=100.0
    )
    address: Optional[str] = Field(
        default=None,
        description="Hex address like 0x80003100 (from virtual_address metadata)"
    )


class FunctionInfo(BaseModel):
    """Complete information about a function for decompilation."""

    name: str = Field(description="Function name")
    file_path: str = Field(description="Relative path in src/ directory")
    address: str = Field(description="Hex address like 0x800C5A30")
    size_bytes: int = Field(description="Size of function in bytes")
    current_match: float = Field(
        description="Current match percentage (0.0 to 1.0)",
        ge=0.0,
        le=1.0
    )
    asm: Optional[str] = Field(
        default=None,
        description="Assembly code for the function"
    )
    context: Optional[str] = Field(
        default=None,
        description="Includes and type definitions for decompilation"
    )
    object_status: Literal["Matching", "NonMatching", "Equivalent"] = Field(
        description="Status of the containing object file"
    )
    section: str = Field(
        default=".text",
        description="Section like .init, .text, etc."
    )
    lib: Optional[str] = Field(
        default=None,
        description="Library this function belongs to"
    )

    @property
    def is_matched(self) -> bool:
        """Check if function is fully matched."""
        return self.current_match >= 1.0

    @property
    def match_percent(self) -> float:
        """Get match percentage (0-100)."""
        return self.current_match * 100.0


class ExtractionResult(BaseModel):
    """Result of extracting functions from the project."""

    functions: list[FunctionInfo] = Field(
        default_factory=list,
        description="List of extracted functions"
    )
    total_functions: int = Field(
        default=0,
        description="Total number of functions extracted"
    )
    matched_functions: int = Field(
        default=0,
        description="Number of fully matched functions"
    )
    unmatched_functions: int = Field(
        default=0,
        description="Number of unmatched functions"
    )

    @property
    def match_percentage(self) -> float:
        """Overall match percentage."""
        if self.total_functions == 0:
            return 0.0
        return (self.matched_functions / self.total_functions) * 100.0
