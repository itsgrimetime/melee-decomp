---
name: decomp
description: Match decompiled C code to original PowerPC assembly for Super Smash Bros Melee. Use this skill when asked to match, decompile, or fix a function to achieve 100% match against target assembly. Invoked with /decomp <function_name> or automatically when working on decompilation tasks.
---

# Melee Decompilation Matching

You are an expert at matching C source code to PowerPC assembly for the Melee decompilation project. Your goal is to achieve byte-for-byte identical compilation output.

## Subdirectory Worktrees

The project uses **subdirectory-based worktrees** for parallel agent work. Each source subdirectory gets its own isolated worktree, which enables easy merges since commits to different subdirectories rarely conflict.

**Worktree mapping:**
```
melee/src/melee/lb/*.c     → melee-worktrees/dir-lb/
melee/src/melee/gr/*.c     → melee-worktrees/dir-gr/
melee/src/melee/ft/chara/ftFox/*.c → melee-worktrees/dir-ft-chara-ftFox/
melee/src/melee/ft/chara/ftCommon/*.c → melee-worktrees/dir-ft-chara-ftCommon/
```

**Key points:**
- Source file is **auto-detected** when you claim a function (no flags needed)
- The worktree is created automatically when needed
- Commits stay isolated until collected via `melee-agent worktree collect`
- Local decomp.me server is **auto-detected** (no env vars needed)

**Claiming a function:**
```bash
# Just claim the function - source file is auto-detected
melee-agent claim add lbColl_80008440
```

**High-contention zone:** `ft/chara/ftCommon/` contains 123 behavior files used by ALL characters. Subdirectory locks expire after 30 minutes to prevent blocking.

**Do NOT:**
- Create branches directly in `melee/` - use subdirectory worktrees
- Manually specify `--melee-root` - if you think you need to, stop and ask the user for confirmation, stating your justification
- Work on functions in locked subdirectories owned by other agents

## Workflow

### Step 0: Automatic Build Validation

When you first run a `melee-agent` command, the system automatically:
1. Validates your worktree builds with `--require-protos`
2. If the build fails, archives the broken worktree and creates a fresh one
3. Caches the validation result for 30 minutes

You'll see messages like:
- `[dim]Running build validation (this may take a minute)...[/dim]`
- `[green]Worktree build OK[/green]` - you're good to go
- `[red]Worktree build FAILED - creating fresh worktree[/red]` - automatic recovery

**No manual action needed** - just proceed with Step 1.

### Step 1: Choose and Claim a Function

**If user specifies a function:** Skip to Step 2.

**Otherwise:** Find a good candidate and claim it:
```bash
# Best: Use recommendation scoring (considers size, match%, module)
melee-agent extract list --min-match 0 --max-match 0.50 --sort score --show-score

# Filter by module for focused work
melee-agent extract list --module lb --sort score --show-score
melee-agent extract list --module ft --sort score --show-score

melee-agent claim add <function_name>  # Claims expire after 1 hour
```

> **CRITICAL:** Once you claim a function, you MUST work on THAT function until completion.
> Do NOT switch to a different function mid-workflow. If `claim add` fails (function already claimed),
> pick a different function from the list. The function you claim is the one you work on.

**Prioritization:** The `--sort score` option ranks functions by:
- **Size:** 50-300 bytes ideal (small enough to match, complex enough to matter)
- **Match %:** Lower is better (more room to improve)
- **Module:** ft/, lb/ preferred (well-documented)

**Avoid 95-99%** matches - remaining diffs are usually context/type issues that require header fixes.

### Step 2: Create Scratch and Read Source

```bash
melee-agent extract get <function_name> --create-scratch
```

Then read the source file in `melee/src/` for context. Look for:
- Function signature and local struct definitions (must include these!)
- Nearby functions for coding patterns

### Step 3: Compile and Iterate

Write code and run compile command with code inline:

```bash
melee-agent scratch compile <slug> --code '
void func(s32 arg0) {
    if (arg0 < 1) {
        arg0 = 1;
    }
    // ... rest of function
}
' --diff
```

The compile shows **match % history**:
```
Compiled successfully!
Match: 85.0%
History: 45% → 71.5% → 85%  # Shows your progress over iterations
```

**Diff markers:**
- `r` = register mismatch → reorder variable declarations
- `i` = offset difference → usually OK, ignore
- `>/<` = extra/missing instruction → check types, casts, operator precedence

**Common fixes:**
- Reorder variable declarations (registers allocated in declaration order)
- Type fixes: `float`→`f32`, `int`→`s32`, `bool`→`BOOL`
- Add/remove parentheses, inline vs temp variables
- Change `if/else` ↔ `switch`

### Step 4: Know When to Stop

