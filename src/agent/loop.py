"""
Main agent loop for decompilation matching.

This module orchestrates the process of:
1. Selecting a function to match
2. Creating a scratch on decomp.me
3. Iteratively refining the code until a match is achieved
4. Committing the result
"""

import asyncio
from pathlib import Path
from typing import Optional

from anthropic import APIError

from .models import MatchResult, MatchAttempt, MatchingContext
from .strategies import get_strategies_for_diff, Strategy
from .prompts import (
    make_initial_prompt,
    make_refinement_prompt,
    extract_code_from_response,
)
from .llm import LLMClient


async def run_matching_agent(
    function_name: Optional[str] = None,
    melee_root: Path = Path("melee"),
    api_url: str = "http://localhost:8000",
    max_iterations: int = 50,
    auto_commit: bool = False,
    llm_client: Optional[LLMClient] = None,
) -> MatchResult:
    """Run the matching agent loop.

    Args:
        function_name: Specific function to match, or None to auto-select
        melee_root: Path to the melee project root
        api_url: URL of the decomp.me API
        max_iterations: Maximum refinement iterations
        auto_commit: Whether to automatically commit on success
        llm_client: LLM client instance (or None to create a new one)

    Returns:
        MatchResult with the outcome
    """
    # Import here to avoid circular imports
    from src.client import DecompMeAPIClient
    from src.extractor import extract_function, extract_unmatched_functions

    # Initialize clients
    client = DecompMeAPIClient(base_url=api_url)

    # Create LLM client if not provided
    if llm_client is None:
        try:
            llm_client = LLMClient()
        except ValueError as e:
            return MatchResult(
                function_name=function_name or "",
                matched=False,
                best_match=0.0,
                error=f"Failed to initialize LLM client: {e}",
            )

    # Get function to work on
    if function_name:
        func_info = await extract_function(melee_root, function_name)
        if func_info is None:
            return MatchResult(
                function_name=function_name,
                matched=False,
                best_match=0.0,
                error=f"Function '{function_name}' not found",
            )
    else:
        # Auto-select a good candidate
        result = await extract_unmatched_functions(
            melee_root=melee_root,
            include_asm=True,
            include_context=True,
        )
        # Filter by size and match criteria
        functions = [
            f for f in result.functions
            if 50 <= f.size_bytes <= 500
            and 0.3 <= f.current_match <= 0.99
        ]
        if not functions:
            return MatchResult(
                function_name="",
                matched=False,
                best_match=0.0,
                error="No suitable unmatched functions found",
            )
        # Pick the one with highest partial match
        func_info = max(functions, key=lambda f: f.current_match)

    # Create matching context
    ctx = MatchingContext(
        function_name=func_info.name,
        file_path=func_info.file_path,
        asm=func_info.asm,
        context=func_info.context,
        current_match=func_info.current_match,
        size_bytes=func_info.size_bytes,
        address=func_info.address,
    )

    print(f"Starting matching attempt for: {ctx.function_name}")
    print(f"  File: {ctx.file_path}")
    print(f"  Size: {ctx.size_bytes} bytes")
    print(f"  Current match: {ctx.current_match * 100:.1f}%")

    # Create scratch on decomp.me
    try:
        scratch = await client.create_scratch(
            name=ctx.function_name,
            target_asm=ctx.asm,
            context=ctx.context,
            source_code="// Initial placeholder\n",
            diff_label=ctx.function_name,
        )
        ctx.scratch_slug = scratch.slug
        print(f"Created scratch: {api_url}/scratch/{scratch.slug}")
    except Exception as e:
        return MatchResult(
            function_name=ctx.function_name,
            matched=False,
            best_match=0.0,
            error=f"Failed to create scratch: {e}",
        )

    # Try to get initial decompilation from m2c
    try:
        decompiled = await client.decompile_scratch(scratch.slug)
        initial_code = decompiled.decompilation
        print("Got initial m2c decompilation")
    except Exception:
        # Fall back to empty implementation
        initial_code = f"void {ctx.function_name}(void) {{\n    // TODO: implement\n}}"
        print("Using placeholder code (m2c failed)")

    # Main iteration loop
    current_code = initial_code
    best_match = 0.0
    strategies = get_strategies_for_diff({})

    for iteration in range(max_iterations):
        print(f"\n--- Iteration {iteration + 1}/{max_iterations} ---")

        # Update scratch with current code
        try:
            await client.update_scratch(scratch.slug, source_code=current_code)
        except Exception as e:
            print(f"Failed to update scratch: {e}")
            continue

        # Compile and get diff
        try:
            result = await client.compile_scratch(scratch.slug)
        except Exception as e:
            print(f"Compilation failed: {e}")
            continue

        if not result.success:
            print(f"Compilation error: {result.compiler_output[:500]}")
            # Try to fix compilation errors
            # This would involve analyzing the error and making fixes
            continue

        if result.diff_output is None:
            print("No diff output")
            continue

        # Calculate match percentage
        score = result.diff_output.current_score
        max_score = result.diff_output.max_score
        match_pct = 1.0 if score == 0 else (1.0 - score / max_score) if max_score > 0 else 0.0

        print(f"Score: {score}/{max_score} ({match_pct * 100:.1f}% match)")

        # Record attempt
        attempt = MatchAttempt(
            iteration=iteration,
            source_code=current_code,
            score=score,
            max_score=max_score,
            match_percent=match_pct,
            compiler_output=result.compiler_output,
        )
        ctx.attempts.append(attempt)

        # Track best
        if match_pct > best_match:
            best_match = match_pct
            ctx.best_attempt = attempt

        # Check for success
        if score == 0:
            print("\n*** 100% MATCH ACHIEVED! ***")

            pr_url = None
            if auto_commit:
                from src.commit import auto_detect_and_commit

                try:
                    pr_url = await auto_detect_and_commit(
                        function_name=ctx.function_name,
                        new_code=current_code,
                        scratch_id=scratch.slug,
                        scratch_url=f"{api_url}/scratch/{scratch.slug}",
                        melee_root=melee_root,
                        author="agent",
                        create_pull_request=True,
                    )
                except Exception as e:
                    print(f"Failed to commit: {e}")

            return MatchResult(
                function_name=ctx.function_name,
                matched=True,
                best_match=1.0,
                scratch_slug=scratch.slug,
                iterations=iteration + 1,
                pr_url=pr_url,
            )

        # Get next strategy
        strategy = strategies[iteration % len(strategies)] if strategies else None
        strategy_hint = strategy.prompt_hint if strategy else "Try different approaches"

        # Generate refinement prompt
        prompt = make_refinement_prompt(
            function_name=ctx.function_name,
            current_code=current_code,
            score=score,
            max_score=max_score,
            compiler_output=result.compiler_output,
            diff_snippet=str(result.diff_output.rows[:20]),
            strategy_hint=strategy_hint,
        )

        # Call LLM to get refined code
        print(f"Calling LLM for refinement (prompt: {len(prompt)} chars)...")
        try:
            llm_response = await llm_client.generate_code(prompt)
            if llm_response is None:
                print("LLM returned no response, skipping iteration")
                continue

            # Extract code from response
            new_code = extract_code_from_response(llm_response)
            if new_code:
                print(f"Got refined code ({len(new_code)} chars)")
                current_code = new_code
                # Record the strategy used
                if ctx.attempts:
                    ctx.attempts[-1].strategy_used = strategy.name if strategy else "general"
            else:
                print("Could not extract code from LLM response")
                # Still continue - maybe try again with different strategy

        except APIError as e:
            print(f"LLM API error: {e}")
            # On API error, we can't continue effectively
            if "rate_limit" in str(e).lower():
                print("Rate limit hit - stopping iterations")
                break
            # For other API errors, try to continue
            continue
        except Exception as e:
            print(f"Unexpected error during LLM call: {e}")
            # Try to continue with next iteration
            continue

    return MatchResult(
        function_name=ctx.function_name,
        matched=False,
        best_match=best_match,
        scratch_slug=ctx.scratch_slug,
        iterations=len(ctx.attempts),
    )


