"""Pydantic models for decomp.me API requests and responses."""

from datetime import datetime
from typing import Any

from pydantic import BaseModel, Field


class Library(BaseModel):
    """Library dependency for compilation."""

    name: str
    version: str


class Profile(BaseModel):
    """User profile information."""

    is_anonymous: bool
    id: int
    is_online: bool
    is_admin: bool
    username: str
    github_id: int | None = None
    frog_color: list[float] | None = None  # HSL color values


class ScratchCreate(BaseModel):
    """Request model for creating a new scratch."""

    name: str | None = None
    compiler: str = "mwcc_247_92"  # Default to Melee compiler
    platform: str | None = None  # Defaults to compiler's platform
    compiler_flags: str = ""
    diff_flags: list[str] = Field(default_factory=list)
    preset: str | int | None = None  # Preset ID
    source_code: str | None = None
    target_asm: str = ""
    context: str = ""
    diff_label: str = ""  # Function name
    libraries: list[dict[str, str]] = Field(default_factory=list)

    model_config = {"extra": "allow"}


class Scratch(BaseModel):
    """Scratch response model."""

    slug: str
    name: str
    description: str = ""
    creation_time: datetime
    last_updated: datetime
    compiler: str
    platform: str
    compiler_flags: str
    diff_flags: list[str]
    preset: str | int | None = None
    source_code: str
    context: str
    diff_label: str
    score: int  # -1 if doesn't compile, 0 = perfect match, >0 = diff bytes
    max_score: int
    match_override: bool
    libraries: list[Library]
    parent: str | None = None
    owner: Profile | None = None
    language: str | None = None
    claim_token: str | None = None  # Only present on creation

    model_config = {"extra": "allow"}


class TerseScratch(BaseModel):
    """Minimal scratch information for listings."""

    slug: str
    owner: Profile | None
    last_updated: datetime
    creation_time: datetime
    platform: str
    compiler: str
    preset: str | None
    name: str
    score: int
    max_score: int
    match_override: bool
    parent: str | None
    libraries: list[Library]

    model_config = {"extra": "allow"}


class DiffRow(BaseModel):
    """A single row in the diff output."""

    key: str | None = None
    base: dict[str, Any] | None = None
    current: dict[str, Any] | None = None

    model_config = {"extra": "allow"}


class DiffOutput(BaseModel):
    """Diff comparison output."""

    arch_str: str
    current_score: int
    max_score: int
    rows: list[DiffRow] = Field(default_factory=list)
    mnemonic_counts: dict[str, Any] | None = None

    model_config = {"extra": "allow"}


class CompilationResult(BaseModel):
    """Result of compiling a scratch."""

    success: bool
    compiler_output: str  # Compiler errors/warnings
    diff_output: DiffOutput | None = None
    left_object: str | None = None  # Base64 encoded object file
    right_object: str | None = None  # Base64 encoded object file

    @property
    def score(self) -> int:
        """Get current score from diff output."""
        if self.diff_output:
            return self.diff_output.current_score
        return -1

    @property
    def max_score(self) -> int:
        """Get max score from diff output."""
        if self.diff_output:
            return self.diff_output.max_score
        return -1

    @property
    def is_perfect(self) -> bool:
        """Check if compilation resulted in a perfect match."""
        return self.success and self.score == 0


class DecompilationResult(BaseModel):
    """Result of decompiling a scratch."""

    decompilation: str


class CompilerInfo(BaseModel):
    """Information about an available compiler."""

    id: str
    name: str
    platform: str
    language: str

    model_config = {"extra": "allow"}


class PresetInfo(BaseModel):
    """Information about a preset."""

    id: str
    name: str
    platform: str
    compiler: str
    compiler_flags: str
    diff_flags: list[str]
    libraries: list[Library]

    model_config = {"extra": "allow"}


class ScratchUpdate(BaseModel):
    """Request model for updating a scratch."""

    name: str | None = None
    compiler: str | None = None
    compiler_flags: str | None = None
    diff_flags: list[str] | None = None
    source_code: str | None = None
    context: str | None = None
    diff_label: str | None = None
    libraries: list[dict[str, str]] | None = None
    match_override: bool | None = None

    model_config = {"extra": "forbid"}


class CompileRequest(BaseModel):
    """Request model for compiling with overrides."""

    compiler: str | None = None
    compiler_flags: str | None = None
    diff_flags: list[str] | None = None
    diff_label: str | None = None
    source_code: str | None = None
    context: str | None = None
    libraries: list[dict[str, str]] | None = None
    include_objects: bool = False  # Include base64 encoded objects

    model_config = {"extra": "forbid"}


class ForkRequest(BaseModel):
    """Request model for forking a scratch."""

    name: str | None = None
    source_code: str | None = None
    compiler_flags: str | None = None

    model_config = {"extra": "allow"}
