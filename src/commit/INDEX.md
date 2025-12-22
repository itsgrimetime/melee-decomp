# Commit Manager Module - File Index

Complete index of all files in the Commit Manager module.

## Quick Navigation

- 📖 **New User?** Start with [QUICKSTART.md](QUICKSTART.md)
- 📚 **Need Details?** Read [README.md](README.md)
- 🔍 **Looking for API?** Check [API.md](API.md)
- 🧪 **Want Examples?** See [example.py](example.py)
- ✅ **Testing?** Run [tests.py](tests.py)

---

## Python Modules (8 files)

### Core Modules (6 files)

#### 1. `__init__.py` (35 lines)
**Purpose**: Module initialization and exports
**Exports**: 12 public functions and classes
**Documentation**: Inline docstrings

```python
from commit import (
    update_source_file,
    update_scratches_txt,
    update_configure_py,
    get_file_path_from_function,
    format_files,
    verify_clang_format_available,
    create_pr,
    get_remote_url,
    check_branch_exists,
    switch_to_branch,
    CommitWorkflow,
    auto_detect_and_commit,
)
```

#### 2. `update.py` (141 lines)
**Purpose**: Update source files and scratches.txt
**Functions**:
- `update_source_file()` - Replace function implementation in C files
- `update_scratches_txt()` - Add entries to scratches.txt

**Key Features**:
- Regex-based function finding
- Proper brace matching
- Preserves surrounding code
- Prevents duplicates

#### 3. `configure.py` (119 lines)
**Purpose**: Update configure.py file
**Functions**:
- `update_configure_py()` - Change NonMatching → Matching
- `get_file_path_from_function()` - Auto-detect file location

**Key Features**:
- Pattern matching for Object() declarations
- Safe replacement logic
- File existence verification
- Source code searching

#### 4. `format.py` (120 lines)
**Purpose**: Code formatting with clang-format
**Functions**:
- `format_files()` - Run git clang-format on files
- `verify_clang_format_available()` - Check tool availability

**Key Features**:
- Automatic staging and re-staging
- Graceful fallback if unavailable
- Error handling

#### 5. `pr.py` (270 lines)
**Purpose**: Pull request creation and git operations
**Functions**:
- `create_pr()` - Create branch, commit, and open PR
- `get_remote_url()` - Get repository URL
- `check_branch_exists()` - Verify branch existence
- `switch_to_branch()` - Switch git branches

**Key Features**:
- GitHub CLI integration
- Branch management
- Commit message generation
- PR template creation

#### 6. `workflow.py` (173 lines)
**Purpose**: High-level workflow orchestration
**Classes**:
- `CommitWorkflow` - Complete workflow manager

**Functions**:
- `auto_detect_and_commit()` - One-function automation

**Key Features**:
- 5-step workflow execution
- Progress tracking
- Error handling at each step
- Files changed tracking

### Support Modules (2 files)

#### 7. `example.py` (200 lines)
**Purpose**: Working code examples
**Examples**:
1. Auto-detect and commit
2. Manual workflow
3. Individual steps
4. Commit without PR

**Use Cases**:
- Learning the API
- Quick reference
- Testing different approaches
- Template code

#### 8. `tests.py` (200 lines)
**Purpose**: Comprehensive test suite
**Tests**:
- Unit tests for all components
- Integration tests for git operations
- Dry run tests with temporary files

**Coverage**:
- Source file updates
- configure.py updates
- scratches.txt formatting
- Git operations
- Error handling

---

## Documentation (4 files)

### 1. `README.md` (7.4 KB, ~350 lines)
**Purpose**: Main documentation
**Sections**:
- Overview
- Module structure
- Usage guide
- File formats
- Workflow steps
- Requirements
- Error handling
- Troubleshooting

**Audience**: All users
**When to Read**: After quick start, before deep dive

### 2. `API.md` (11 KB, ~470 lines)
**Purpose**: Complete API reference
**Sections**:
- Workflow functions
- Update functions
- Configure functions
- Format functions
- PR functions
- Classes
- Common patterns

**Audience**: Developers integrating the module
**When to Read**: When writing code using the API

### 3. `QUICKSTART.md` (7.9 KB, ~400 lines)
**Purpose**: 5-minute getting started guide
**Sections**:
- Prerequisites
- Installation
- Your first match
- Common scenarios
- Tips and tricks
- Troubleshooting
- Example script

**Audience**: New users
**When to Read**: First, before anything else

### 4. `SUMMARY.md` (9.8 KB, ~450 lines)
**Purpose**: Build summary and overview
**Sections**:
- Module statistics
- Files created
- Key features
- Technical architecture
- Function specifications
- Workflow steps
- Testing strategy

**Audience**: Project maintainers, reviewers
**When to Read**: For project overview and status

---

## Documentation by Use Case

### "I'm new, how do I start?"
1. Read [QUICKSTART.md](QUICKSTART.md)
2. Run [example.py](example.py)
3. Refer to [README.md](README.md) for details

### "I need to integrate this into my code"
1. Check [API.md](API.md) for function signatures
2. Review [example.py](example.py) for patterns
3. Run [tests.py](tests.py) to verify setup

### "I'm maintaining or reviewing this module"
1. Read [SUMMARY.md](SUMMARY.md) for overview
2. Review [README.md](README.md) for architecture
3. Check [tests.py](tests.py) for coverage

