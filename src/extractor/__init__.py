"""Function extractor module for the melee decompilation project.

This module extracts unmatched functions from the melee decompilation project,
including their assembly code, context, and match status.
"""

from .models import (
    ObjectStatus,
    FunctionSymbol,
    FunctionMatch,
    FunctionInfo,
    ExtractionResult,
)
from .parser import ConfigureParser, parse_configure
from .report import ReportParser, parse_report
from .context import ContextGenerator, generate_context
from .symbols import SymbolParser, parse_symbols
from .asm import AsmExtractor, extract_asm_for_function
from .splits import SplitsParser, parse_splits
from .extractor import (
    FunctionExtractor,
    extract_unmatched_functions,
    extract_function,
)

__all__ = [
    # Models
    "ObjectStatus",
    "FunctionSymbol",
    "FunctionMatch",
    "FunctionInfo",
    "ExtractionResult",
    # Parsers and extractors
    "ConfigureParser",
    "ReportParser",
    "ContextGenerator",
    "SymbolParser",
    "AsmExtractor",
    "SplitsParser",
    "FunctionExtractor",
    # Async functions
    "parse_configure",
    "parse_report",
    "generate_context",
    "parse_symbols",
    "parse_splits",
    "extract_asm_for_function",
    "extract_unmatched_functions",
    "extract_function",
]

__version__ = "0.1.0"
