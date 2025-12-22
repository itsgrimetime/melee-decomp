"""
Matching strategies for the decompilation agent.

These strategies encode common patterns and transformations that help
achieve matching decompilations for PowerPC/GameCube code.
"""

from dataclasses import dataclass
from typing import Optional


@dataclass
class Strategy:
    """A decompilation strategy to try."""

    name: str
    description: str
    prompt_hint: str
    priority: int = 0  # Higher = try first


# Common strategies for PowerPC/MWCC matching
STRATEGIES: list[Strategy] = [
    Strategy(
        name="initial_m2c",
        description="Start with m2c decompilation output",
        prompt_hint="Use the m2c decompiler output as a starting point. Clean up variable names and types.",
        priority=100,
    ),
    Strategy(
        name="register_allocation",
        description="Adjust variable declarations to match register allocation",
        prompt_hint="""The MWCC compiler allocates registers in declaration order. Try:
- Reordering local variable declarations
- Splitting or combining variable declarations
- Using explicit register variables if needed""",
        priority=90,
    ),
    Strategy(
        name="loop_structure",
        description="Adjust loop structure to match",
        prompt_hint="""PowerPC loop code generation depends on loop structure:
- Try converting for/while/do-while between each other
- Adjust loop bounds and increment expressions
- Consider loop unrolling or combining""",
        priority=85,
    ),
    Strategy(
        name="inline_functions",
        description="Handle inline function expansion",
        prompt_hint="""MWCC aggressively inlines small functions:
- Check if helper functions should be inlined
- Look for patterns that match known inline functions
- Consider __attribute__((always_inline)) or inline keyword""",
        priority=80,
    ),
    Strategy(
        name="struct_access",
        description="Optimize struct member access patterns",
        prompt_hint="""Struct access affects code generation:
- Try caching struct pointers in local variables
- Adjust order of struct member accesses
- Check struct padding and alignment""",
        priority=75,
    ),
    Strategy(
        name="condition_order",
        description="Reorder conditional expressions",
        prompt_hint="""Condition ordering affects branch generation:
- Swap && and || operand order
- Invert conditions and swap branches
- Try short-circuit vs eager evaluation""",
        priority=70,
    ),
    Strategy(
        name="arithmetic_equivalence",
        description="Use equivalent arithmetic expressions",
        prompt_hint="""Different arithmetic forms generate different code:
- a * 2 vs a << 1 vs a + a
- a / 4 vs a >> 2 (for unsigned)
- Strength reduction patterns""",
        priority=65,
    ),
    Strategy(
        name="cast_adjustment",
        description="Adjust type casts",
        prompt_hint="""Type casts affect instruction selection:
- Add or remove explicit casts
- Use (s32), (u32), (s16), (u16), etc.
- Cast intermediate results in expressions""",
        priority=60,
    ),
    Strategy(
        name="volatile_const",
        description="Adjust volatile/const qualifiers",
        prompt_hint="""Qualifiers affect optimization:
- Try adding volatile to prevent optimization
- Use const for read-only data
- Check if globals need volatile""",
        priority=55,
    ),
    Strategy(
        name="temp_variables",
        description="Add or remove temporary variables",
        prompt_hint="""Temporary variables affect register usage:
- Break complex expressions into temps
- Combine temps back into single expressions
- Name temps to match expected register usage""",
        priority=50,
    ),
    Strategy(
        name="early_return",
        description="Use early returns vs nested conditions",
        prompt_hint="""Return patterns affect code layout:
- Convert nested if/else to early returns
- Or consolidate early returns into nested structure
- Check goto usage for complex control flow""",
        priority=45,
    ),
    Strategy(
        name="literal_format",
        description="Adjust literal formats",
        prompt_hint="""Literal format affects constant loading:
- Float: 1.0f vs 1.0F vs (f32)1.0
- Int: 0x10 vs 16 vs 020
- Use proper suffixes: 1U, 1L, 1UL""",
        priority=40,
    ),
]


def get_strategies_for_diff(diff_info: dict) -> list[Strategy]:
    """Get relevant strategies based on diff analysis.

    Args:
        diff_info: Information about the current diff

    Returns:
        List of strategies sorted by relevance
    """
    # For now, return all strategies sorted by priority
    # Future: analyze diff to prioritize specific strategies
    return sorted(STRATEGIES, key=lambda s: -s.priority)


def analyze_diff_for_hints(diff_rows: list) -> list[str]:
    """Analyze diff output to suggest specific fixes.

    Args:
        diff_rows: Rows from the diff output

    Returns:
        List of specific hints based on the diff
    """
    hints = []

    # This is a placeholder for more sophisticated diff analysis
    # In the future, we could:
    # - Detect register mismatches
    # - Identify instruction pattern differences
    # - Suggest specific transformations

    return hints