- **Match achieved:** score = 0
- **Time limit:** Don't spend more than 10 minutes on a single function
- **Stop iterating:** Stuck with only `r`/`i` diffs, or same changes oscillating

### Step 5: Commit (REQUIRED - DO NOT SKIP)

**Threshold:** Any improvement over the starting match %. Progress is progress.

**Use the workflow command:**
```bash
melee-agent workflow finish <function_name> <slug>
```

This single command:
1. Tests compilation with --dry-run
2. Applies the code to the melee repo
3. Records the function as committed
4. Releases any claims

**CRITICAL: Commit Requirements**

Before committing, you MUST ensure:

1. **Header signatures match implementations** - If you implement `void foo(int x)`, the header MUST declare `void foo(int)`, not `UNK_RET foo(UNK_PARAMS)`. The CI uses `-requireprotos` which fails on mismatches.

2. **No merge conflict markers** - Files must not contain `<<<<<<<`, `=======`, or `>>>>>>>` markers.

3. **Build passes with --require-protos** - This is the acceptance criteria:
   ```bash
   cd <worktree> && python configure.py --require-protos && ninja
   ```
   The `--require-protos` flag is **required**, not optional. It ensures all function prototypes are declared before use, which CI enforces. If this build fails, your commit will fail CI.

4. **Fix callers when signatures change** - If you change a function from `void foo(void)` to `void foo(s32)`, you must update ALL callers to pass the correct argument. Use grep to find them:
   ```bash
   grep -r "function_name" <worktree>/src/melee/
   ```
5. **No naming regressions** - Do not change names of functions, params, variables, etc. from an "english" name to their address-based name, e.g. do not change `ItemStateTable_GShell[] -> it_803F5BA8[]`

6. **No pointer arithmetic/magic numbers** - don't do things like `if (((u8*)&lbl_80472D28)[0x116] == 1) {`, if you find yourself needing to do this to get a 100% match, you should investigate and update the struct definition accordingly.

**Common header fixes needed:**
```c
// Before (stub declaration):
/* 0D7268 */ UNK_RET ftCo_800D7268(UNK_PARAMS);

// After (matches implementation):
/* 0D7268 */ M2C_UNK ftCo_800D7268(void* arg0);
```

**Improved commit diagnostics:** When `--dry-run` fails, you should see:
- Suggestions for missing `#include` statements based on undefined types
- Detection of header signature mismatches (e.g., `UNK_RET` vs actual signature)
- Notes about which header file needs updating

## Type and Context Tips

**Quick struct lookup:** Use the struct command to find field offsets and known issues:
```bash
melee-agent struct offset 0x1898              # What field is at offset 0x1898?
melee-agent struct show dmg --offset 0x1890   # Show fields near offset
melee-agent struct issues                     # Show all known type issues
melee-agent struct callback FtCmd2            # Look up callback signature
melee-agent struct callback                   # List all known callback types
```

**Search context (supports multiple patterns):**
```bash
melee-agent scratch search-context <slug> "HSD_GObj" "FtCmd2" "ColorOverlay"
```

**File-local definitions:** If a function uses a `static struct` defined in the .c file, you MUST include it in your scratch source - the context only has headers.

## Known Type Issues

The context headers may have some incorrect type declarations. When you see assembly that doesn't match the declared type, use these workarounds:

| Field | Declared | Actual | Detection | Workaround |
|-------|----------|--------|-----------|------------|
| `fp->dmg.x1894` | `int` | `HSD_GObj*` | `lwz` then dereferenced | `((HSD_GObj*)fp->dmg.x1894)` |
| `fp->dmg.x1898` | `int` | `float` | Loaded with `lfs` | `(*(float*)&fp->dmg.x1898)` |
| `fp->dmg.x1880` | `int` | `Vec3*` | Passed as pointer arg | `((Vec3*)&fp->dmg.x1880)` |
| `item->xD90.x2073` | - | `u8` | Same as `fp->x2070.x2073` | Access via union field |

**When to suspect a type issue:**
- Assembly uses `lfs`/`stfs` but header declares `int` → should be `float`
- Assembly does `lwz` then immediately dereferences → should be pointer
- Register allocation doesn't match despite correct logic → type mismatch causing extra conversion code

**Workaround pattern:**
```c
/* Cast helpers for mistyped fields */
#define DMG_X1898(fp) (*(float*)&(fp)->dmg.x1898)
#define DMG_X1894(fp) ((HSD_GObj*)(fp)->dmg.x1894)
```

## PowerPC / MWCC Reference

### Calling Convention
- Integer args: r3, r4, r5, r6, r7, r8, r9, r10
- Float args: f1, f2, f3, f4, f5, f6, f7, f8
- Return: r3 (int/ptr) or f1 (float)

