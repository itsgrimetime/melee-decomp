# Comparison: lukechampine's vs Our Melee Decomp Approach

A comparison of the Claude skills and tooling between [lukechampine/melee](https://github.com/lukechampine/melee/tree/claude-skills/.claude/skills) and this repository.

## Architecture & Tooling

| Aspect | lukechampine's Approach | Our Approach |
|--------|------------------------|--------------|
| **Infrastructure** | Fully local (scripts + ninja) | Self-hosted decomp.me server + CLI (`melee-agent`) |
| **Compilation** | `checkdiff.py` against ninja build | decomp.me scratch API via `melee-agent scratch compile` |
| **State tracking** | Ninja-based (`ninja baseline`, `ninja changes_all`) | SQLite database at `~/.config/decomp-me/agent_state.db` |
| **Decompiler** | m2c via `decomp.py` with CLI flags | m2c integrated in `melee-agent extract get --create-scratch` |

## Multi-Agent Coordination

| Aspect | lukechampine's | Ours |
|--------|----------------|------|
| **Isolation** | None (single-agent workflow) | Subdirectory worktrees (`melee-worktrees/dir-lb/`, etc.) |
| **Locking** | Not present | `melee-agent claim add/release` system |
| **PR batching** | Not mentioned | `/collect-for-pr` skill for batching commits |
| **Agent tracking** | None | `melee-agent state agents` shows active agents |

## Skill Comparison

### lukechampine's Skills (6 total)

1. **melee-decomp** - Core matching workflow with `decomp.py` + `checkdiff.py`
2. **decomp-progress** - Simple progress metrics via `ninja baseline/changes_all`
3. **easy-funcs** - Function finder with byte-size and address filtering
4. **item-decomp** - Domain knowledge for item code (ItemVars unions, attributes)
5. **mismatch-db** - Pattern database for common assembly mismatches
6. **opseq** - Opcode sequence search to find similar functions

### Our Skills (6 total)

1. **decomp** - Full matching workflow with server integration
2. **decomp-fixup** - Build issue resolution (headers, signatures)
3. **decomp-permuter** - Automated code variation search for 95%+ matches
4. **understand** - Naming/documentation for reverse engineering
5. **collect-for-pr** - PR batching from worktrees
6. **melee-debug** - Dolphin runtime debugging (experimental)

## Notable Unique Features

### lukechampine has that we don't

- **mismatch-db**: A knowledge base of common assembly mismatch patterns (stack size issues, struct copying quirks). This is a practical "cookbook" of known problems and solutions.
- **opseq**: Searches for functions by opcode patterns (`beq,mr,bl`) to find similar already-decompiled code for reference. Useful for learning patterns.
- **easy-funcs**: Targeted function finder by byte size and address range for finding quick wins.
- **item-decomp**: Deep domain knowledge for item-specific conventions (ItemVars union handling, attribute void pointers).

### We have that lukechampine doesn't

- **Worktree isolation**: Enables parallel agent work on different subdirectories without conflicts.
- **decomp-permuter**: Automated brute-force search for 95%+ stuck matches using decomp-permuter tool.
- **melee-debug**: Runtime debugging via Dolphin emulator for verification.
- **Server-based scratches**: Persistent scratches with history tracking, context management, and sync to production decomp.me.
- **State database**: Full audit trail of function progress, claims, and completion status.
- **PR workflow**: Automated PR creation with CI feedback monitoring.

## Workflow Philosophy

### lukechampine's approach

- Simpler, more local - everything runs via ninja and Python scripts
- "5 meaningful attempts max then abandon" - strict iteration limits
- Progress measured by ninja's `changes_all` reporting
- No external dependencies (no server needed)

### Our approach

- Server-centric with decomp.me as the source of truth
- Rich state tracking enables multi-agent orchestration
- Worktree isolation enables parallel development
- More complex infrastructure but supports team/agent coordination

## Knowledge Management

### lukechampine's mismatch-db

A standout feature - it catalogs specific diff patterns:
- Stack size problems (wrong `stwu r1` offset)
- Struct copy inefficiencies (field-by-field vs whole-struct)

This is explicit knowledge capture that we handle more implicitly in our `/decomp` skill's troubleshooting section.

### Our melee-re integration

Provides reference materials (symbol maps, struct docs, character IDs) that isn't mentioned in lukechampine's skills.

## Summary

lukechampine's approach is **leaner and more self-contained** - good for single-agent work with useful domain-specific knowledge bases (mismatch-db, item-decomp, opseq). The opseq tool for finding similar functions is particularly clever.

Our approach is **more infrastructure-heavy but coordination-aware** - designed for parallel agent work with worktree isolation, claims, state tracking, and PR batching. The decomp-permuter and melee-debug skills add capabilities for stuck matches and runtime verification.

## Ideas to Potentially Adopt

The most valuable ideas from lukechampine's approach:

1. **mismatch-db pattern** - Explicit catalog of known assembly mismatch patterns
2. **opseq search** - Finding similar functions by opcode patterns
3. **Domain-specific skills** (like item-decomp) - Deep knowledge for specific code areas
