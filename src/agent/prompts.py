"""
Prompts for the decompilation agent.

These prompts guide the LLM in understanding and fixing decompilation issues.
"""

SYSTEM_PROMPT = """You are an expert decompilation assistant specializing in PowerPC assembly
and the Metrowerks CodeWarrior compiler (MWCC) for Nintendo GameCube.

Your task is to help match C source code to original assembly, achieving byte-for-byte
identical compilation output.

Key knowledge:
- MWCC has specific code generation patterns for loops, conditionals, and function calls
- Register allocation follows declaration order
- The compiler performs various optimizations that can be controlled through code structure
- Small changes in C code can result in different instruction sequences

When analyzing diffs:
1. Look at instruction differences carefully
2. Consider what C constructs would generate each instruction
3. Think about register allocation and spilling
4. Consider compiler optimizations and how to trigger/prevent them

Always explain your reasoning and provide specific, actionable suggestions.
"""


def make_initial_prompt(function_name: str, asm: str, context_snippet: str) -> str:
    """Create the initial prompt for starting decompilation."""
    return f"""I need to decompile the function `{function_name}` from Super Smash Bros. Melee.

Here is the target assembly (PowerPC):
```asm
{asm}
```

Here is some context from the codebase (types and declarations):
```c
{context_snippet[:4000]}  // truncated for brevity
```

Please analyze this assembly and provide an initial C implementation that you think
will match. Focus on:
1. Understanding the function's purpose from the assembly
2. Identifying the parameter types and return type
3. Recognizing common patterns (loops, conditionals, function calls)
4. Using appropriate types (s32, u32, f32, etc. as per the codebase conventions)

Provide your C code implementation.
"""


def make_refinement_prompt(
    function_name: str,
    current_code: str,
    score: int,
    max_score: int,
    compiler_output: str,
    diff_snippet: str,
    strategy_hint: str,
) -> str:
    """Create a prompt for refining an existing attempt."""
    match_pct = (1.0 - score / max_score) * 100 if max_score > 0 else 0

    return f"""The current decompilation of `{function_name}` is at {match_pct:.1f}% match.

Current score: {score} differing instructions out of {max_score} total.

Current C code:
```c
{current_code}
```

{f"Compiler output: {compiler_output}" if compiler_output else ""}

Diff analysis (showing instruction differences):
```
{diff_snippet[:2000]}
```

Strategy suggestion: {strategy_hint}

Please analyze the diff and suggest specific changes to improve the match.
Focus on the differing instructions and what C code changes would fix them.

Provide your updated C code implementation.
"""


def make_analysis_prompt(diff_rows: list, current_code: str) -> str:
    """Create a prompt for analyzing a specific diff."""
    # Format diff rows for display
    diff_text = "\n".join(
        f"{row.get('base', {}).get('text', '')} | {row.get('current', {}).get('text', '')}"
        for row in diff_rows[:50]  # Limit rows
    )

    return f"""Analyze these assembly differences:

Target (left) vs Current (right):
```
{diff_text}
```

Current C code:
```c
{current_code}
```

What specific changes to the C code would make the current output match the target?
Consider:
- Register allocation differences
- Instruction ordering
- Optimization differences
- Type/cast issues
"""


def extract_code_from_response(response: str) -> str | None:
    """Extract C code from an LLM response.

    Args:
        response: The full LLM response

    Returns:
        Extracted C code or None if not found
    """
    import re

    # Look for code blocks
    code_block_pattern = r"```(?:c|cpp)?\n(.*?)```"
    matches = re.findall(code_block_pattern, response, re.DOTALL)

    if matches:
        # Return the last code block (usually the final implementation)
        return matches[-1].strip()

    # If no code block, try to find function-like content
    func_pattern = r"(?:static\s+)?(?:inline\s+)?(?:\w+\s+)+\w+\s*\([^)]*\)\s*\{[^}]+\}"
    matches = re.findall(func_pattern, response, re.DOTALL)

    if matches:
        return matches[-1].strip()

    return None