### Register Allocation
- Registers allocated in variable declaration order
- Loop counters often use CTR register (not a GPR)
- Compiler may reorder loads for optimization

### Compiler Flags (Melee)
```
-O4,p -nodefaults -fp hard -Cpp_exceptions off -enum int -fp_contract on -inline auto
```

- `-O4,p` = aggressive optimization with pooling
- `-inline auto` = compiler decides what to inline

## Example Session

```bash
# Find best candidates using recommendation scoring
melee-agent extract list --min-match 0 --max-match 0.50 --sort score --show-score --limit 10
# Pick a function with high score (130+), reasonable size (50-300 bytes)

# Claim the function (source file auto-detected)
melee-agent claim add lbColl_80008440
# → Auto-detected source file: melee/lb/lbcollision.c
# → Claimed: lbColl_80008440
# → Subdirectory: lb
# → Worktree will be at: melee-worktrees/dir-lb/

# Create scratch with full context
melee-agent extract get lbColl_80008440 --create-scratch
# → Created scratch `xYz12`

# Read source file for context, then write and compile
melee-agent scratch compile xYz12 -s /tmp/decomp_xYz12.c --diff
# → 45% match, analyze diff, iterate...

# If stuck, check for type issues
melee-agent struct issues
melee-agent struct offset 0x1898  # What field is at this offset?

# Search for struct definitions in the scratch context
melee-agent scratch search-context xYz12 "CollData" "HSD_GObj"

# Improved the match, FINISH THE FUNCTION (commits + records)
melee-agent workflow finish lbColl_80008440 xYz12

# Check subdirectory worktree status
melee-agent worktree list
```

## Checking Your Progress

Track function states across the pipeline:

```bash
melee-agent state status                  # Shows all tracked functions by category
melee-agent state status --category matched   # Only 95%+ not yet committed
melee-agent state status <func_name>      # Check specific function details
melee-agent state urls <func_name>        # Show all URLs (scratch, PR)
```

**Function statuses:**
- `in_progress` - Being worked on (< 95% match)
- `matched` - 95%+ match, ready to commit
- `committed` - Code applied to repo
- `in_review` - Has an open PR
- `merged` - PR merged to main

## What NOT to Do

1. **Don't search decomp.me first when starting fresh** - find functions from the melee repo
2. **Don't spend >10 minutes on one function** - commit your progress and move on
3. **Don't ignore file-local types** - they must be included in source
4. **Don't keep trying the same changes** - if reordering doesn't help after 3-4 attempts, the issue is likely context-related
5. **Don't skip `workflow finish`** - just marking complete without committing loses your work!
6. **Don't continue working if `claim add` fails** - pick a different function
7. **Don't use raw curl/API calls** - use CLI tools like `scratch search-context` instead
8. **Don't switch functions after claiming** - work on the EXACT function you claimed, not a different one

## Troubleshooting

| Issue | Solution |
|-------|----------|
| Undefined identifier | `melee-agent scratch search-context <slug> "name"` or include file-local definition |
| Score drops dramatically | Reverted an inline expansion - try different approach |
| Stuck at same score | Change had no codegen effect - try structural change |
| Only `i` (offset) diffs | Usually fine - focus on `r` and instruction diffs |
| Commit validation fails | Check braces balanced, function name present, not mid-statement |
| Commit compile fails | Missing extern declarations or file-local types - use `--dry-run` first |
| Missing stub marker | Run `melee-agent stub add <function_name>` to add it |
| Stuck at 85-90% with extra conversion code | Likely type mismatch - run `melee-agent struct issues` and check for known issues |
| Assembly uses `lfs` but code generates `lwz`+conversion | Field is float but header says int - use cast workaround |
| Can't find struct offset | `melee-agent struct offset 0xXXX --struct StructName` |
| Struct field not visible in context | Use `M2C_FIELD(ptr, offset, type)` macro for raw offset access |

**NonMatching files:** You CAN work on functions in NonMatching files. The build uses original .dol for linking, so builds always pass. Match % is tracked per-function.

**Header signature bugs:** If assembly shows parameter usage (e.g., `cmpwi r3, 1`) but header declares `void func(void)`:
1. Fix the header in your worktree
2. Rebuild: `ninja`
3. Re-create scratch to get updated context

## Server Unreachable

If the decomp.me server is unreachable, **STOP and report the issue to the user**. Do NOT attempt to work around it with local-only workflows. The server should always be available - if it's not, something is wrong that needs to be fixed.

## Note on objdiff-cli

The `objdiff-cli diff` command is an **interactive TUI tool for humans** - it requires a terminal and does NOT work for agents. Do not attempt to use it. Agents should always use the decomp.me scratch workflow for matching functions.

If you need to verify match percentage after a build, check `build/GALE01/report.json` which is generated by `ninja`.
