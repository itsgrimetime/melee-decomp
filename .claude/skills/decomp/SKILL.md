---
name: decomp
description: Match decompiled C code to original PowerPC assembly for Super Smash Bros Melee. Use this skill when asked to match, decompile, or fix a function to achieve 100% match against target assembly. Invoked with /decomp <function_name> or automatically when working on decompilation tasks.
---

# Melee Decompilation Matching

You are an expert at matching C source code to PowerPC assembly for the Melee decompilation project. Your goal is to achieve byte-for-byte identical compilation output.

## Parallel Agent Setup

Agent session isolation is **automatic** - no configuration needed. Each Claude Code conversation gets a unique agent ID based on its process ID, creating isolated session files.

**Shared across agents** (for coordination):
- `/tmp/decomp_claims.json` - function claims (prevents duplicate work)
- `/tmp/decomp_completed.json` - completion tracking (prevents re-picking)

**Per-agent** (session isolation, automatic):
- `/tmp/decomp_cookies_ppid<N>.json` - decomp.me session
- `/tmp/decomp_scratch_tokens_ppid<N>.json` - scratch ownership tokens

For manual override: `export DECOMP_AGENT_ID="agent-1"`

## Tools: CLI Commands

All decomp.me operations use the `melee-agent` CLI. Use `--json` for machine-readable output when parsing results.

**Scratch management:**
```bash
melee-agent scratch get <slug>                    # Get scratch info + source
melee-agent scratch create <function_name>        # Create new scratch from melee repo
melee-agent scratch compile <slug>                # Compile and show diff
melee-agent scratch update <slug> <file.c>        # Update scratch from file
melee-agent scratch search [query] --platform gc_wii  # Search scratches
melee-agent scratch search-context <slug> <pattern>   # Search headers
```

**Parallel agent coordination:**
```bash
melee-agent claim add <function_name>             # Claim before working
melee-agent claim release <function_name>         # Release when done
melee-agent claim list                            # Show active claims
```

**Completion tracking:**
```bash
melee-agent complete mark <name> <slug> <pct>     # Record completion
melee-agent complete list                         # Show completed functions
```

**Function extraction:**
```bash
melee-agent extract list --min-match 0 --max-match 0.50  # Find candidates
melee-agent extract get <function_name>                   # Get ASM + metadata
```

## Workflow

### Step 0: Choose a Function

**If user specifies a function name:** Skip to Step 1.

**If user asks to "work on something new":** Find an unmatched function:

```bash
melee-agent extract list --min-match 0 --max-match 0.50 --limit 20
```

**Prioritization strategy:**
- **0-50% match** (PREFERRED) - Fresh functions with room to improve
- **50-500 bytes** - Not too simple, not too complex
- **In well-understood modules** - ft/, lb/, gr/ have good patterns

**AVOID 95-99% matches** - Already optimized, remaining diffs are context issues.

Once you pick a function, **claim it before proceeding**:
```bash
melee-agent claim add <function_name>
```

If the claim fails (another agent is working on it), pick a different function. Claims expire after 1 hour.

### Step 1: Get Function Info and Create Scratch

Get the function's assembly and metadata:

```bash
melee-agent extract get <function_name>
```

Check if a scratch already exists (for reference):
```bash
melee-agent scratch search "<function_name>" --platform gc_wii
```

**Best practice: Always create a new scratch.** This ensures you own it and can update it:
```bash
melee-agent scratch create <function_name>
```

This automatically:
- Extracts ASM from the melee build
- Loads full Melee context (~1.8MB headers)
- Saves claim token for updates

**Note the scratch slug** from the output.

### Step 2: Get Existing Source Code

Read the current implementation from the melee project:

```bash
grep -rn "<function_name>" melee/src/
```

Then use the Read tool to get the full source file and understand the context.

**Key things to look for:**
- The function signature (parameter types, return type)
- **Local struct definitions BEFORE the function** (must be included!)
- Nearby functions for coding patterns
- Header includes for type definitions

### Step 3: Compile and Analyze

Write your source code to a temp file, then compile:

```bash
# Write code to temp file
cat > /tmp/decomp_test.c << 'EOF'
// Your source code here
void function_name(...) {
    ...
}
EOF

# Update scratch and compile
melee-agent scratch update <slug> /tmp/decomp_test.c
melee-agent scratch compile <slug>
```

**CRITICAL:** Include any file-local type definitions (structs, enums) in your source. The scratch context only has headers, not .c file local definitions.

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

### Step 5: Save Progress

