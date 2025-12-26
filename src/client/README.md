# Decomp.me API Client

A comprehensive Python client for the decomp.me REST API, designed for the Melee decompilation project.

## Overview

This module provides:

1. **Low-level HTTP client** (`api.py`) - Direct API endpoint access with retry logic
2. **Pydantic models** (`models.py`) - Type-safe request/response models
3. **High-level manager** (`scratch.py`) - Convenient workflow automation
4. **Full exports** (`__init__.py`) - Clean public API

## Installation

Dependencies are already included in the project's `pyproject.toml`:

- `httpx>=0.27.0` - Async HTTP client with retry support
- `pydantic>=2.0` - Data validation and serialization

## Quick Start

### Basic Usage

```python
import asyncio
from src.client import DecompMeAPIClient, ScratchCreate

async def main():
    async with DecompMeAPIClient("http://localhost:8000") as client:
        # Create a scratch
        scratch = await client.create_scratch(
            ScratchCreate(
                name="Player_UpdateFunc",
                target_asm=asm_code,
                diff_label="Player_UpdateFunc",
                compiler="mwcc_247_92",
                compiler_flags="-O4,p -inline auto -nodefaults",
            )
        )

        # Compile and check score
        result = await client.compile_scratch(scratch.slug)
        print(f"Score: {result.score}/{result.max_score}")

asyncio.run(main())
```

### High-Level Workflow

```python
from src.client import DecompMeAPIClient, ScratchManager

async with DecompMeAPIClient() as client:
    manager = ScratchManager(client)

    # Create from assembly with auto-decompilation
    scratch = await manager.create_from_asm(
        target_asm=asm_code,
        function_name="Player_UpdateFunc",
        context="#include <player.h>",
    )

    # Iterate on implementation
    result = await manager.iterate(scratch, new_source_code)
    if result.is_perfect:
        print("Perfect match!")
```

## API Reference

### DecompMeAPIClient

Low-level HTTP client for direct API access.

#### Constructor

```python
DecompMeAPIClient(
    base_url: str = "http://localhost:8000",
    timeout: float = 30.0,
    max_retries: int = 3
)
```

#### Scratch CRUD

- `create_scratch(scratch: ScratchCreate) -> Scratch`
- `get_scratch(slug: str) -> Scratch`
- `update_scratch(slug: str, updates: ScratchUpdate) -> Scratch`
- `delete_scratch(slug: str) -> None`
- `list_scratches(...) -> list[TerseScratch]`

#### Compilation & Decompilation

- `compile_scratch(slug: str, overrides: CompileRequest | None, save_score: bool) -> CompilationResult`
- `decompile_scratch(slug: str, context: str | None, compiler: str | None) -> DecompilationResult`

#### Scratch Management

- `fork_scratch(slug: str, fork_params: ForkRequest | None) -> Scratch`
- `get_scratch_family(slug: str) -> list[TerseScratch]`
- `claim_scratch(slug: str, token: str) -> bool`

#### Utilities

- `list_compilers() -> list[CompilerInfo]`
- `list_presets() -> list[PresetInfo]`
- `export_scratch(slug: str, target_only: bool) -> bytes`

### ScratchManager

High-level manager for common workflows.

#### Constructor

```python
ScratchManager(
    client: DecompMeAPIClient,
    default_compiler: str = "mwcc_247_92",
    default_flags: str = "-O4,p -inline auto -nodefaults"
)
```

#### Workflow Methods

- `create_from_asm(...)` - Create scratch from assembly with auto-decompilation
- `iterate(scratch, new_source, save)` - Iterate on implementation
- `compile_and_check(scratch, source_code)` - Compile with detailed results
- `batch_compile(scratch, source_variants)` - Try multiple variants
- `find_best_flags(scratch, flag_variants)` - Optimize compiler flags
- `fork_and_modify(...)` - Fork and modify a scratch
- `get_family(scratch)` - Get related scratches
- `decompile(scratch, context)` - Get automatic decompilation

## Models

### Request Models

- **ScratchCreate** - Create new scratch
  - `name`, `compiler`, `platform`, `compiler_flags`, `diff_flags`
  - `source_code`, `target_asm`, `context`, `diff_label`
  - `libraries`, `preset`

- **ScratchUpdate** - Update scratch fields
  - All fields optional
  - Only provided fields are updated

- **CompileRequest** - Compile with overrides
  - Temporary compilation without saving
  - Override any compilation parameter

- **ForkRequest** - Fork scratch with changes
  - `name`, `source_code`, `compiler_flags`

