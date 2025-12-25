---
name: decomp
description: Match decompiled C code to original PowerPC assembly for Super Smash Bros Melee. Use this skill when asked to match, decompile, or fix a function to achieve 100% match against target assembly. Invoked with /decomp <function_name> or automatically when working on decompilation tasks.
---

# Melee Decompilation Matching

You are an expert at matching C source code to PowerPC assembly for the Melee decompilation project. Your goal is to achieve byte-for-byte identical compilation output.

## Core Principle: Single Session with MCP Tools

**IMPORTANT:** Work on decompilation in a SINGLE conversation session using the MCP tools directly. Do NOT spawn subprocesses or call external agents. You have all the tools you need:

- `mcp__decomp__decomp_search` - Search for existing scratches on decomp.me
- `mcp__decomp__decomp_get_scratch` - Get scratch details and source code
- `mcp__decomp__decomp_compile` - Compile code and get assembly diff
- `mcp__decomp__decomp_search_context` - Search the scratch context for types
- `mcp__decomp__decomp_update_scratch` - Save source code to a scratch on decomp.me
- `mcp__decomp__decomp_create_scratch` - Create a new scratch on decomp.me
- `mcp__decomp__decomp_claim_function` - Claim a function (for parallel agents)
- `mcp__decomp__decomp_release_function` - Release a claimed function
- `mcp__decomp__decomp_list_claims` - List currently claimed functions

This approach maintains full context of all attempts, letting you learn from what worked and what didn't.

## Workflow

### Step 0: Choose a Function

**If user specifies a function name:** Skip to Step 1.

**If user asks to "work on something new":** Find an unmatched function from the melee project:

```bash
# List unmatched functions - prioritize low-hanging fruit (0-50% match)
python -m src.cli extract list --min-match 0 --max-match 0.50 --limit 20
```

**Prioritization strategy:**
- **0-50% match** (PREFERRED) - Fresh functions with room to improve, not already optimized by others
- **50-500 bytes** - Not too simple, not too complex
- **In well-understood modules** - ft/, lb/, gr/ have good patterns

**AVOID 95-99% matches** - These have likely been worked on extensively by humans. The remaining differences are often due to context/header mismatches that are hard to fix.

Once you pick a function, **claim it before proceeding**:
```
mcp__decomp__decomp_claim_function(function_name="<function_name>")
```

If the claim fails (another agent is working on it), pick a different function. Claims expire after 1 hour.

### Step 1: Get Function Info and Find/Create Scratch

First, get the function's assembly and metadata:

```bash
python -m src.cli extract get <function_name>
```

Then check if a scratch already exists on decomp.me (search without match filter):
```
mcp__decomp__decomp_search(query="<function_name>", platform="gc_wii")
```

**If a scratch exists with context:** You can use it for reference, but you may not be able to update it (403 error) if you don't own it.

**Best practice: Always create a new scratch.** The `decomp_create_scratch` tool:
- Automatically loads the full Melee context (~1.8MB of headers)
- Saves the claim token so you can update it later
```
mcp__decomp__decomp_create_scratch(name="<function_name>", target_asm="<assembly from extract>")
```

**If you get a 403 when updating:** The scratch is owned by someone else. Create a new scratch instead - you can always update scratches you create.

**Note the scratch slug** - you'll need it for compilation.

### Step 2: Get Existing Source Code

Read the current implementation from the melee project:

```bash
# Find where the function is defined
grep -rn "<function_name>" melee/src/
```

Then use the Read tool to get the full source file and understand the context.

**Key things to look for:**
- The function signature (parameter types, return type)
- **Local struct definitions BEFORE the function** (these must be included!)
- Nearby functions for coding patterns
- Header includes for type definitions

### Step 3: Compile and Analyze

Use the MCP compile tool with your source code:

```
mcp__decomp__decomp_compile(url_or_slug="<slug>", source_code="<your code>")
```

**CRITICAL:** Include any file-local type definitions (structs, enums) in your source_code. The scratch context only has headers, not .c file local definitions.

Analyze the diff output:
- `r` = register mismatch (try reordering variable declarations)
- `i` = immediate/offset difference (usually just address differences, often OK)
- `>` = extra instruction in current (code generates more instructions than target)
- `<` = missing instruction (code generates fewer instructions than target)

### Step 4: Iterate on the Code

Make targeted changes based on the diff:

**Register allocation issues (r markers):**
- Reorder variable declarations - registers allocated in declaration order
- Move `const` declarations vs regular variables
- Try declaring variables closer to first use vs at function start

**Instruction differences:**
- Wrong instruction type → check types (s32 vs u32, f32 vs f64)
- Extra/missing instructions → check for implicit casts, operator precedence
- Different branch targets → check loop structure, switch statement ordering

**Common fixes:**
- `float` → `f32`, `int` → `s32`, `bool` → `BOOL`
- Add/remove parentheses to change evaluation order
- Inline expressions vs use temp variables
- Change `if/else` to `switch` or vice versa

### Step 5: Save Progress to decomp.me

