"""Decomp.me API Client.

This module provides a comprehensive Python client for the decomp.me REST API,
including both low-level HTTP operations and high-level scratch management.

Example:
    Basic usage:

    >>> from src.client import DecompMeAPIClient, ScratchManager, ScratchCreate
    >>>
    >>> async with DecompMeAPIClient("http://localhost:8000") as client:
    ...     # Create a scratch
    ...     scratch = await client.create_scratch(
    ...         ScratchCreate(
    ...             name="Player_UpdateFunc",
    ...             target_asm=asm_code,
    ...             diff_label="Player_UpdateFunc",
    ...             compiler="mwcc_247_92",
    ...             compiler_flags="-O4,p -inline auto -nodefaults",
    ...         )
    ...     )
    ...
    ...     # Compile and check
    ...     result = await client.compile_scratch(scratch.slug)
    ...     print(f"Score: {result.score}/{result.max_score}")

    High-level scratch management:

    >>> async with DecompMeAPIClient() as client:
    ...     manager = ScratchManager(client)
    ...
    ...     # Create from assembly
    ...     scratch = await manager.create_from_asm(
    ...         target_asm=asm_code,
    ...         function_name="Player_UpdateFunc",
    ...         context="#include <player.h>",
    ...     )
    ...
    ...     # Iterate on the implementation
    ...     result = await manager.iterate(scratch, new_source_code)
    ...     if result.is_perfect:
    ...         print("Perfect match!")
"""

from .api import DecompMeAPIClient, DecompMeAPIError
from .models import (
    CompilationResult,
    CompileRequest,
    CompilerInfo,
    DecompilationResult,
    DiffOutput,
    DiffRow,
    ForkRequest,
    Library,
    PresetInfo,
    Profile,
    Scratch,
    ScratchCreate,
    ScratchUpdate,
    TerseScratch,
)
from .scratch import ScratchManager

__all__ = [
    # API Client
    "DecompMeAPIClient",
    "DecompMeAPIError",
    # High-level Manager
    "ScratchManager",
    # Models - Request
    "ScratchCreate",
    "ScratchUpdate",
    "CompileRequest",
    "ForkRequest",
    # Models - Response
    "Scratch",
    "TerseScratch",
    "CompilationResult",
    "DecompilationResult",
    "DiffOutput",
    "DiffRow",
    "CompilerInfo",
    "PresetInfo",
    "Library",
    "Profile",
]
