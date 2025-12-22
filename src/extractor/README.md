# Function Extractor Module

This module extracts unmatched functions from the melee decompilation project, including their assembly code, context, and match status.

## Features

- **Parse configure.py**: Extract object file status (Matching, NonMatching, Equivalent)
- **Parse symbols.txt**: Extract function symbols with addresses and sizes
- **Parse report.json**: Get per-function fuzzy match percentages from objdiff
- **Extract assembly**: Get PowerPC assembly code for functions from build/GALE01/asm/
- **Generate context**: Create decompilation context (includes and type definitions)
- **Complete function info**: Combine all data into a single FunctionInfo object

## Installation

The module uses Pydantic for data models. Install dependencies:

```bash
pip install pydantic
```

## Usage

### Extract All Unmatched Functions

```python
from pathlib import Path
from extractor import FunctionExtractor

# Initialize extractor
melee_root = Path("/path/to/melee")
extractor = FunctionExtractor(melee_root)

# Extract all unmatched functions
result = extractor.extract_unmatched_functions(
    include_asm=True,
    include_context=False  # Context generation can be slow
)

print(f"Found {result.unmatched_functions} unmatched functions")
for func in result.functions:
    print(f"{func.name} - {func.match_percent:.1f}% matched")
```

### Extract a Specific Function

```python
from pathlib import Path
from extractor import FunctionExtractor

melee_root = Path("/path/to/melee")
extractor = FunctionExtractor(melee_root)

# Extract a specific function
func_info = extractor.extract_function(
    "lbCollision_CheckMove",
    include_asm=True,
    include_context=True
)

if func_info:
    print(f"Function: {func_info.name}")
    print(f"Address: {func_info.address}")
    print(f"Size: {func_info.size_bytes} bytes")
    print(f"Match: {func_info.match_percent:.1f}%")
    print(f"File: {func_info.file_path}")
    print(f"Status: {func_info.object_status}")
    if func_info.asm:
        print(f"Assembly:\n{func_info.asm}")
```

### Parse Individual Components

```python
from pathlib import Path
from extractor import ConfigureParser, SymbolParser, ReportParser

melee_root = Path("/path/to/melee")

# Parse configure.py
parser = ConfigureParser(melee_root)
objects = parser.parse_objects()
non_matching = parser.get_non_matching_objects()

# Parse symbols.txt
symbol_parser = SymbolParser(melee_root)
symbols = symbol_parser.parse_symbols()

# Parse report.json
report_parser = ReportParser(melee_root)
matches = report_parser.get_function_matches()
stats = report_parser.get_overall_stats()
```

### Async Usage

```python
import asyncio
from pathlib import Path
from extractor import extract_unmatched_functions, extract_function

async def main():
    melee_root = Path("/path/to/melee")

    # Extract unmatched functions
    result = await extract_unmatched_functions(melee_root)

    # Extract specific function
    func = await extract_function(melee_root, "lbCollision_CheckMove")

asyncio.run(main())
```

## Module Structure

```
extractor/
├── __init__.py          # Main exports
├── models.py            # Pydantic models for data structures
├── parser.py            # Parse configure.py
├── report.py            # Parse report.json
├── symbols.py           # Parse symbols.txt
├── asm.py               # Extract assembly code
├── context.py           # Generate decompilation context
└── extractor.py         # Main extractor combining all components
```

## Data Models

### FunctionInfo

Complete information about a function:

```python
class FunctionInfo(BaseModel):
    name: str                    # Function name
    file_path: str              # Relative path in src/
    address: str                # Hex address like 0x800C5A30
    size_bytes: int             # Size in bytes
    current_match: float        # Match percentage (0.0 to 1.0)
    asm: Optional[str]          # Assembly code
    context: Optional[str]      # Decompilation context
    object_status: str          # "Matching", "NonMatching", or "Equivalent"
    section: str                # Section like .text, .init
    lib: Optional[str]          # Library name
```

### ObjectStatus

Status of an object file:

```python
class ObjectStatus(BaseModel):
    file_path: str              # Relative path in src/
    status: str                 # "Matching", "NonMatching", or "Equivalent"
    source: Optional[str]       # Source file path
    lib: Optional[str]          # Library name
```

### FunctionSymbol

Function symbol from symbols.txt:

```python
class FunctionSymbol(BaseModel):
    name: str                   # Function name
    address: str                # Hex address
    size_bytes: int             # Size in bytes
    section: str                # Section name
    scope: Optional[str]        # Scope: global, local, weak
```

### FunctionMatch

Function match data from report.json:

```python
class FunctionMatch(BaseModel):
    name: str                   # Function name
    fuzzy_match_percent: float  # Match percentage (0.0 to 100.0)
```

## Requirements

- Python 3.10+
- Pydantic 2.x
- Melee decompilation project at `/path/to/melee`
- Built project (with build/GALE01/report.json and asm files)

## Notes

- The extractor requires the melee project to be built at least once to generate assembly files and report.json
- Context generation can be slow for files with many includes
- Function-to-file mapping uses ASM files as a heuristic; for exact mapping, splits.txt would need to be parsed
- report.json may not exist if the project hasn't been built yet; in this case, match percentages default to 0.0 for NonMatching objects and 1.0 for Matching objects
