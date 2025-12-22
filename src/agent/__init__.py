"""
Decompilation Agent module.

Contains the main agent loop for attempting to match functions.
"""

from .loop import run_matching_agent, MatchResult

__all__ = ["run_matching_agent", "MatchResult"]
