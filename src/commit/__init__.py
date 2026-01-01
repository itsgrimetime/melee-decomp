"""Commit Manager module for melee decomp agent.

Handles updating source files and creating PRs when functions are matched.
"""

from .update import update_source_file
from .configure import update_configure_py, get_file_path_from_function, should_mark_as_matching
from .format import format_files, verify_clang_format_available
from .pr import (
    create_pr,
    get_remote_url,
    check_branch_exists,
    switch_to_branch
)
from .workflow import CommitWorkflow, auto_detect_and_commit
from .diagnostics import (
    # Error dataclasses
    CompilerError,
    DiagnosticResult,
    # Parsing functions
    parse_mwcc_errors,
    extract_linker_errors,
    extract_undefined_identifiers,
    extract_conflicting_functions,
    # Analysis functions
    analyze_commit_error,
    suggest_includes,
    find_header_for_function,
    get_header_line_number,
    # Signature checking
    check_header_sync,
    format_signature_mismatch,
    get_header_fix_suggestion,
    # Caller detection
    find_callers,
    check_callers_need_update,
    format_caller_updates_needed,
)

__all__ = [
    # Update functions
    "update_source_file",
    # Configure functions
    "update_configure_py",
    "get_file_path_from_function",
    "should_mark_as_matching",
    # Format functions
    "format_files",
    "verify_clang_format_available",
    # PR functions
    "create_pr",
    "get_remote_url",
    "check_branch_exists",
    "switch_to_branch",
    # Workflow
    "CommitWorkflow",
    "auto_detect_and_commit",
    # Diagnostics - error types
    "CompilerError",
    "DiagnosticResult",
    # Diagnostics - parsing
    "parse_mwcc_errors",
    "extract_linker_errors",
    "extract_undefined_identifiers",
    "extract_conflicting_functions",
    # Diagnostics - analysis
    "analyze_commit_error",
    "suggest_includes",
    "find_header_for_function",
    "get_header_line_number",
    # Diagnostics - signature checking
    "check_header_sync",
    "format_signature_mismatch",
    "get_header_fix_suggestion",
    # Diagnostics - caller detection
    "find_callers",
    "check_callers_need_update",
    "format_caller_updates_needed",
]
