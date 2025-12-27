# Melee Decomp Agent

Agent tooling for contributing to the Super Smash Bros. Melee decompilation project using a self-hosted [decomp.me](https://decomp.me) instance.

## Overview

This project provides tooling to enable AI agents (and humans) to:
- Extract unmatched functions from the melee decomp project
- Create and iterate on scratches using a local decomp.me instance
- Commit matched source code back to the original decomp project
- Sync completed scratches to production decomp.me
- Coordinate multiple parallel agents working on different functions

## Architecture

```
melee-decomp/
├── melee/                    # Submodule: main decompilation repo (doldecomp/melee)
│   ├── src/melee/            # Decompiled C source files
│   ├── asm/                   # PowerPC assembly files
│   ├── include/               # Header files
│   ├── config/GALE01/         # Build configuration
│   └── build/                 # Build outputs (ctx.c, report.json)
│
├── src/                       # Main agent tooling source
│   ├── cli/                   # Typer CLI commands
│   │   ├── __init__.py        # Main app, registers all sub-apps
│   │   ├── extract.py         # Function discovery (list, get)
│   │   ├── scratch.py         # Scratch management (create, compile, update)
│   │   ├── claim.py           # Agent coordination (add, release, list)
│   │   ├── complete.py        # Completion tracking (mark, list)
│   │   ├── commit.py          # Apply matches to repo (apply, format)
│   │   ├── sync.py            # Production sync (auth, list, production)
│   │   └── _common.py         # Shared utilities, paths, worktree management
│   ├── client/                # decomp.me API client
│   │   ├── api.py             # Async HTTP client with session management
│   │   ├── models.py          # Pydantic models (Scratch, CompilationResult)
│   │   └── scratch.py         # Scratch-specific helpers
│   ├── extractor/             # Extract functions from melee repo
│   │   ├── extractor.py       # Main FunctionExtractor class
│   │   ├── parser.py          # Parse configure.py for object status
│   │   ├── symbols.py         # Parse symbol maps
│   │   ├── asm.py             # Extract assembly from .s files
│   │   ├── context.py         # Generate decompilation context
│   │   └── splits.py          # Parse splits.txt for address ranges
│   └── commit/                # Commit workflow
│       ├── workflow.py        # Full commit workflow orchestration
│       ├── update.py          # Update source files with new code
│       ├── configure.py       # Update configure.py status
│       ├── format.py          # Run clang-format
│       └── pr.py              # Create pull requests
│
├── decomp-me-mcp/             # MCP server for Claude Desktop integration
├── decomp.me/                 # Submodule: self-hosted decomp.me instance
├── docker/                    # Docker setup for local decomp.me
│   ├── setup.sh               # Start single instance
│   ├── start-workers.sh       # Start parallel instances
│   └── docker-compose.yml     # Service definitions
├── melee-worktrees/           # Per-agent git worktrees (auto-created)
├── config/                    # Project configuration
│   └── scratches_slug_map.json  # Local→production slug mapping
└── pyproject.toml             # Python project config
```

## Installation

### Prerequisites

- Python 3.11+
- Docker (for local decomp.me instance)
- Git

### Setup

```bash
# Clone with submodules
git clone --recursive https://github.com/your-username/melee-decomp
cd melee-decomp

# Install Python package
pip install -e .

# Start local decomp.me instance
cd docker && ./setup.sh
```

## CLI Reference

All commands available via `melee-agent` or `python -m src.cli`:

### Function Discovery

```bash
# List unmatched functions (default: <99% match, excludes completed)
melee-agent extract list

# Filter by match percentage
melee-agent extract list --max-match 0.50 --min-match 0.0

# Only show functions that can be committed (in Matching files)
melee-agent extract list --matching-only

# Get details for a specific function
melee-agent extract get <function_name>

# Get function and create scratch in one step
melee-agent extract get <function_name> --create-scratch
```

### Scratch Operations

```bash
# Create a scratch from function in melee repo
melee-agent scratch create <function_name>

# Get scratch details
melee-agent scratch get <slug>

# Update scratch source from file (use unique filename per scratch)
melee-agent scratch update <slug> /tmp/decomp_<slug>.c

# Compile and show match percentage
melee-agent scratch compile <slug>

# Compile with instruction diff
melee-agent scratch compile <slug> --diff

# Search scratches
melee-agent scratch search "item"
melee-agent scratch search --platform gc_wii --limit 20

# Search within scratch context (headers)
melee-agent scratch search-context <slug> "struct Item"
```

### Agent Coordination

```bash
# Claim a function before working on it
melee-agent claim add <function_name>

# Release a claim when done
melee-agent claim release <function_name>

# List all active claims
melee-agent claim list
```

### Completion Tracking

```bash
# Mark a function as completed
melee-agent complete mark <function_name> <slug> <match_percent>

# Mark as committed to repo
melee-agent complete mark <function_name> <slug> 100.0 --committed

# List completed functions
melee-agent complete list
melee-agent complete list --min-match 95.0
```

### Commit Workflow

```bash
# Preview changes (dry run)
melee-agent commit apply <function_name> <slug> --dry-run

# Apply matched function to melee repo
melee-agent commit apply <function_name> <slug>

# Apply and create PR
melee-agent commit apply <function_name> <slug> --pr

# Force commit below 95% threshold
melee-agent commit apply <function_name> <slug> --force

# Run clang-format on staged changes
melee-agent commit format
```

### Production Sync

```bash
# Configure Cloudflare authentication
melee-agent sync auth

# Check auth status
melee-agent sync status

# List functions ready to sync
melee-agent sync list --min-match 95.0

# Sync to production decomp.me
melee-agent sync production

# View slug mappings
melee-agent sync slugs
```

### Utilities

```bash
# List available compilers
melee-agent compilers
```

## Key Files

| Location | Purpose |
|----------|---------|
| `~/.config/decomp-me/cookies_{agent_id}.json` | Per-agent session cookies |
| `~/.config/decomp-me/completed_functions.json` | Shared completion tracking |
| `~/.config/decomp-me/production_cookies.json` | Production decomp.me auth |
| `/tmp/decomp_claims.json` | Ephemeral agent claims (1-hour expiry) |
| `config/scratches_slug_map.json` | Local→production slug mapping |
| `melee/build/ctx.c` | Build context (preprocessed headers) |
| `melee/build/GALE01/report.json` | Function match percentages |

## Environment Variables

```bash
# Local decomp.me instance URL (defaults to hostname-based local access)
DECOMP_ME_URL=http://nzxt-discord.local

# Or via VPN for remote access
DECOMP_ME_URL=http://10.200.0.1

# API base (alternative to DECOMP_ME_URL)
DECOMP_API_BASE=http://localhost:8000

# Manual agent ID (auto-detected from Claude process if not set)
DECOMP_AGENT_ID=agent-1
```

## Agent Workflow

### Standard Decompilation Flow

1. **Find a function to work on**
   ```bash
   melee-agent extract list --max-match 0.50 --matching-only
   ```

2. **Claim the function** (prevents duplicate work)
   ```bash
   melee-agent claim add <function_name>
   ```

3. **Create a scratch**
   ```bash
   melee-agent scratch create <function_name>
   ```

4. **Read existing code** for context
   - Check `melee/src/melee/` for related files
   - Look at similar functions in the same file

5. **Iterate on the decompilation**
   ```bash
   # Write code to temp file (use slug in filename to avoid conflicts)
   echo 'void MyFunc(void) { ... }' > /tmp/decomp_<slug>.c

   # Update and compile
   melee-agent scratch update <slug> /tmp/decomp_<slug>.c
   melee-agent scratch compile <slug> --diff
   ```

6. **When ready (95%+ match), commit**
   ```bash
   # Preview first
   melee-agent commit apply <function_name> <slug> --dry-run

   # Apply for real
   melee-agent commit apply <function_name> <slug>
   ```

7. **Mark as complete**
   ```bash
   melee-agent complete mark <function_name> <slug> 100.0 --committed
   ```

8. **Release the claim**
   ```bash
   melee-agent claim release <function_name>
   ```

### Multi-Agent Coordination

When running multiple Claude instances in parallel:

- Each agent automatically gets isolated:
  - Separate git worktree at `melee-worktrees/{agent_id}/`
  - Separate session cookies
  - Separate git branch (`agent/{agent_id}`)

- Claims use file locking to prevent race conditions

- Completed functions are tracked in a shared file with atomic updates

### Match Thresholds

- **95%+**: Ready to commit (minor register/offset differences OK)
- **100%**: Perfect match, ideal for submission

## Building Melee

The melee submodule must be built to generate required files:

```bash
cd melee
python configure.py
ninja

# This generates:
# - build/ctx.c (decompilation context)
# - build/GALE01/report.json (match percentages)
# - asm/ files from original ROM
```

## Docker Setup

### Single Instance

```bash
cd docker
./setup.sh

# Access at http://localhost (or configured hostname)
```

### Multiple Parallel Instances

```bash
# Start 3 worker instances
./start-workers.sh 3

# Worker URLs:
# - http://localhost:8001
# - http://localhost:8002
# - http://localhost:8003

# Check status
./status.sh

# Stop workers
./stop-workers.sh 3
```

## MCP Server (Claude Desktop)

The `decomp-me-mcp/` directory contains an MCP server for Claude Desktop integration:

```json
{
  "mcpServers": {
    "decomp": {
      "command": "/path/to/decomp-me-mcp/.venv/bin/python",
      "args": ["-m", "decomp_mcp.server"]
    }
  }
}
```

This provides tools like:
- `decomp_get_scratch` - Fetch scratch details
- `decomp_compile` - Compile and get diff
- `decomp_search` - Search scratches
- `decomp_search_context` - Search header files

## Compiler Settings

Default compiler for Melee (GameCube):
- **Compiler**: `mwcc_233_163n` (Metrowerks CodeWarrior)
- **Flags**: `-O4,p -nodefaults -fp hard -Cpp_exceptions off -enum int -fp_contract on -inline auto`
- **Platform**: `gc_wii`

## Development

```bash
# Install dev dependencies
pip install -e ".[dev]"

# Run tests
pytest

# Type checking
ruff check src/
```

## Resources

- [decomp.me](https://decomp.me) - Decompilation collaboration platform
- [doldecomp/melee](https://github.com/doldecomp/melee) - Main Melee decomp repo
- [MCP Documentation](https://modelcontextprotocol.io) - Model Context Protocol
