"""Commit Manager module for melee decomp agent.

Handles updating source files and creating PRs when functions are matched.
"""

from .update import update_source_file, update_scratches_txt
from .configure import update_configure_py, get_file_path_from_function
from .format import format_files, verify_clang_format_available
from .pr import (
    create_pr,
    get_remote_url,
    check_branch_exists,
    switch_to_branch
)
from .workflow import CommitWorkflow, auto_detect_and_commit

__all__ = [
    # Update functions
    "update_source_file",
    "update_scratches_txt",
    # Configure functions
    "update_configure_py",
    "get_file_path_from_function",
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
]
