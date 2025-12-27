---
name: decomp
description: Match decompiled C code to original PowerPC assembly for Super Smash Bros Melee. Use this skill when asked to match, decompile, or fix a function to achieve 100% match against target assembly. Invoked with /decomp <function_name> or automatically when working on decompilation tasks.
---

# Melee Decompilation Matching

You are an expert at matching C source code to PowerPC assembly for the Melee decompilation project. Your goal is to achieve byte-for-byte identical compilation output.

## Parallel Agent Setup

Agent session isolation is **automatic** - no configuration needed. Each Claude Code conversation gets a unique agent ID based on its process ID, creating isolated session files and git worktrees.

### Git Worktrees (Source Isolation)

Each agent gets its own melee worktree to avoid git conflicts when working in parallel:

- **Location:** `melee-worktrees/{agent_id}/` (e.g., `melee-worktrees/claude62741/`)
- **Branch:** `agent/{agent_id}` (e.g., `agent/claude62741`)
- **Created:** Automatically on first CLI command (takes ~1 second)

When a worktree is created, you'll see:
```
WORKTREE CREATED: /path/to/melee-worktrees/claude62741
BRANCH: agent/claude62741
Run all git commands in the worktree, not in melee/
```

On subsequent commands:
```
Using worktree: /path/to/melee-worktrees/claude62741
```

**Important:** All git operations (commits, branches, etc.) should be done in your worktree directory, not in the main `melee/` submodule.

### Session Files

**Shared across agents** (for coordination):
- `/tmp/decomp_claims.json` - function claims (ephemeral, 1-hour expiry)

**Per-agent files** (in `~/.config/decomp-me/`):
- `cookies_{agent_id}.json` - decomp.me session
- `scratch_tokens_{agent_id}.json` - scratch ownership tokens
- `completed_functions.json` - completion tracking (shared, with locking)
- `production_cookies.json` - production decomp.me auth (shared)

**Project files** (in `melee-decomp/config/`):
- `scratches_slug_map.json` - local→production slug mapping

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
melee-agent complete mark <name> <slug> <pct>     # Record completion (auto-detects branch)
melee-agent complete mark <name> <slug> <pct> --branch feat-xyz  # Explicit branch
melee-agent complete list                         # Show completed functions with branch info
```

**Sync to production decomp.me:**
```bash
melee-agent sync auth                             # Configure cf_clearance cookie
melee-agent sync status                           # Check auth status
melee-agent sync list                             # List functions to sync
melee-agent sync production                       # Sync to production (searches for existing first)
melee-agent sync slugs                            # Show local→production mapping
```

**Function extraction:**
```bash
melee-agent extract list --min-match 0 --max-match 0.50  # Find candidates
melee-agent extract list --show-status                    # Show Matching/NonMatching column
melee-agent extract get <function_name>                   # Get ASM + metadata
melee-agent extract get <function_name> --create-scratch  # Get ASM + create scratch in one step
```

**Audit & tracking:**
```bash
melee-agent audit status                          # Progress overview (merged/review/committed/ready)
melee-agent audit status --check                  # Check live PR status from GitHub
melee-agent audit list <category>                 # List by: merged, review, committed, ready, lost, wip
melee-agent audit recover                         # Show lost scratches needing recovery
```

**PR tracking:**
```bash
melee-agent pr status                             # Show PR status summary
melee-agent pr status --check                     # Check live status via gh CLI
melee-agent pr link <pr_url> <func1> <func2>...   # Link functions to a PR
melee-agent pr link-batch <pr_url> -c complete    # Link all complete functions
melee-agent pr list --no-pr                       # Show functions without PR
melee-agent pr check <pr_url>                     # Check single PR status
```

**Pre-commit validation:**
```bash
melee-agent hook validate                         # Validate staged changes
melee-agent hook install                          # Install git pre-commit hook
```

## Workflow

### Step 0: Choose a Function

**If user specifies a function name:** Skip to Step 1.

**If user asks to "work on something new":** Find an unmatched function:

```bash
melee-agent extract list --min-match 0 --max-match 0.50 --limit 20
```

**Understanding Matching vs NonMatching Files**

The melee project has two types of source files in `configure.py`:
- **Matching files**: Build compiles from C source
- **NonMatching files**: Build uses pre-compiled object from original .dol for linking

**Key insight:** You CAN work on functions in NonMatching files! The build system:
- Uses the original .dol object for linking (so builds always pass)
- Tracks per-function match % in `report.json` to detect regressions
- Only requires flipping to Matching when ALL functions in a file are 100% matched AND data matches

**When to flip a file to Matching:**
- Every function in the file must be 100% matched
- Data must also match
- Use `tools/dep_graph.py --leaves` to find files with no NonMatching dependencies

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

Get the function's assembly and create a scratch in one step:

```bash
melee-agent extract get <function_name> --create-scratch
```

This automatically:
- Extracts ASM from the melee build
- Loads full Melee context (~1.8MB headers)
- Creates a scratch on decomp.me
- Saves claim token for updates

**Note the scratch slug** from the output.

Alternatively, if you just want to inspect the function first:
```bash
melee-agent extract get <function_name>        # View ASM only
melee-agent scratch create <function_name>     # Then create scratch separately
```

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

Write your source code to a temp file, then compile. **Important:** Use an agent-specific temp file to avoid race conditions when multiple agents run in parallel:

```bash
# Write code to temp file (use agent-specific filename)
# The $$ variable gives the shell process ID, ensuring uniqueness
cat > /tmp/decomp_$$.c << 'EOF'
// Your source code here
void function_name(...) {
    ...
}
EOF

