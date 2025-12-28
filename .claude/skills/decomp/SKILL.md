---
name: decomp
description: Match decompiled C code to original PowerPC assembly for Super Smash Bros Melee. Use this skill when asked to match, decompile, or fix a function to achieve 100% match against target assembly. Invoked with /decomp <function_name> or automatically when working on decompilation tasks.
---

# Melee Decompilation Matching

You are an expert at matching C source code to PowerPC assembly for the Melee decompilation project. Your goal is to achieve byte-for-byte identical compilation output.

## Agent Worktrees

Parallel agents each have their own worktree at `melee-worktrees/<agent-id>/`.
CLI commands auto-detect and use your worktree by default.

**Key points:**
- `commit apply`, `workflow finish`, etc. automatically use your worktree
- If you see "Warning: Committing to main melee repo", something is wrong
- Commits stay isolated until collected via `melee-agent worktree collect`

**Do NOT:**
- Create branches directly in `melee/` - use your worktree
- Manually specify `--melee-root melee/` unless you have a reason

## Workflow

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

Write code to `/tmp/decomp_<slug>.c`, then compile:
```bash
melee-agent scratch compile <slug> -s /tmp/decomp_<slug>.c --diff
```

The compile also shows **match % history**:
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
- **Stop iterating:** Stuck at 95%+ with only `r`/`i` diffs, or same changes oscillating

### Step 5: Commit (REQUIRED - DO NOT SKIP)

**Threshold:** 95%+ with only register/offset differences.

> **WARNING:** Using `complete mark` WITHOUT `commit apply` does NOT save your work!
> The function will appear in your tracking file but will NOT be in the repository.
> Your match will be LOST when the scratch expires or you move on.

**RECOMMENDED: Use the workflow command (combines both steps):**
```bash
melee-agent workflow finish <function_name> <slug>
```

This single command:
1. Verifies the scratch meets the match threshold
2. Tests compilation with --dry-run
3. Applies the code to the melee repo
4. Records the function as committed
5. Releases any claims

**Alternative: Manual two-step process:**
```bash
melee-agent commit apply <function_name> <slug> --dry-run  # Always verify first
melee-agent commit apply <function_name> <slug>            # Then commit
melee-agent complete mark <function_name> <slug> <pct> --committed
```

**CRITICAL: Commit Requirements**

Before committing, you MUST ensure:

1. **Header signatures match implementations** - If you implement `void foo(int x)`, the header MUST declare `void foo(int)`, not `UNK_RET foo(UNK_PARAMS)`. The CI uses `-requireprotos` which fails on mismatches.

2. **No merge conflict markers** - Files must not contain `<<<<<<<`, `=======`, or `>>>>>>>` markers.

3. **Build passes locally** - Run `ninja` in the melee directory to verify the build succeeds before committing.

4. **Test with require-protos** - Run `python configure.py --require-protos && ninja` to catch missing prototypes early.

**Common header fixes needed:**
```c
// Before (stub declaration):
/* 0D7268 */ UNK_RET ftCo_800D7268(UNK_PARAMS);

// After (matches implementation):
/* 0D7268 */ M2C_UNK ftCo_800D7268(void* arg0);
```

**Improved commit diagnostics:** When `--dry-run` fails, the CLI now:
- Suggests missing `#include` statements based on undefined types
- Detects header signature mismatches (e.g., `UNK_RET` vs actual signature)
- Shows which header file needs updating

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

The context headers have some incorrect type declarations. When you see assembly that doesn't match the declared type, use these workarounds:

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

melee-agent claim add lbColl_80008440

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

# At 97% with only register diffs, FINISH THE FUNCTION (commits + records)
melee-agent workflow finish lbColl_80008440 xYz12
```

## Checking for Uncommitted Work

If you're unsure whether you've committed your matches:

```bash
melee-agent workflow status              # Shows all uncommitted 95%+ matches
melee-agent workflow status <func_name>  # Check specific function
```

## What NOT to Do

1. **Don't search decomp.me first when starting fresh** - find functions from the melee repo
2. **Don't give up at 90%** - often small changes get you to 99%+
3. **Don't ignore file-local types** - they must be included in source
4. **Don't commit to repo until 95%+ match** - only Step 5 touches the melee repo
5. **Don't keep trying the same changes** - if reordering doesn't help after 3-4 attempts, the issue is likely context-related
6. **Don't use `complete mark` without `--committed`** - this only records in a tracking file, NOT the repo!
7. **Don't continue working if `claim add` fails** - pick a different function
8. **Don't use raw curl/API calls** - use CLI tools like `scratch search-context` instead

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
