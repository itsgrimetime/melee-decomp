# Commit Manager - Quick Start Guide

Get started with the Commit Manager module in 5 minutes.

## Prerequisites

Before using this module, ensure you have:

1. **Python 3.10+** installed
2. **Git** installed and configured
3. **GitHub CLI** (`gh`) installed and authenticated
4. **git clang-format** installed (optional but recommended)
5. A local clone of the **melee repository**

### Quick Setup

```bash
# Install GitHub CLI (macOS)
brew install gh

# Authenticate with GitHub
gh auth login

# Install clang-format (macOS)
brew install clang-format

# Verify installations
git --version
gh --version
git clang-format --version
```

## Installation

The Commit Manager is part of the melee-decomp-agent project. No separate installation needed.

```python
# Import the module
from commit import auto_detect_and_commit
```

## Your First Match

Let's say you've matched a function called `Command_Execute` on decomp.me.

### Step 1: Copy Your Matched Code

Copy the matched code from decomp.me:

```c
bool Command_Execute(CommandInfo* info, u32 command)
{
    if (command < 10) {
        lbCommand_803B9840[command](info);
        return true;
    }
    return false;
}
```

### Step 2: Run the Auto-Detect Workflow

```python
import asyncio
from pathlib import Path
from commit import auto_detect_and_commit

async def main():
    # Your matched code from decomp.me
    matched_code = """bool Command_Execute(CommandInfo* info, u32 command)
{
    if (command < 10) {
        lbCommand_803B9840[command](info);
        return true;
    }
    return false;
}"""

    # Run the workflow
    pr_url = await auto_detect_and_commit(
        function_name="Command_Execute",
        new_code=matched_code,
        scratch_id="abc123def",  # From decomp.me URL
        scratch_url="https://decomp.me/scratch/abc123def",
        melee_root=Path("/path/to/melee"),  # Your melee repo
        author="agent"
    )

    if pr_url:
        print(f"Success! PR created: {pr_url}")
    else:
        print("Failed to create PR. Check the error messages above.")

# Run it
asyncio.run(main())
```

### Step 3: Review the PR

The workflow automatically:
- ✓ Updates the source file with your matched code
- ✓ Changes NonMatching → Matching in configure.py
- ✓ Adds entry to scratches.txt
- ✓ Formats the code with clang-format
- ✓ Creates a branch, commits, and opens a PR

Open the PR URL to review your changes!

## Common Scenarios

### Scenario 1: You Know the File Path

If you already know which file contains the function:

```python
from commit import CommitWorkflow

workflow = CommitWorkflow(melee_root=Path("/path/to/melee"))

pr_url = await workflow.execute(
    function_name="Command_Execute",
    file_path="melee/lb/lbcommand.c",  # Explicit path
    new_code=matched_code,
    scratch_id="abc123",
    scratch_url="https://decomp.me/scratch/abc123"
)
```

### Scenario 2: Commit Without Creating PR

Maybe you want to review locally first:

```python
pr_url = await auto_detect_and_commit(
    function_name="Command_Execute",
    new_code=matched_code,
    scratch_id="abc123",
    scratch_url="https://decomp.me/scratch/abc123",
    melee_root=Path("/path/to/melee"),
    create_pull_request=False  # Don't create PR
)
```

Then manually create PR later:
```bash
cd /path/to/melee
git push -u origin agent/match-Command_Execute
gh pr create
```

### Scenario 3: Just Update Files

Only update files without committing:

```python
from commit import (
    update_source_file,
    update_configure_py,
    format_files
)

melee_root = Path("/path/to/melee")

# Update source
await update_source_file(
    "melee/lb/lbcommand.c",
    "Command_Execute",
    matched_code,
    melee_root
)

# Update configure.py
await update_configure_py("melee/lb/lbcommand.c", melee_root)

# Format
await format_files(["src/melee/lb/lbcommand.c"], melee_root)

# Now commit manually with git
```

## Understanding the Workflow

The complete workflow performs 5 steps:

```
[1/5] Update source file
      └─ Replaces function in C file

[2/5] Update configure.py
      └─ Changes NonMatching → Matching

[3/5] Update scratches.txt
      └─ Adds match entry

[4/5] Format files
      └─ Runs git clang-format

[5/5] Create pull request
      └─ Branch → Commit → Push → PR
```

Each step is independent and can be run separately if needed.

## Tips & Tricks

### Tip 1: Get the Scratch ID

The scratch ID is in the decomp.me URL:
```
https://decomp.me/scratch/abc123def
                           ^^^^^^^^^
                           This is the ID
```

### Tip 2: Find Your Function

Don't know which file contains your function? Use:

```python
from commit import get_file_path_from_function

file_path = await get_file_path_from_function(
    "Command_Execute",
    Path("/path/to/melee")
)
print(f"Found in: {file_path}")
```

### Tip 3: Check Prerequisites

Before running, verify prerequisites:

```python
from commit import verify_clang_format_available

if await verify_clang_format_available():
    print("✓ Ready to format")
else:
    print("⚠ Install clang-format")
```

### Tip 4: Dry Run

Want to see what would happen without making changes?

```python
# Read the source code to understand what will change
workflow = CommitWorkflow(Path("/path/to/melee"))

# Check what files would be affected
print("These files will be changed:")
print("- src/melee/lb/lbcommand.c")
print("- configure.py")
print("- config/GALE01/scratches.txt")
```

## Troubleshooting

### Error: "Function not found in file"

**Cause:** Function name doesn't match or file path is wrong.

**Solution:**
1. Check the function name exactly (case-sensitive)
2. Verify the file exists in `melee/src/`
3. Use `get_file_path_from_function()` to auto-detect

### Error: "git clang-format not found"

**Cause:** clang-format not installed.

**Solution:**
```bash
# macOS
brew install clang-format

# Linux
apt-get install clang-format
```

Or skip formatting:
```python
# The workflow continues even if formatting fails
```

### Error: "gh CLI not found"

**Cause:** GitHub CLI not installed or not authenticated.

**Solution:**
```bash
brew install gh
gh auth login
```

### Error: "Branch already exists"

**Cause:** You ran this before for the same function.

**Solution:**
```bash
cd /path/to/melee
git checkout main
git branch -D agent/match-FunctionName
```

Then run again.

## Next Steps

1. **Read the full documentation**: [README.md](README.md)
2. **Check the API reference**: [API.md](API.md)
3. **Run the tests**: `python -m commit.tests`
4. **See examples**: [example.py](example.py)

## Example Script

Save this as `match.py`:

```python
#!/usr/bin/env python3
"""Quick script to commit a matched function."""

import asyncio
from pathlib import Path
from commit import auto_detect_and_commit

async def main():
    # Configure these
    MELEE_ROOT = Path("/path/to/melee")
    FUNCTION_NAME = "Command_Execute"
    SCRATCH_ID = "abc123"
    SCRATCH_URL = f"https://decomp.me/scratch/{SCRATCH_ID}"

    # Paste your matched code here
    MATCHED_CODE = """bool Command_Execute(CommandInfo* info, u32 command)
{
    if (command < 10) {
        lbCommand_803B9840[command](info);
        return true;
    }
    return false;
}"""

    # Run the workflow
    print(f"Committing match for: {FUNCTION_NAME}")
    pr_url = await auto_detect_and_commit(
        function_name=FUNCTION_NAME,
        new_code=MATCHED_CODE,
        scratch_id=SCRATCH_ID,
        scratch_url=SCRATCH_URL,
        melee_root=MELEE_ROOT
    )

    if pr_url:
        print(f"\n✓ Success! PR: {pr_url}")
        return 0
    else:
        print("\n✗ Failed")
        return 1

if __name__ == "__main__":
    exit(asyncio.run(main()))
```

Then run:
```bash
python match.py
```

## Support

Need help? Check:

1. Error messages - they're descriptive
2. [README.md](README.md) - full documentation
3. [API.md](API.md) - complete API reference
4. [example.py](example.py) - working examples
5. [tests.py](tests.py) - test suite

Happy matching! 🎮