### Response Models

- **Scratch** - Full scratch details
  - Metadata: `slug`, `name`, `creation_time`, `last_updated`
  - Configuration: `compiler`, `platform`, `compiler_flags`, `diff_flags`
  - Code: `source_code`, `context`, `diff_label`
  - Scoring: `score`, `max_score`, `match_override`
  - Relations: `parent`, `owner`, `claim_token`

- **TerseScratch** - Minimal scratch info for listings

- **CompilationResult** - Compilation output
  - `success` - Whether compilation succeeded
  - `compiler_output` - Errors/warnings
  - `diff_output` - Detailed diff comparison
  - Properties: `score`, `max_score`, `is_perfect`

- **DiffOutput** - Diff comparison details
  - `arch_str` - Architecture
  - `current_score`, `max_score` - Scoring
  - `rows` - List of diff rows

## Configuration

### Base URL

The client defaults to `http://localhost:8000`. Override for different environments:

```python
# Local development
client = DecompMeAPIClient("http://localhost:8000")

# Production (if self-hosted)
client = DecompMeAPIClient("https://decomp.me")
```

### Compiler Defaults

For Melee decompilation, the defaults are:

- **Compiler**: `mwcc_247_92` (MetroWerks CodeWarrior 2.47, build 92)
- **Platform**: `gc_wii` (GameCube/Wii)
- **Flags**: `-O4,p -inline auto -nodefaults`

These match the original Melee build configuration.

### Retry Logic

The client automatically retries transient failures (network errors, 5xx responses):

- **Default retries**: 3
- **Timeout**: 30 seconds per request
- Configurable via constructor

## Error Handling

All API errors raise `DecompMeAPIError`:

```python
from src.client import DecompMeAPIClient, DecompMeAPIError

try:
    scratch = await client.get_scratch("invalid-slug")
except DecompMeAPIError as e:
    print(f"API error: {e}")
```

## Examples

See `examples/client_usage.py` for comprehensive examples:

1. Basic scratch creation and compilation
2. High-level ScratchManager usage
3. Iterative development workflow
4. Compiler flag optimization
5. Working with families and forks

## API Endpoints Reference

Based on decomp.me backend at `decomp.me/backend/`:

### Scratch Operations

- `POST /api/scratch` - Create scratch
- `GET /api/scratch/{slug}` - Get scratch
- `PUT /api/scratch/{slug}` - Update scratch
- `DELETE /api/scratch/{slug}` - Delete scratch
- `GET /api/scratch` - List scratches (with filters)

### Compilation & Decompilation

- `GET /api/scratch/{slug}/compile` - Compile and save score
- `POST /api/scratch/{slug}/compile` - Compile with overrides (no save)
- `POST /api/scratch/{slug}/decompile` - Auto-decompile with m2c

### Scratch Management

- `POST /api/scratch/{slug}/fork` - Fork scratch
- `GET /api/scratch/{slug}/family` - Get related scratches
- `POST /api/scratch/{slug}/claim` - Claim ownership
- `GET /api/scratch/{slug}/export` - Export as ZIP

### Utilities

- `GET /api/compiler` - List compilers
- `GET /api/preset` - List presets

## Development

### Type Checking

All models use Pydantic v2 for runtime validation:

```python
from src.client import ScratchCreate

# This will raise ValidationError
scratch = ScratchCreate(
    name=123,  # Error: expected str
    compiler="invalid",  # Will be caught by API
)
```

### Logging

The client uses Python's `logging` module:

```python
import logging

# Enable debug logging
logging.basicConfig(level=logging.DEBUG)
```

## Architecture Notes

### Why Async?

The client is fully async to:
1. Support concurrent operations (batch compile, etc.)
2. Integrate with async agent frameworks
3. Enable non-blocking I/O for better performance

### Retry Strategy

Uses `httpx.AsyncHTTPTransport(retries=N)` for automatic retries on:
- Connection errors
- Timeouts
- 5xx server errors

Does NOT retry on:
- 4xx client errors (bad request, not found, etc.)
- Successful responses

### Model Design

Pydantic models are designed to:
- Match backend serializers exactly
- Support partial updates (optional fields)
- Provide helpful properties (`is_perfect`, etc.)
- Allow extra fields for forward compatibility

## Testing

Basic syntax validation:

```bash
python -m py_compile src/client/*.py
```

Run examples (requires running backend):

```bash
python examples/client_usage.py
```

## License

Part of the melee-decomp-agent project.
