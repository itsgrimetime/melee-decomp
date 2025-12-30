---
name: understand
description: Document and name functions, structs, and fields in Melee decompilation. Use for improving readability, discovering function purposes, and naming unknown fields. Invoked with /understand <target> where target is a function, file, or struct name.
---

# Melee Code Understanding

You are an expert at reverse engineering Super Smash Bros. Melee code to discover the purpose of functions, structs, and data. Your goal is to improve human readability by naming things appropriately and adding documentation.

## When to Use This Skill

Use `/understand` when you want to:
- Name an address-based function (e.g., `Module_80031790` → `Module_DescriptiveName`)
- Document what a function does with `@brief`, `@param`, `@return`
- Name unknown struct fields (e.g., `unk45` → `damage_multiplier`)
- Understand a module or file's overall purpose

Use `/decomp` instead when:
- You need to match assembly (achieve byte-identical compilation)
- You're iterating on code to reduce diff percentage

## Target Types

This skill supports three target types:

| Invocation | Description |
|------------|-------------|
| `/understand <func_name>` | Analyze and document a single function |
| `/understand <file_path>` | Analyze all functions in a file or module |
| `/understand <StructName>` | Analyze struct field usage and naming |

## Subdirectory Worktrees

The project uses **subdirectory-based worktrees** for parallel agent work, just like `/decomp`. Each source subdirectory gets its own isolated worktree.

**Worktree mapping:**
```
melee/src/melee/lb/*.c     → melee-worktrees/dir-lb/
melee/src/melee/gr/*.c     → melee-worktrees/dir-gr/
melee/src/melee/ft/chara/ftFox/*.c → melee-worktrees/dir-ft-chara-ftFox/
```

**Key points:**
- Source file is **auto-detected** when you claim a function
- Work in the worktree, not the main `melee/` directory
- Commits stay isolated until collected via `/collect-for-pr`
- Claims prevent conflicts with other agents

## Workflow

### Step 1: Claim the Target

**For functions:** Claim before starting work to prevent conflicts:
```bash
melee-agent claim add <func_name>
# → Auto-detects source file
# → Locks the subdirectory worktree
# → Shows worktree path to use
```

**For files/modules:** Claim any function in the file to lock the subdirectory:
```bash
# Find a function in the target file
grep "^void\|^s32" melee/src/melee/<module>/<file>.c | head -1
melee-agent claim add <any_func_in_file>
```

**For structs:** Claim a function that uses the struct heavily.

> **CRITICAL:** Once you claim, work on THAT target. Don't switch mid-workflow.

### Step 2: Identify and Gather Context

**For functions:**
```bash
# Get function metadata and location
melee-agent extract get <func_name>

# If a scratch exists, view it for assembly context
melee-agent scratch get <slug>
```

**For files:** Read from the worktree path shown after claiming.

**For structs:**
```bash
# Find the struct definition
melee-agent struct show <name>

# Look up field at specific offset
melee-agent struct offset 0x50
```

### Step 3: Gather Cross-References

**Find callers** (who calls this function?):
```bash
grep -rn "func_name(" melee/src/melee/
```

**Find callees** (what does this function call?):
Read the function body and note all function calls.

**Find struct usages**:
```bash
grep -rn "StructName" melee/src/melee/
grep -rn "->field_name" melee/src/melee/
```

### Step 3: Analyze Patterns

Ask yourself:
- **Arguments:** What values are passed to this function? What types?
- **Return value:** How is the return value used by callers?
- **Context:** What similar functions exist in this module?
- **Game behavior:** What Melee game feature does this relate to?
- **Field access:** What offsets are accessed? What values are stored?

### Step 4: Research Game Knowledge

Look up Melee game information to aid naming:
- **Characters:** Moves, abilities, state machines (Fox's shine, Marth's dancing blade)
- **Items:** Names and behaviors (Pokéball, Bob-omb, Ray Gun)
- **Stages:** Features and mechanics (Fountain of Dreams platforms)
- **Mechanics:** Technical terms (L-cancel, wavedash, DI, hitstun)
- **Modes:** Game modes, menu structures, VS mode, Adventure mode

