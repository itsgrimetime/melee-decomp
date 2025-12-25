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

This approach maintains full context of all attempts, letting you learn from what worked and what didn't.

## Workflow

### Step 0: Choose a Function

**If user specifies a function name:** Skip to Step 1.

**If user asks to "work on something new":** Find an unmatched function from the melee project:

```bash
# List unmatched functions with partial progress (good candidates)
python -m src.cli extract list --min-match 0.3 --max-match 0.99 --limit 20
```

Good candidates are:
- **30-99% match** - Already have partial code, easier to complete
- **50-500 bytes** - Not too simple, not too complex
- **In well-understood modules** - ft/, lb/, gr/ have good patterns

Once you pick a function, proceed to Step 1.

### Step 1: Get Function Info and Create Scratch

First, get the function's assembly and metadata:

```bash
python -m src.cli extract get <function_name>
```

Then check if a scratch already exists on decomp.me:
```
mcp__decomp__decomp_search(query="<function_name>", min_match_percent=50)
```

If a good scratch exists (80%+), use it. Otherwise create a new one:
```bash
python -m src.cli scratch create <function_name>
```

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

### Step 7: Apply Matched Code

Once you achieve 100% match (or determine code is correct despite context differences):

```bash
python -m src.cli commit apply <function_name> <scratch_slug>
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

**Step 0:** Find a good candidate
```bash
python -m src.cli extract list --min-match 0.5 --max-match 0.99 --limit 10
```
→ Pick `fn_80393C14` at 99% match, 280 bytes

**Step 1:** Get function info and create scratch
```bash
python -m src.cli extract get fn_80393C14
python -m src.cli scratch create fn_80393C14
```
→ Created scratch `Ivxsr`

**Step 2:** Read the project source
```
Read: melee/src/sysdolphin/baselib/particle.c
```
→ Find the function and the local struct before it

**Step 3:** Compile with the existing source (including local struct)
```
mcp__decomp__decomp_compile(url_or_slug="Ivxsr", source_code="static struct {...} hsd_804CF7E8; void fn_80393C14(...) {...}")
```
→ 96.4% match

**Step 4:** Analyze diff - see register mismatches, try reordering variables

**Step 5:** Save progress after each improvement
```
mcp__decomp__decomp_update_scratch(url_or_slug="Ivxsr", source_code="...")
```

**Step 6:** After several attempts, stuck at 96.4% with only r6/r8 register swap
→ Determine this is a context limitation, code is functionally correct

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
