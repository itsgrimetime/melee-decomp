# Commit Manager Module

The Commit Manager module handles updating source files and creating pull requests when functions are matched in the melee decomp project.

## Overview

This module provides a complete workflow for integrating matched functions from decomp.me into the melee decompilation project. It automates:

1. Updating source files with matched code
2. Updating `configure.py` to change `NonMatching` → `Matching`
3. Running code formatting (`git clang-format`)
4. Creating branches, commits, and pull requests

## Module Structure

```
commit/
├── __init__.py       # Module exports
├── update.py         # Update source files
├── configure.py      # Update configure.py file
├── format.py         # Run clang-format on files
├── pr.py            # Create PRs via GitHub API
├── workflow.py      # High-level workflow orchestration
└── README.md        # This file
```

## Usage

### Quick Start - Auto-detect Workflow

The easiest way to use this module is with the auto-detect workflow:

```python
from pathlib import Path
from commit import auto_detect_and_commit

# Auto-detect file and commit
pr_url = await auto_detect_and_commit(
    function_name="Command_Execute",
    new_code="""bool Command_Execute(CommandInfo* info, u32 command)
{
    if (command < 10) {
        lbCommand_803B9840[command](info);
        return true;
    }
    return false;
}""",
    scratch_id="abc123",
    scratch_url="https://decomp.me/scratch/abc123",
    melee_root=Path("/path/to/melee"),
    create_pull_request=True
)

print(f"PR created: {pr_url}")
```

### Manual Workflow

For more control, use the `CommitWorkflow` class:

```python
from pathlib import Path
from commit import CommitWorkflow

workflow = CommitWorkflow(melee_root=Path("/path/to/melee"))

pr_url = await workflow.execute(
    function_name="Command_Execute",
    file_path="melee/lb/lbcommand.c",  # Relative to src/
    new_code="...",
    scratch_id="abc123",
    scratch_url="https://decomp.me/scratch/abc123",
    create_pull_request=True
)
```

### Individual Functions

You can also use individual functions for specific tasks:

#### Update Source File

```python
from commit import update_source_file

success = await update_source_file(
    file_path="melee/lb/lbcommand.c",
    function_name="Command_Execute",
    new_code="...",
    melee_root=Path("/path/to/melee")
)
```

#### Update configure.py

```python
from commit import update_configure_py

success = await update_configure_py(
    file_path="melee/lb/lbcommand.c",
    melee_root=Path("/path/to/melee")
)
```

#### Format Files

```python
from commit import format_files

success = await format_files(
    files=["melee/lb/lbcommand.c"],
    melee_root=Path("/path/to/melee")
)
```

#### Create Pull Request

```python
from commit import create_pr

pr_url = await create_pr(
    function_name="Command_Execute",
    scratch_url="https://decomp.me/scratch/abc123",
    files_changed=["src/melee/lb/lbcommand.c", "configure.py"],
    melee_root=Path("/path/to/melee")
)
```

## Requirements

### System Dependencies

- **git**: For version control operations
- **git clang-format**: For code formatting (optional but recommended)
- **gh** (GitHub CLI): For creating pull requests

Install GitHub CLI:
```bash
# macOS
brew install gh

# Linux
# See: https://github.com/cli/cli#installation

# Authenticate
gh auth login
```

### Python Dependencies

- Python 3.10+ (uses `|` union type syntax)
- asyncio (standard library)
- pathlib (standard library)
- re (standard library)

## File Formats

### Source Files

Source files are located in `melee/src/` directory. The module:
- Finds and replaces the entire function implementation
- Preserves file structure and surrounding code
- Maintains proper indentation and formatting

### configure.py

The `configure.py` file uses this format:
```python
Object(NonMatching, "melee/lb/lbcommand.c")  # Before
Object(Matching, "melee/lb/lbcommand.c")     # After
```

## Workflow Steps

The complete workflow performs these steps in order:

1. **Update Source File**
   - Finds the function in the C file
   - Replaces the implementation with matched code
   - Preserves file structure

2. **Update configure.py**
   - Changes `Object(NonMatching, ...)` to `Object(Matching, ...)`
   - Validates the file exists in configure.py

3. **Format Files**
   - Runs `git clang-format` on modified C files
   - Ensures code follows project style guidelines
   - Automatically stages formatted changes

4. **Create Pull Request**
   - Creates new branch: `agent/match-{function_name}`
   - Commits all changes with descriptive message
   - Pushes to remote repository
   - Opens PR with summary and test plan

## Error Handling

All functions return boolean values or `None` to indicate success/failure:

- `True` / valid value: Operation succeeded
- `False` / `None`: Operation failed

Errors are printed to stdout with descriptive messages.

## Branch Naming

Pull request branches follow this pattern:
```
agent/match-{function_name}
```

Example: `agent/match-Command_Execute`

## Commit Messages

Commits follow this format:
```
Match {function_name}

Matched function {function_name} from decomp.me.

decomp.me scratch: {scratch_url}

Files changed:
- {file1}
- {file2}

🤖 Generated with [Claude Code](https://claude.com/claude-code)

Co-Authored-By: Claude Opus 4.5 <noreply@anthropic.com>
```

## Pull Request Template

PRs include:
- Summary of the match
- Link to decomp.me scratch
- Test plan checklist
- Files changed list
- Attribution

## Best Practices

1. **Always verify the match** - Ensure the code actually matches before committing
2. **Test locally first** - Run the build to verify no regressions
3. **Use descriptive scratch names** - Name scratches on decomp.me appropriately
4. **Review formatting** - The auto-formatter may need manual adjustments
5. **Check for conflicts** - Ensure no one else is working on the same function

## Troubleshooting

### "git clang-format not found"

Install clang-format:
```bash
# macOS
brew install clang-format

# Linux
apt-get install clang-format
```

### "gh CLI not found"

Install and authenticate with GitHub CLI:
```bash
brew install gh
gh auth login
```

### "Function not found in file"

Verify:
- The function name is correct
- The file path is relative to `src/` directory
- The function exists in the specified file

### "Branch already exists"

The module will checkout the existing branch. To start fresh:
```bash
cd /path/to/melee
git checkout main
git branch -D agent/match-{function_name}
```

## Contributing

When modifying this module:

1. Maintain async/await patterns for all I/O operations
2. Add comprehensive error handling
3. Print informative messages for debugging
4. Update this README with any new features
5. Follow the existing code style

## License

Part of the melee-decomp-agent project.