After each successful compilation that improves the match, the scratch is already updated on decomp.me (from the `scratch update` command). Benefits:
- Your progress is visible on the decomp.me web UI
- Others can see and continue your work
- Partial matches are catalogued for future reference

### Step 6: Know When to Stop

**You've achieved a match when:** score = 0

**Stop iterating when:**
1. **Stuck at 95-99% with only register/offset differences** - The code is likely correct
2. **Same changes keep oscillating** - You've explored the search space
3. **Only `r` (register) or `i` (offset) differences remain** - These don't affect behavior

### Step 7: Verify Before Committing

**Always use --dry-run first** to verify the code will apply correctly:

```bash
melee-agent commit apply <function_name> <scratch_slug> --dry-run
```

This will:
1. Validate the code structure (balanced braces, no mid-statement insertions)
2. Show a preview of the code to be inserted
3. Temporarily apply the code and verify it compiles
4. Revert all changes

If dry-run passes, proceed with the actual commit.

### Step 8: Commit and Complete

**Commit threshold: 95%+ with only register/offset differences**

At 95%+ match with only `r` or `i` markers in the diff:
```bash
melee-agent commit apply <function_name> <scratch_slug>
```

The commit workflow now automatically:
- Validates the code before insertion
- Verifies compilation after applying changes
- Reverts if compilation fails

**Always mark as completed when done** (this prevents other agents from re-picking it):
```bash
melee-agent complete mark <function_name> <scratch_slug> <match_percent> --committed --notes "register diffs only"
```

This automatically releases the claim and persists across sessions.

## Type and Context Tips

### Finding Types in Context

Use the context search command:
```bash
melee-agent scratch search-context <slug> "Fighter"
melee-agent scratch search-context <slug> "struct.*attr"
```

### Common Type Mappings

| Project Type | decomp.me Context | Notes |
|-------------|-------------------|-------|
| `ftCo_DatAttrs` | `attr` | Fighter attributes |
| `Fighter*` | `Fighter*` | Usually same |
| `HSD_GObj*` | `HSD_GObj*` | Usually same |

### File-Local Definitions

If a function uses a `static struct` defined in the .c file, you MUST include it in your source:

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
melee-agent extract list --min-match 0 --max-match 0.50 --limit 10
```
→ Pick `lbColl_80008440` at 0% match, 180 bytes (fresh function, not worked on)
→ Claim it:
```bash
melee-agent claim add lbColl_80008440
```
→ Claimed successfully

**Step 1:** Get function info and create scratch
```bash
melee-agent extract get lbColl_80008440
melee-agent scratch create lbColl_80008440
```
→ Created scratch `xYz12` with full Melee context

**Step 2:** Read the project source
```
Read: melee/src/lb/lbcoll.c
```
→ Find the function and any local structs before it

**Step 3:** Write and compile
```bash
cat > /tmp/decomp_test.c << 'EOF'
void lbColl_80008440(...) {...}
EOF
melee-agent scratch update xYz12 /tmp/decomp_test.c
melee-agent scratch compile xYz12
```
→ 45% match

**Step 4:** Analyze diff - fix types, reorder variables, iterate

**Step 5:** Progress is auto-saved with each `scratch update`

**Step 6:** Continue iterating until 100% match or stuck at 95%+

**Step 7:** At 97% with only register diffs, commit and mark complete:
```bash
melee-agent commit apply lbColl_80008440 xYz12
melee-agent complete mark lbColl_80008440 xYz12 97.0 --committed --notes "register diffs only"
```

## What NOT to Do

1. **Don't search decomp.me first when starting fresh** - find functions from the melee repo
2. **Don't give up at 90%** - often small changes get you to 99%+
3. **Don't ignore file-local types** - they must be included in source
4. **Don't commit to repo until 95%+ match** - only Step 7 touches the melee repo
5. **Don't keep trying the same changes** - if reordering doesn't help after 3-4 attempts, the issue is likely context-related

## Troubleshooting

**Compilation fails with undefined identifier:**
- Search the context: `melee-agent scratch search-context <slug> "identifier_name"`
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

**Commit apply fails with "Code validation failed":**
- `Unbalanced braces`: The code has mismatched `{` and `}` - check the scratch source
- `Code starts with 'case'/'break'/'else'`: The function was inserted mid-statement on decomp.me
- `Function not found in code`: The scratch source doesn't contain the expected function name

**Commit apply fails with "File does not compile":**
- The matched code references undefined symbols - add missing extern declarations or struct definitions
- Use `--dry-run` to preview and test before actual commit
- Check that file-local types are included in your scratch source
