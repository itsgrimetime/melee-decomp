"""Models for the decompilation agent."""

from dataclasses import dataclass, field
from typing import Optional


@dataclass
class MatchResult:
    """Result of a matching attempt."""

    function_name: str
    matched: bool
    best_match: float  # 0.0 to 1.0
    scratch_slug: Optional[str] = None
    iterations: int = 0
    pr_url: Optional[str] = None
    error: Optional[str] = None


@dataclass
class MatchAttempt:
    """A single attempt at matching."""

    iteration: int
    source_code: str
    score: int
    max_score: int
    match_percent: float
    compiler_output: str = ""
    strategy_used: str = ""


@dataclass
class MatchingContext:
    """Context for a matching session."""

    function_name: str
    file_path: str
    asm: str
    context: str
    current_match: float
    size_bytes: int
    address: str
    scratch_slug: Optional[str] = None
    attempts: list[MatchAttempt] = field(default_factory=list)
    best_attempt: Optional[MatchAttempt] = None