async def select_best_candidate(
    melee_root: Path,
    min_size: int = 50,
    max_size: int = 500,
) -> Optional[str]:
    """Select the best function candidate for matching.

    Prioritizes:
    1. Functions with high partial match (easier to complete)
    2. Medium-sized functions (not too simple, not too complex)
    3. Functions in well-understood modules

    Args:
        melee_root: Path to melee project
        min_size: Minimum function size
        max_size: Maximum function size

    Returns:
        Function name or None
    """
    from src.extractor import extract_unmatched_functions

    result = await extract_unmatched_functions(
        melee_root=melee_root,
        include_asm=False,
        include_context=False,
    )

    # Filter by size and match criteria
    functions = [
        f for f in result.functions
        if min_size <= f.size_bytes <= max_size
        and 0.3 <= f.current_match <= 0.99
    ]

    if not functions:
        return None

    # Score candidates
    def score_candidate(func):
        score = 0.0
        # Higher partial match is better
        score += func.current_match * 50
        # Prefer medium size
        size_factor = 1.0 - abs(func.size_bytes - 200) / 300
        score += max(0, size_factor) * 20
        # Prefer certain modules (lb, ft are well-understood)
        if "/lb/" in func.file_path or "/ft/" in func.file_path:
            score += 10
        return score

    best = max(functions, key=score_candidate)
    return best.name