**Example:** If working on `ft/chara/ftFox/`, understanding Fox's moveset helps:
- `ftFox_SpecialNStart` → Blaster startup
- `ftFox_SpecialSStart` → Fox Illusion startup
- `ftFox_SpecialHiStart` → Fire Fox startup
- `ftFox_SpecialLwStart` → Reflector (shine) startup

**Do NOT** look up:
- Other decompilation projects' naming (avoid copying potentially wrong names)
- Reverse engineering documentation or symbol maps

### Step 5: Propose Names

**Function naming pattern:** `Module_DescriptiveName`
- Keep module prefix (`ft_`, `lb_`, `gr_`, etc.)
- Use descriptive suffix based on discovered purpose
- Preserve address-based name only if purpose is truly unknown

**Variable naming:** `snake_case`
- `var_r3` → `player_index`
- `temp_f1` → `knockback_scale`

**Struct field naming:** `snake_case`
- Replace `unk<offset>` or `x<offset>` only when purpose is known
- Example: `unk45` → `costume_id` if you can verify it stores costume

### Step 7: Apply Changes (in Worktree)

Work in the **worktree directory** shown when you claimed (e.g., `melee-worktrees/dir-lb/`).

**Source files** (`<worktree>/src/melee/<module>/*.c`):
- Rename functions, variables, parameters
- Add documentation comments

**Header files** (`<worktree>/include/melee/<module>/*.h` or local `types.h`):
- Update function declarations
- Rename struct fields
- Add field documentation

**Format for documentation:**
```c
/// @brief Applies knockback to a fighter based on attack properties
/// @param[in] gobj The fighter's game object
/// @param[in] attack_data Attack properties including angle and power
/// @return true if knockback was applied successfully
```

**Format for struct fields:**
```c
struct FighterData {
    /*0x00*/ u32 flags;              /// @brief State flags
    /*0x04*/ f32 facing_direction;   /// @brief 1.0 = right, -1.0 = left
    /*0x08*/ s32 action_state;       /// @brief Current action state ID
    /*0x0C*/ f32 x0C;                /// @todo Unknown - accessed in damage calc
};
```

### Step 8: Verify Build

After making changes, ensure the build still works in the worktree:
```bash
cd <worktree> && ninja
```

If renaming causes issues, update all references before committing.

### Step 9: Commit and Record

**Commit your documentation changes:**
```bash
cd <worktree>
git add -A
git commit -m "docs(<module>): document <func_name> - <brief description>"
```

**Commit message patterns:**
- `docs(lb): document lbColl_80008440 as CheckSphereCollision`
- `docs(ft): name shield-related fields in FighterData`
- `docs(gr): add @brief comments to stage loading functions`

**Record documentation and release claim:**
```bash
melee-agent complete document <func_name>
# Or for partial documentation:
melee-agent complete document <func_name> --status partial
```

This automatically releases any claim and tracks the function as documented.

### Step 10: Batch into PR

Documentation changes are collected with other worktree commits:
```bash
melee-agent worktree list --commits  # See pending commits
# Use /collect-for-pr when ready to batch into a PR
```

## Naming Conventions Reference

| Element | Convention | Example |
|---------|------------|---------|
| Function | `Module_DescriptiveName` | `ftCo_ApplyKnockback` |
| Function (unknown) | `Module_80XXXXXX` | `ftCo_8007C98C` |
| Variable | `snake_case` | `knockback_angle` |
| Struct | `CamelCase` | `FighterData` |
| Struct field | `snake_case` | `facing_direction` |
| Unknown field | `unk<hex>` or `x<hex>` | `unk45`, `x1894` |
| Constant | `UPPER_SNAKE_CASE` | `MAX_PLAYERS` |

## Module Prefixes

| Prefix | Full Name | Description |
|--------|-----------|-------------|
| `ft_`, `ftCo_` | Fighter Common | Shared fighter behaviors |
| `ft<Char>_` | Fighter Character | Character-specific (ftFox_, ftMarth_) |
| `pl_` | Player | Player state management |
| `it_` | Item | Item behaviors |
| `gr_` | Ground | Stage/ground handling |
| `gm_` | Game | Game/match management |
| `mn_` | Menu | Menu system |
| `lb_` | Library | Utility functions |
| `cm_` | Collision | Collision detection |
| `ef_` | Effect | Visual effects |
| `sc_` | Scene | Scene management |

