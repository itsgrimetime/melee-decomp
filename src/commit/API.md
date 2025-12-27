# Commit Manager API Reference

Complete API documentation for the Commit Manager module.

## Table of Contents

- [Workflow Functions](#workflow-functions)
- [Update Functions](#update-functions)
- [Configure Functions](#configure-functions)
- [Format Functions](#format-functions)
- [PR Functions](#pr-functions)
- [Classes](#classes)

---

## Workflow Functions

### `auto_detect_and_commit()`

Auto-detect the file containing a function and commit it.

```python
async def auto_detect_and_commit(
    function_name: str,
    new_code: str,
    scratch_id: str,
    scratch_url: str,
    melee_root: Path,
    create_pull_request: bool = True
) -> Optional[str]
```

**Parameters:**
- `function_name` (str): Name of the matched function
- `new_code` (str): The new function implementation code
- `scratch_id` (str): decomp.me scratch ID
- `scratch_url` (str): Full URL to the decomp.me scratch
- `melee_root` (Path): Path to the melee project root
- `create_pull_request` (bool, optional): Whether to create a PR. Default: True

**Returns:**
- `str`: PR URL if successful and `create_pull_request` is True
- `None`: If failed or `create_pull_request` is False

**Example:**
```python
from pathlib import Path
from commit import auto_detect_and_commit

pr_url = await auto_detect_and_commit(
    function_name="Command_Execute",
    new_code="bool Command_Execute(...) { ... }",
    scratch_id="abc123",
    scratch_url="https://decomp.me/scratch/abc123",
    melee_root=Path("/path/to/melee")
)
```

---

## Update Functions

### `update_source_file()`

Replace function implementation in a source file.

```python
async def update_source_file(
    file_path: str,
    function_name: str,
    new_code: str,
    melee_root: Path
) -> bool
```

**Parameters:**
- `file_path` (str): Relative path to C file (e.g., "melee/lb/lbcommand.c")
- `function_name` (str): Name of the function to replace
- `new_code` (str): The new function implementation
- `melee_root` (Path): Path to the melee project root

**Returns:**
- `bool`: True if successful, False otherwise

**Behavior:**
- Finds the function in the specified file
- Replaces the entire function implementation
- Preserves surrounding code and structure
- Handles nested braces correctly

**Example:**
```python
success = await update_source_file(
    file_path="melee/lb/lbcommand.c",
    function_name="Command_Execute",
    new_code="bool Command_Execute(...) { ... }",
    melee_root=Path("/path/to/melee")
)
```

---

## Configure Functions

### `update_configure_py()`

Change NonMatching to Matching for a file in configure.py.

```python
async def update_configure_py(
    file_path: str,
    melee_root: Path
) -> bool
```

**Parameters:**
- `file_path` (str): Relative path to the C file
- `melee_root` (Path): Path to the melee project root

**Returns:**
- `bool`: True if successful, False otherwise

**Transformation:**
```python
# Before
Object(NonMatching, "melee/lb/lbcommand.c")

# After
Object(Matching, "melee/lb/lbcommand.c")
```

**Example:**
```python
success = await update_configure_py(
    file_path="melee/lb/lbcommand.c",
    melee_root=Path("/path/to/melee")
)
```

### `get_file_path_from_function()`

Find the file path containing a specific function.

```python
async def get_file_path_from_function(
    function_name: str,
    melee_root: Path
) -> str | None
```

**Parameters:**
- `function_name` (str): Name of the function to search for
- `melee_root` (Path): Path to the melee project root

**Returns:**
- `str`: Relative file path if found
- `None`: If not found

**Example:**
```python
file_path = await get_file_path_from_function(
    "Command_Execute",
    Path("/path/to/melee")
)
# Returns: "melee/lb/lbcommand.c"
```

---

## Format Functions

### `format_files()`

Run git clang-format on specified files.

```python
async def format_files(
    files: list[str],
    melee_root: Path
) -> bool
```

**Parameters:**
- `files` (list[str]): List of file paths relative to melee_root
- `melee_root` (Path): Path to the melee project root

**Returns:**
- `bool`: True if successful, False otherwise

**Behavior:**
1. Stages the files with `git add`
2. Runs `git clang-format`
3. Re-stages the formatted files

**Example:**
```python
success = await format_files(
    files=["src/melee/lb/lbcommand.c"],
    melee_root=Path("/path/to/melee")
)
```

### `verify_clang_format_available()`

Verify that git clang-format is available.

```python
async def verify_clang_format_available() -> bool
```

**Returns:**
- `bool`: True if available, False otherwise

**Example:**
```python
if await verify_clang_format_available():
    print("Clang-format is available")
```

---

## PR Functions

### `create_pr()`

Create branch, commit, and open a pull request.

```python
async def create_pr(
    function_name: str,
    scratch_url: str,
    files_changed: list[str],
    melee_root: Path,
    base_branch: str = "main"
) -> str | None
```

**Parameters:**
- `function_name` (str): Name of the matched function
- `scratch_url` (str): URL to the decomp.me scratch
- `files_changed` (list[str]): List of files that were changed
- `melee_root` (Path): Path to the melee project root
- `base_branch` (str, optional): Base branch for PR. Default: "main"

**Returns:**
- `str`: PR URL if successful
- `None`: If failed

**Branch Naming:**
```
agent/match-{function_name}
```

**Example:**
```python
pr_url = await create_pr(
    function_name="Command_Execute",
    scratch_url="https://decomp.me/scratch/abc123",
    files_changed=["src/melee/lb/lbcommand.c", "configure.py"],
    melee_root=Path("/path/to/melee")
)
```

### `get_remote_url()`

Get the GitHub repository URL.

```python
async def get_remote_url(melee_root: Path) -> str | None
```

**Parameters:**
- `melee_root` (Path): Path to the melee project root

**Returns:**
- `str`: Repository URL if successful
- `None`: If failed

**Example:**
```python
url = await get_remote_url(Path("/path/to/melee"))
# Returns: "git@github.com:doldecomp/melee.git"
```

### `check_branch_exists()`

Check if a branch exists locally.

```python
async def check_branch_exists(
    branch_name: str,
    melee_root: Path
) -> bool
```

**Parameters:**
- `branch_name` (str): Name of the branch to check
- `melee_root` (Path): Path to the melee project root

**Returns:**
- `bool`: True if branch exists, False otherwise

**Example:**
```python
exists = await check_branch_exists("main", Path("/path/to/melee"))
```

### `switch_to_branch()`

Switch to a specific branch.

```python
async def switch_to_branch(
    branch_name: str,
    melee_root: Path
) -> bool
```

**Parameters:**
- `branch_name` (str): Name of the branch to switch to
- `melee_root` (Path): Path to the melee project root

**Returns:**
- `bool`: True if successful, False otherwise

**Example:**
```python
success = await switch_to_branch("main", Path("/path/to/melee"))
```

---

## Classes

### `CommitWorkflow`

Manages the complete workflow for committing matched functions.

#### Constructor

```python
def __init__(self, melee_root: Path)
```

**Parameters:**
- `melee_root` (Path): Path to the melee project root directory

**Attributes:**
- `melee_root` (Path): The melee project root
- `files_changed` (list[str]): List of files changed during workflow

#### Methods

##### `execute()`

Execute the complete workflow to commit a matched function.

```python
async def execute(
    self,
    function_name: str,
    file_path: str,
    new_code: str,
    scratch_id: str,
    scratch_url: str,
    create_pull_request: bool = True
) -> Optional[str]
```

**Parameters:**
- `function_name` (str): Name of the matched function
- `file_path` (str): Relative path to the source file
- `new_code` (str): The new function implementation
- `scratch_id` (str): decomp.me scratch ID
- `scratch_url` (str): Full URL to the decomp.me scratch
- `create_pull_request` (bool, optional): Whether to create a PR. Default: True

**Returns:**
- `str`: PR URL if successful and `create_pull_request` is True
- `None`: If failed or `create_pull_request` is False

**Workflow Steps:**
1. Update the source file with new code
2. Update configure.py to mark as Matching
3. Format the changed files
4. Create a PR (if requested)

**Example:**
```python
workflow = CommitWorkflow(melee_root=Path("/path/to/melee"))

pr_url = await workflow.execute(
    function_name="Command_Execute",
    file_path="melee/lb/lbcommand.c",
    new_code="bool Command_Execute(...) { ... }",
    scratch_id="abc123",
    scratch_url="https://decomp.me/scratch/abc123"
)
```

---

## Error Handling

All functions follow these conventions:

**Boolean Returns:**
- `True`: Operation succeeded
- `False`: Operation failed

**Optional String Returns:**
- Valid string: Operation succeeded (e.g., PR URL)
- `None`: Operation failed

**Error Messages:**
All functions print descriptive error messages to stdout when failures occur.

---

## Type Hints

The module uses Python 3.10+ type hints:

- `str | None` instead of `Optional[str]`
- `list[str]` instead of `List[str]`
- All async functions return coroutines

---

## Dependencies

### Required Python Modules
- `asyncio` - Async operations
- `pathlib` - Path handling
- `re` - Regular expressions
- `json` - JSON handling

### Required System Tools
- `git` - Version control
- `git clang-format` - Code formatting (optional)
- `gh` (GitHub CLI) - Pull request creation

---

## Common Patterns

### Pattern 1: Complete Auto Workflow
```python
from pathlib import Path
from commit import auto_detect_and_commit

pr_url = await auto_detect_and_commit(
    function_name="MyFunction",
    new_code="...",
    scratch_id="abc123",
    scratch_url="https://decomp.me/scratch/abc123",
    melee_root=Path("/path/to/melee")
)
```

### Pattern 2: Manual Step-by-Step
```python
from commit import (
    update_source_file,
    update_configure_py,
    format_files,
    create_pr
)

melee_root = Path("/path/to/melee")

await update_source_file("melee/lb/file.c", "MyFunc", "...", melee_root)
await update_configure_py("melee/lb/file.c", melee_root)
await format_files(["src/melee/lb/file.c"], melee_root)
await create_pr("MyFunc", "https://...", ["src/..."], melee_root)
```

### Pattern 3: Workflow Class
```python
from commit import CommitWorkflow

workflow = CommitWorkflow(Path("/path/to/melee"))
pr_url = await workflow.execute(
    function_name="MyFunction",
    file_path="melee/lb/file.c",
    new_code="...",
    scratch_id="abc123",
    scratch_url="https://decomp.me/scratch/abc123"
)
```

---

## Version Information

- Python Version: 3.10+
- Module Version: 1.0.0
- Last Updated: 2024-12-22