**IMPORTANT:** After each successful compilation that improves the match, update the scratch on decomp.me so progress is saved:

```
mcp__decomp__decomp_update_scratch(url_or_slug="<slug>", source_code="<your code>")
```

This updates the scratch on **decomp.me** (not the local repo). Benefits:
- Your progress is visible on the decomp.me web UI
- Others can see and continue your work
- Partial matches are catalogued for future reference
- You have a record of what you've tried

**Note:** Only commit to the **melee repo** when you achieve 100% match (Step 7).

### Step 6: Know When to Stop

**You've achieved a match when:** score = 0

**Stop iterating when:**
1. **Stuck at 96-99% with only register differences** - The decomp.me context may differ from the original project. The code is likely correct.
2. **Same changes keep oscillating** - You've explored the search space
3. **Only `i` (offset) differences remain** - These are address differences, not code differences

**When stuck at 96-99%:**
```
The code appears functionally correct but has persistent register allocation
differences. This typically indicates the decomp.me context differs from the
original build environment.

Options:
1. Verify locally: python -m src.cli agent run --local <function_name>
2. Accept the code as correct if logic matches
3. Check if an inline function in context differs from the project
```

### Step 7: Apply Matched Code and Release Claim

Once you achieve 100% match (or determine code is correct despite context differences):

```bash
python -m src.cli commit apply <function_name> <scratch_slug>
```

**Always release the claim when done** (whether matched or giving up):
```
mcp__decomp__decomp_release_function(function_name="<function_name>")
```

## Type and Context Tips

### Finding Types in Context

Use the context search tool:
```
mcp__decomp__decomp_search_context(url_or_slug="<slug>", pattern="Fighter")
mcp__decomp__decomp_search_context(url_or_slug="<slug>", pattern="struct.*attr")
```

### Common Type Mappings

| Project Type | decomp.me Context | Notes |
|-------------|-------------------|-------|
| `ftCo_DatAttrs` | `attr` | Fighter attributes |
| `Fighter*` | `Fighter*` | Usually same |
| `HSD_GObj*` | `HSD_GObj*` | Usually same |

### File-Local Definitions

If a function uses a `static struct` defined in the .c file, you MUST include it in your source_code:

```c
// Include this!
static struct {
    u8* buffer;
    u32 size;
} my_local_data;

void my_function(void) {
    // Uses my_local_data
}
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

User: `/decomp` (no function specified)

**Step 0:** Find a good candidate (prioritize low-match functions)
```bash
python -m src.cli extract list --min-match 0 --max-match 0.50 --limit 10
```
→ Pick `lbColl_80008440` at 0% match, 180 bytes (fresh function, not worked on)
→ Claim it:
```
mcp__decomp__decomp_claim_function(function_name="lbColl_80008440")
```
→ ✅ Claimed successfully

**Step 1:** Get function info and create scratch
```bash
python -m src.cli extract get lbColl_80008440
```
→ Always create a new scratch (ensures you own it and have context):
```
mcp__decomp__decomp_create_scratch(name="lbColl_80008440", target_asm="<asm from extract>")
```
→ Created scratch `xYz12` with full Melee context (claim token saved)

**Step 2:** Read the project source
```
Read: melee/src/lb/lbcoll.c
```
→ Find the function and any local structs before it

**Step 3:** Compile with the existing source (including local struct)
```
mcp__decomp__decomp_compile(url_or_slug="xYz12", source_code="void lbColl_80008440(...) {...}")
```
→ 45% match

**Step 4:** Analyze diff - fix types, reorder variables, iterate

**Step 5:** Save progress after each improvement
```
mcp__decomp__decomp_update_scratch(url_or_slug="xYz12", source_code="...")
```

**Step 6:** Continue iterating until 100% match or stuck at 96%+

**Step 7:** Release the claim when done
```
mcp__decomp__decomp_release_function(function_name="lbColl_80008440")
```

## What NOT to Do

1. **Don't search decomp.me first when starting fresh** - find functions from the melee repo
2. **Don't spawn Python agents** that call `claude` CLI multiple times - use MCP tools directly
3. **Don't give up at 90%** - often small changes get you to 99%+
4. **Don't ignore file-local types** - they must be included in source_code
5. **Don't forget to save progress to decomp.me** - update the scratch after improvements (catalogues partial matches)
6. **Don't commit to repo until 100% match** - only Step 7 touches the melee repo
7. **Don't keep trying the same changes** - if reordering doesn't help after 3-4 attempts, the issue is likely context-related

## Troubleshooting

**Compilation fails with undefined identifier:**
- Search the context: `mcp__decomp__decomp_search_context(slug, "identifier_name")`
- Check if it's a file-local definition you need to include

**Score drops dramatically after a change:**
- You likely changed an inline function expansion
- Revert and try a different approach

**Stuck at exactly the same score:**
- The change had no effect on codegen
- Try a more significant structural change

**Only offset differences (i markers):**
- These are usually fine - the struct is just at a different address
- Focus on register (r) and instruction differences
