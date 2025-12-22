"""High-level scratch management utilities.

This module provides a convenient interface for working with decomp.me scratches,
including creation, compilation, iteration, and workflow automation.
"""

import logging
from typing import Any

from .api import DecompMeAPIClient
from .models import (
    CompilationResult,
    CompileRequest,
    Scratch,
    ScratchCreate,
    ScratchUpdate,
)

logger = logging.getLogger(__name__)


class ScratchManager:
    """High-level manager for decomp.me scratches.

    This class provides convenient methods for common scratch workflows,
    including iterative development and automated matching.

    Args:
        client: DecompMeAPIClient instance
        default_compiler: Default compiler to use (default: mwcc_247_92 for Melee)
        default_flags: Default compiler flags
    """

    def __init__(
        self,
        client: DecompMeAPIClient,
        default_compiler: str = "mwcc_247_92",
        default_flags: str = "-O4,p -inline auto -nodefaults",
    ):
        self.client = client
        self.default_compiler = default_compiler
        self.default_flags = default_flags

    async def create_from_asm(
        self,
        target_asm: str,
        function_name: str,
        context: str = "",
        source_code: str | None = None,
        name: str | None = None,
        compiler: str | None = None,
        compiler_flags: str | None = None,
        **kwargs: Any,
    ) -> Scratch:
        """Create a new scratch from assembly code.

        This is the most common workflow - provide target assembly and get
        a scratch with initial decompilation.

        Args:
            target_asm: Target assembly code
            function_name: Function name for diff matching
            context: Context code (headers, typedefs, etc.)
            source_code: Initial source code (if None, will auto-decompile)
            name: Scratch name (defaults to function_name)
            compiler: Compiler ID (defaults to default_compiler)
            compiler_flags: Compiler flags (defaults to default_flags)
            **kwargs: Additional ScratchCreate parameters

        Returns:
            Created scratch with initial score

        Example:
            >>> manager = ScratchManager(client)
            >>> scratch = await manager.create_from_asm(
            ...     target_asm=asm_code,
            ...     function_name="Player_UpdateFunc",
            ...     context="#include <player.h>",
            ... )
        """
        scratch_params = ScratchCreate(
            name=name or function_name,
            compiler=compiler or self.default_compiler,
            compiler_flags=compiler_flags or self.default_flags,
            target_asm=target_asm,
            context=context,
            diff_label=function_name,
            source_code=source_code or "",  # Let backend auto-decompile if empty
            **kwargs,
        )

        logger.info(f"Creating scratch for function: {function_name}")
        scratch = await self.client.create_scratch(scratch_params)

        # If we didn't provide source code and backend auto-decompiled,
        # fetch the updated scratch to get the decompilation
        if not source_code:
            scratch = await self.client.get_scratch(scratch.slug)

        logger.info(
            f"Created scratch {scratch.slug} - Score: {scratch.score}/{scratch.max_score}"
        )
        return scratch

    async def iterate(
        self,
        scratch: Scratch,
        new_source: str,
        save: bool = True,
    ) -> CompilationResult:
        """Iterate on a scratch with new source code.

        Args:
            scratch: Scratch to update
            new_source: New source code to try
            save: If True, save the source to the scratch; if False, just compile

        Returns:
            Compilation result with diff

        Example:
            >>> result = await manager.iterate(scratch, new_source)
            >>> if result.is_perfect:
            ...     print("Perfect match!")
        """
        if save:
            logger.info(f"Updating and compiling scratch {scratch.slug}")
            scratch = await self.client.update_scratch(
                scratch.slug,
                ScratchUpdate(source_code=new_source),
            )
            # The update triggers recompilation, so we can get the score from the scratch
            return CompilationResult(
                success=scratch.score >= 0,
                compiler_output="",
                diff_output=None,  # Not included in update response
            )
        else:
            logger.info(f"Compiling scratch {scratch.slug} with temporary changes")
            return await self.client.compile_scratch(
                scratch.slug,
                CompileRequest(source_code=new_source),
                save_score=False,
            )

    async def get_current_score(self, slug: str) -> tuple[int, int]:
        """Get current score for a scratch.

        Args:
            slug: Scratch slug/ID

        Returns:
            Tuple of (current_score, max_score)
        """
        scratch = await self.client.get_scratch(slug)
        return (scratch.score, scratch.max_score)

    async def is_matching(self, slug: str) -> bool:
        """Check if a scratch is matching (score == 0).

        Args:
            slug: Scratch slug/ID

        Returns:
            True if scratch matches perfectly
        """
        score, _ = await self.get_current_score(slug)
        return score == 0

    async def compile_and_check(
        self,
        scratch: Scratch,
        source_code: str | None = None,
    ) -> CompilationResult:
        """Compile a scratch and return detailed results.

        Args:
            scratch: Scratch to compile
            source_code: Optional source code override

        Returns:
            Compilation result with full diff output
        """
        logger.info(f"Compiling scratch {scratch.slug}")

        if source_code:
            compile_req = CompileRequest(source_code=source_code)
            result = await self.client.compile_scratch(
                scratch.slug,
                compile_req,
                save_score=False,
            )
        else:
            # Use GET to save score
            result = await self.client.compile_scratch(
                scratch.slug,
                save_score=True,
            )

        if not result.success:
            logger.warning(f"Compilation failed:\n{result.compiler_output}")
        elif result.is_perfect:
            logger.info(f"Perfect match! Score: {result.score}/{result.max_score}")
        else:
            logger.info(f"Score: {result.score}/{result.max_score}")

        return result

    async def update_flags(
        self,
        scratch: Scratch,
        compiler_flags: str,
    ) -> Scratch:
        """Update compiler flags for a scratch.

        Args:
            scratch: Scratch to update
            compiler_flags: New compiler flags

        Returns:
            Updated scratch
        """
        logger.info(f"Updating compiler flags for {scratch.slug}")
        return await self.client.update_scratch(
            scratch.slug,
            ScratchUpdate(compiler_flags=compiler_flags),
        )

    async def fork_and_modify(
        self,
        slug: str,
        new_source: str | None = None,
        new_name: str | None = None,
        new_flags: str | None = None,
    ) -> Scratch:
        """Fork a scratch and optionally modify it.

        Args:
            slug: Scratch slug/ID to fork
            new_source: New source code
            new_name: New name
            new_flags: New compiler flags

        Returns:
            Forked scratch
        """
        from .models import ForkRequest

        fork_params = ForkRequest()
        if new_source:
            fork_params.source_code = new_source
        if new_name:
            fork_params.name = new_name
        if new_flags:
            fork_params.compiler_flags = new_flags

        logger.info(f"Forking scratch {slug}")
        return await self.client.fork_scratch(slug, fork_params)

    async def get_family(self, scratch: Scratch) -> list[Any]:
        """Get all related scratches for the same function.

        Args:
            scratch: Scratch to find family for

        Returns:
            List of related scratches
        """
        logger.info(f"Fetching family for scratch {scratch.slug}")
        return await self.client.get_scratch_family(scratch.slug)

    async def decompile(
        self,
        scratch: Scratch,
        context: str | None = None,
    ) -> str:
        """Get automatic decompilation for a scratch.

        Args:
            scratch: Scratch to decompile
            context: Optional context override

        Returns:
            Decompiled source code
        """
        logger.info(f"Decompiling scratch {scratch.slug}")
        result = await self.client.decompile_scratch(
            scratch.slug,
            context=context,
        )
        return result.decompilation

    async def batch_compile(
        self,
        scratch: Scratch,
        source_variants: list[str],
    ) -> list[tuple[str, CompilationResult]]:
        """Try multiple source code variants and return results.

        Args:
            scratch: Scratch to compile
            source_variants: List of source code variants to try

        Returns:
            List of (source_code, result) tuples sorted by score
        """
        logger.info(f"Batch compiling {len(source_variants)} variants for {scratch.slug}")

        results: list[tuple[str, CompilationResult]] = []
        for i, source in enumerate(source_variants, 1):
            logger.debug(f"Compiling variant {i}/{len(source_variants)}")
            result = await self.client.compile_scratch(
                scratch.slug,
                CompileRequest(source_code=source),
                save_score=False,
            )
            results.append((source, result))

        # Sort by score (lower is better, -1 means failed)
        results.sort(key=lambda x: (x[1].score if x[1].score >= 0 else float("inf")))

        best_score = results[0][1].score if results else -1
        logger.info(f"Batch compile complete. Best score: {best_score}")

        return results

    async def find_best_flags(
        self,
        scratch: Scratch,
        flag_variants: list[str],
    ) -> tuple[str, int]:
        """Try different compiler flag combinations to find the best score.

        Args:
            scratch: Scratch to optimize
            flag_variants: List of compiler flag combinations to try

        Returns:
            Tuple of (best_flags, best_score)
        """
        logger.info(f"Testing {len(flag_variants)} flag combinations for {scratch.slug}")

        best_flags = scratch.compiler_flags
        best_score = scratch.score

        for flags in flag_variants:
            logger.debug(f"Testing flags: {flags}")
            result = await self.client.compile_scratch(
                scratch.slug,
                CompileRequest(compiler_flags=flags),
                save_score=False,
            )

            if result.success and (best_score < 0 or 0 <= result.score < best_score):
                best_flags = flags
                best_score = result.score
                logger.info(f"New best score: {best_score} with flags: {flags}")

                if best_score == 0:
                    logger.info("Perfect match found!")
                    break

        return best_flags, best_score