## Documentation Tags

```c
/// @brief Short description of purpose
/// @param[in] name Input parameter description
/// @param[out] name Output parameter description
/// @param[in,out] name Input/output parameter
/// @return Description of return value
/// @remarks Additional implementation notes
/// @todo Known unknowns or future work
/// @at{offset} Field offset in struct
/// @sz{size} Field size in bytes
```

## What NOT to Do

1. **Don't work in the main `melee/` directory** - Always use the worktree path after claiming
2. **Don't skip claiming** - Other agents may conflict with your work
3. **Don't rename without evidence** - Cross-references or game knowledge must support the name
4. **Don't copy from other decomp projects** - Their names may be wrong
5. **Don't break the build** - Always verify with `ninja` in the worktree
6. **Don't rename matched code carelessly** - Matched functions have verified behavior
7. **Don't guess struct types** - Use `unk`/`x` prefix if uncertain
8. **Don't remove useful address comments** - Keep `/* 0D7268 */` style comments
9. **Don't forget to commit and record** - Use `complete mark --documented` to track progress

## Troubleshooting

| Issue | Solution |
|-------|----------|
| Claim fails (already claimed) | Pick a different function or wait for release |
| Can't find worktree path | Run `melee-agent worktree list` to see all worktrees |
| Can't determine function purpose | Find more callers, trace data flow backward |
| Field offset unclear | Use `melee-agent struct offset 0xXX` |
| Naming conflicts with existing code | Check module conventions first |
| Build breaks after rename | Update all references with grep |
| Unsure if name is correct | Keep `@todo` comment explaining uncertainty |
| Function does multiple things | Name by primary purpose, document others |
| Changes in wrong directory | Move changes to worktree, don't commit in main repo |

## Checking Progress

Track documentation work across the pipeline:

```bash
melee-agent state status                        # All tracked functions
melee-agent state status --category documented  # Only documented functions
melee-agent state status --category undocumented  # Functions needing docs
melee-agent state status <func_name>            # Check specific function
melee-agent worktree list --commits             # Pending commits in worktrees
```

**Documentation status values:**
- `none` - No documentation yet
- `partial` - Some documentation added (e.g., function named but params not documented)
- `complete` - Fully documented (function, params, return value, struct fields)

## Example Session

```bash
# User invokes: /understand ftCo_8007E3B0

# Step 1: Claim the function
melee-agent claim add ftCo_8007E3B0
# → Auto-detected source: ft/chara/ftCommon/ftCo_Guard.c
# → Worktree: melee-worktrees/dir-ft-chara-ftCommon/

# Step 2: Get function info
melee-agent extract get ftCo_8007E3B0
# Shows function metadata, any existing scratch

# Step 3: Read the function (in worktree)
cat melee-worktrees/dir-ft-chara-ftCommon/src/melee/ft/chara/ftCommon/ftCo_Guard.c
# See function accesses shield-related fields

# Step 4: Find callers
grep -rn "ftCo_8007E3B0" melee/src/melee/
# Found: Called during shield damage calculation

# Step 5: Research game mechanics
# Shield mechanics in Melee: shields take damage, shrink, can break

# Step 6: Propose name
# ftCo_8007E3B0 → ftCo_Shield_CalcDamage

# Step 7: Apply changes (in worktree!)
# - Edit melee-worktrees/dir-ft-chara-ftCommon/src/melee/ft/chara/ftCommon/ftCo_Guard.c
# - Update header declaration
# - Add @brief documentation

# Step 8: Verify build
cd melee-worktrees/dir-ft-chara-ftCommon && ninja
# Build passes!

# Step 9: Commit and record
git add -A
git commit -m "docs(ft): document ftCo_8007E3B0 as ftCo_Shield_CalcDamage"
melee-agent complete document ftCo_8007E3B0

# Step 10: Check status
melee-agent worktree list --commits
# Shows: 1 commit pending in dir-ft-chara-ftCommon
```

## Integration with Other Skills

- **After `/understand`**: If you discover type issues during documentation, use `/decomp-fixup` to fix headers
- **Before `/decomp`**: Use `/understand` first to gain context before attempting to match
- **With `/collect-for-pr`**: Documentation improvements can be batched into PRs