# Update scratch and compile
melee-agent scratch update <slug> /tmp/decomp_$$.c
melee-agent scratch compile <slug>
```

Alternatively, when using the Write tool in Claude Code, use a unique filename like `/tmp/decomp_<slug>.c` where `<slug>` is the scratch ID.

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

The commit workflow automatically:
- Updates the source file with your code
- Updates `configure.py` to mark the file as Matching
- Validates and verifies compilation
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

**Step 0:** Find a good candidate
```bash
melee-agent extract list --min-match 0 --max-match 0.50 --limit 10
```
→ Pick `lbColl_80008440` at 0% match, 180 bytes
→ Claim it:
```bash
melee-agent claim add lbColl_80008440
```
→ Claimed successfully

**Step 1:** Get function info and create scratch in one step
```bash
melee-agent extract get lbColl_80008440 --create-scratch
```
→ Created scratch `xYz12` with full Melee context

**Step 2:** Read the project source
```
Read: melee/src/lb/lbcoll.c
```
→ Find the function and any local structs before it

**Step 3:** Write and compile
```bash
cat > /tmp/decomp_xYz12.c << 'EOF'
void lbColl_80008440(...) {...}
EOF
melee-agent scratch update xYz12 /tmp/decomp_xYz12.c
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

**Understanding NonMatching files:**
- Functions in NonMatching files CAN be worked on - matches are tracked per-function
- The build uses the original .dol object for linking, so builds always pass
- A file can only be flipped to Matching when ALL its functions are 100% AND data matches
- Use `tools/dep_graph.py --leaves` to find NonMatching files with no NonMatching dependencies

**Header signature mismatch (context declares wrong signature):**

Sometimes the header declares a function incorrectly (e.g., `void func(void)` when the assembly clearly shows it takes parameters). Signs of this:
- Assembly shows `cmpwi r3, 1` or similar use of argument registers
- Assembly stores r3/r4/etc to stack frame at function start
- Context compilation fails with "too many arguments" or similar

**How to fix header bugs:**
1. Verify the assembly actually uses the parameter (not just passing through to a tail call)
2. Check the calling convention: r3-r10 for int args, f1-f8 for float args
3. Fix the header in the melee repo (in your worktree):
   - Find the header: `grep -rn "func_name" melee-worktrees/<agent>/include/`
   - Update the signature to match what the assembly expects
   - Rebuild: `cd melee-worktrees/<agent> && ninja`
4. Re-create the scratch with `melee-agent scratch create <func>` to get updated context
5. Continue with matching

This is a legitimate fix - header bugs exist in the codebase and fixing them is valuable.

## Full Lifecycle Workflow

This is the complete workflow from finding functions to submitting PRs:

### Phase 1: Matching (Parallel Agents)

Multiple agents work simultaneously:

1. **Check status first**: `melee-agent audit status` to see overall progress
2. **Find functions**: `melee-agent extract list --min-match 0 --max-match 50`
3. **Claim & work**: Claim a function, create scratch, iterate to 95%+
4. **Mark complete**: `melee-agent complete mark <func> <slug> <pct> --committed`

### Phase 2: Sync to Production

After accumulating matches on local decomp.me:

1. **Authenticate**: `melee-agent sync auth` (needs cf_clearance cookie from browser)
2. **List pending**: `melee-agent sync list`
3. **Push to prod**: `melee-agent sync production` (auto-links to existing scratches if found)
4. **Update file**: `melee-agent audit recover --add-to-file`

### Phase 3: PR Preparation (every ~10-15 matches)

1. **Audit state**: `melee-agent audit status`
2. **Recover missing**: If any "synced but not in file", run recovery
3. **Create branch**: `git checkout -b matches-batch-N`
4. **Stage changes**: `git add melee/` (or specific files)
5. **Run pre-commit**: `melee-agent hook validate --verbose`
6. **Commit**: `git commit -m "Add N matched functions"`
7. **Push**: `git push -u origin matches-batch-N`

### Phase 4: PR Submission

1. **Create PR** against official doldecomp/melee repo
2. **Link functions**: `melee-agent pr link-batch <pr_url> --category complete`
3. **Track status**: `melee-agent pr status --check`
4. **Address feedback**: If changes requested, update and re-run validation

### Tracking States

Each function progresses through these states:

| State | Where Tracked | Next Action |
|-------|--------------|-------------|
| Claimed | `/tmp/decomp_claims.json` | Work on it (expires 1hr) |
| Local scratch | local decomp.me | Continue improving |
| 95%+ match | `completed_functions.json` (with branch) | Sync to production |
| Synced | `scratches_slug_map.json` | Create PR |
| PR linked | `completed_functions.json` (pr_url, branch) | Wait for review |
| PR approved | GitHub | Merge PR |
| Merged | doldecomp/melee | Done! |

Check current state: `melee-agent audit status` and `melee-agent pr status`

### Audit Commands Quick Reference

```bash
# See overall status
melee-agent audit status           # Quick overview
melee-agent audit status --check   # With live PR status from GitHub

# List functions by category
melee-agent audit list merged      # PRs merged (done!)
melee-agent audit list review      # PRs open, in review
melee-agent audit list committed   # Committed but no PR
melee-agent audit list ready       # Ready for PR
melee-agent audit list lost        # 95%+ but not synced
melee-agent audit list wip         # Work in progress

# Recovery
melee-agent audit recover          # Show lost scratches
```

### Common Issues

**"Lost" matches (95%+ but not tracked):**
- These exist on local decomp.me but weren't synced
- Run `melee-agent sync production` to push to prod

**Pre-commit validation fails:**
- Check symbols.txt is updated for new function names
- Ensure clang-format has been run
