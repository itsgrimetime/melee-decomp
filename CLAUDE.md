# Melee Decompilation Project

Reverse-engineering Super Smash Bros. Melee (GameCube) to matching C code using a self-hosted decomp.me instance.

## Architecture

```
melee-decomp/
├── melee/                    # Submodule: main decompilation repo
│   ├── src/melee/            # Decompiled C source files
│   ├── config/GALE01/        # Build config
│   └── tools/                # Build tools (ninja, dtk)
├── src/
│   ├── cli.py                # Main CLI (melee-agent)
│   ├── client/               # decomp.me API client
│   ├── commit/               # Commit workflow (apply matches to repo)
│   └── extractor/            # Extract functions from melee repo
├── decomp-me-mcp/            # MCP server for Claude integration
├── decomp.me/                # Self-hosted decomp.me (submodule)
├── docker/                   # Docker setup for local instance
└── config/                   # Project config (slug maps, etc.)
```

## Key Files

| Location | Purpose |
|----------|---------|
| `~/.config/decomp-me/agent_state.db` | SQLite database (primary state storage) |
| `~/.config/decomp-me/` | Persistent config (cookies, tokens, legacy JSON) |
| `config/scratches_slug_map.json` | Local→production slug mapping |

## CLI Commands

All operations via `python -m src.cli` or `melee-agent`:

```bash
# Scratch operations
melee-agent scratch create <func>     # Create scratch from melee repo
melee-agent scratch get <slug>        # Fetch scratch details
melee-agent scratch compile <slug> -s <file>  # Update + compile in one step
melee-agent scratch compile <slug>    # Compile and diff

# Function discovery
melee-agent extract list --max-match 0.50  # Find unmatched functions
melee-agent extract get <func>             # Get ASM + metadata

# Commit workflow
melee-agent commit apply <func> <slug>     # Apply match to repo
melee-agent commit apply <func> <slug> --dry-run  # Preview first

# Agent coordination
melee-agent claim add <func>          # Claim before working
melee-agent claim release <func>      # Release when done
melee-agent complete mark <func> <slug> <pct>  # Record completion

# Stub management (for missing stub markers)
melee-agent stub check <func>         # Check if stub exists
melee-agent stub add <func>           # Add missing stub marker

# Worktree management (parallel agents)
melee-agent worktree list             # Show all agent worktrees
melee-agent worktree prune --force    # Clean up merged worktrees
melee-agent worktree collect          # Batch commits into PR branch

# Sync to production decomp.me
melee-agent sync auth                 # Configure cf_clearance cookie
melee-agent sync list --author <name> # List scratches to sync
melee-agent sync production           # Sync to https://decomp.me

# State management (SQLite database)
melee-agent state status              # Show tracked functions by category
melee-agent state status <func>       # Show specific function details
melee-agent state urls <func>         # Show all URLs (scratch, PR)
melee-agent state history <func>      # Show audit history
melee-agent state agents              # Show active agents and work
melee-agent state stale               # Show data needing refresh
melee-agent state validate --fix      # Validate DB against sources

# Audit and discovery
melee-agent audit discover-prs        # Link functions to merged PRs
```

## Environment

The local decomp.me server URL is **auto-detected** by probing candidate URLs in order:
1. `nzxt-discord.local` (home network)
2. `10.200.0.1` (WireGuard VPN)
3. `localhost:8000` (local dev)

Override with environment variables if needed:
```bash
DECOMP_API_BASE=http://custom-server      # Override auto-detection
DECOMP_AGENT_ID=agent-1                   # Optional: manual agent isolation
```

## Workflow

1. **Find function**: `extract list` or user-specified
2. **Claim it**: `claim add <func>` (prevents duplicate work)
3. **Create scratch**: `extract get <func> --create-scratch`
4. **Read source**: Check `melee/src/` for existing code + context
5. **Iterate**: Write to `/tmp/decomp_<slug>.c`, `scratch compile <slug> -s /tmp/decomp_<slug>.c`
6. **Finish at 95%+**: `workflow finish <func> <slug>` (commits + records in one step)
7. **Check progress**: `state status` to see all tracked functions by category

## Skills

- `/decomp [func]` - Full decompilation matching workflow (see `.claude/skills/decomp/SKILL.md`)

## Build

```bash
cd melee && python configure.py && ninja  # Build melee
docker compose -f docker/docker-compose.yml up -d  # Local decomp.me
```

## Notes

- Compiler: `mwcc_247_92` with `-O4,p -inline auto -nodefaults`
- Platform: `gc_wii` (GameCube/Wii PowerPC)
- Match threshold: 95%+ with only register/offset diffs is commit-ready
- Always include file-local structs in scratch source (not in headers)