### "Something's not working"
1. Check error message
2. Review Troubleshooting in [QUICKSTART.md](QUICKSTART.md)
3. Check Troubleshooting in [README.md](README.md)
4. Review [API.md](API.md) for correct usage

---

## Module Organization

```
commit/
├── Entry Point
│   └── __init__.py          [Module exports]
│
├── Core Functionality
│   ├── update.py            [File updates]
│   ├── configure.py         [Config updates]
│   ├── format.py            [Code formatting]
│   └── pr.py                [Git & PR ops]
│
├── High-Level API
│   └── workflow.py          [Orchestration]
│
├── Developer Tools
│   ├── example.py           [Usage examples]
│   └── tests.py             [Test suite]
│
└── Documentation
    ├── QUICKSTART.md        [Getting started]
    ├── README.md            [Main docs]
    ├── API.md               [API reference]
    ├── SUMMARY.md           [Build summary]
    └── INDEX.md             [This file]
```

---

## File Statistics

| File | Type | Lines | Size | Purpose |
|------|------|-------|------|---------|
| `__init__.py` | Python | 35 | 925 B | Module initialization |
| `update.py` | Python | 141 | 4.3 KB | File updates |
| `configure.py` | Python | 119 | 3.8 KB | Config updates |
| `format.py` | Python | 120 | 3.3 KB | Code formatting |
| `pr.py` | Python | 270 | 7.5 KB | PR creation |
| `workflow.py` | Python | 173 | 6.1 KB | Orchestration |
| `example.py` | Python | 200 | 5.6 KB | Examples |
| `tests.py` | Python | 200 | 9.5 KB | Tests |
| `README.md` | Markdown | ~350 | 7.4 KB | Main docs |
| `API.md` | Markdown | ~470 | 11 KB | API reference |
| `QUICKSTART.md` | Markdown | ~400 | 7.9 KB | Quick start |
| `SUMMARY.md` | Markdown | ~450 | 9.8 KB | Build summary |
| **TOTAL** | **12 files** | **~2,900** | **~77 KB** | **Complete module** |

---

## Import Hierarchy

```
commit/__init__.py
├─ imports from update.py
│  ├─ update_source_file
│  └─ update_scratches_txt
│
├─ imports from configure.py
│  ├─ update_configure_py
│  └─ get_file_path_from_function
│
├─ imports from format.py
│  ├─ format_files
│  └─ verify_clang_format_available
│
├─ imports from pr.py
│  ├─ create_pr
│  ├─ get_remote_url
│  ├─ check_branch_exists
│  └─ switch_to_branch
│
└─ imports from workflow.py
   ├─ CommitWorkflow (class)
   └─ auto_detect_and_commit
```

---

## Dependency Graph

```
workflow.py
├─ update.py
├─ configure.py
├─ format.py
└─ pr.py

(All modules are independent except workflow.py
which depends on all others)
```

---

## Function Count by Module

| Module | Functions | Classes | Total |
|--------|-----------|---------|-------|
| update.py | 2 | 0 | 2 |
| configure.py | 2 | 0 | 2 |
| format.py | 2 | 0 | 2 |
| pr.py | 4 | 0 | 4 |
| workflow.py | 1 | 1 | 2 |
| **Total** | **11** | **1** | **12** |

---

## Testing Coverage

| Module | Unit Tests | Integration Tests | Total |
|--------|-----------|-------------------|-------|
| update.py | ✓ | ✗ | Partial |
| configure.py | ✓ | ✗ | Partial |
| format.py | ✓ | ✗ | Partial |
| pr.py | ✗ | ✓ | Partial |
| workflow.py | ✓ | ✓ | Full |

All modules have at least partial test coverage.

---

## Common Tasks

### Task: Commit a matched function
**File**: Use `workflow.py` → `auto_detect_and_commit()`
**Example**: [example.py](example.py) → Example 1

### Task: Update just the source file
**File**: Use `update.py` → `update_source_file()`
**Example**: [example.py](example.py) → Example 3

### Task: Create a PR manually
**File**: Use `pr.py` → `create_pr()`
**Example**: [example.py](example.py) → Example 3

### Task: Find which file contains a function
**File**: Use `configure.py` → `get_file_path_from_function()`
**Reference**: [API.md](API.md) → Configure Functions

### Task: Format code
**File**: Use `format.py` → `format_files()`
**Example**: [example.py](example.py) → Example 3

---

## Version History

| Version | Date | Changes |
|---------|------|---------|
| 1.0.0 | 2024-12-22 | Initial release |

---

## Related Files

### In melee-decomp-agent project:
- `/src/agent/` - Main agent logic
- `/src/client/` - decomp.me client
- `/src/extractor/` - Function extraction

### In melee project:
- `melee/configure.py` - Build configuration
- `melee/config/GALE01/scratches.txt` - Match tracking
- `melee/.clang-format` - Code style config
- `melee/src/` - Source files

---

## Support

- **Issues**: Check error messages first
- **Questions**: Read the docs (this index points you to the right one)
- **Examples**: See [example.py](example.py)
- **Testing**: Run [tests.py](tests.py)

---

**Last Updated**: 2024-12-22
**Module Version**: 1.0.0
**Status**: ✓ Complete and documented
