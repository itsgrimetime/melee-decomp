#!/usr/bin/env python3
"""
Analyze PR feedback to identify common issue categories for pre-commit/pre-push hooks.
"""

import json
import re
from collections import defaultdict
from dataclasses import dataclass, field
from pathlib import Path


@dataclass
class IssueCategory:
    name: str
    description: str
    patterns: list[str]
    examples: list[dict] = field(default_factory=list)
    hookable: bool = True  # Whether this can be caught by automated hooks
    hook_suggestion: str = ""


# Define issue categories based on reviewer feedback patterns
ISSUE_CATEGORIES = [
    IssueCategory(
        name="raw_pointer_arithmetic",
        description="Using raw pointer arithmetic instead of proper struct field access or M2C_FIELD macro",
        patterns=[
            r"M2C_FIELD",
            r"pointer arithmetic",
            r"raw access",
            r"\*\s*\(\s*\w+\s*\*\s*\)\s*\(\s*\(\s*char\s*\*\s*\)",
            r"access the proper field",
        ],
        hookable=True,
        hook_suggestion="""
# Check for raw pointer arithmetic patterns
grep -n '\\*(.*\\*)((char\\*)' -- '*.c' && echo "ERROR: Use M2C_FIELD or proper struct fields"
grep -n '\\*(.*\\*)((u8\\*)' -- '*.c' && echo "ERROR: Use M2C_FIELD or proper struct fields"
""",
    ),
    IssueCategory(
        name="wrong_argument_type",
        description="Function argument uses void* or wrong type when a specific type should be used",
        patterns=[
            r"change argument type",
            r"change the argument",
            r"just change the.*type",
        ],
        hookable=False,  # Requires semantic understanding
        hook_suggestion="# Manual review needed - consider using specific struct types for function parameters",
    ),
    IssueCategory(
        name="wrong_return_type",
        description="Function returns wrong type (e.g., int instead of bool)",
        patterns=[
            r"returns.*bool",
            r"return type",
        ],
        hookable=True,
        hook_suggestion="""
# Check for functions returning 0/1 that might be bool
# (Heuristic: functions with TRUE/FALSE returns but int return type)
""",
    ),
    IssueCategory(
        name="true_false_case",
        description="Using TRUE/FALSE macros instead of true/false",
        patterns=[
            r"use.*true.*false.*not.*TRUE.*FALSE",
            r"TRUE.*FALSE",
        ],
        hookable=True,
        hook_suggestion="""
# Check for TRUE/FALSE usage (should use true/false)
grep -n '\\bTRUE\\b' -- '*.c' && echo "ERROR: Use lowercase true instead of TRUE"
grep -n '\\bFALSE\\b' -- '*.c' && echo "ERROR: Use lowercase false instead of FALSE"
""",
    ),
    IssueCategory(
        name="wrong_assert_filename",
        description="Assert macro uses wrong filename (should match the inline header source)",
        patterns=[
            r"filename in the assert",
            r"assert.*isn't correct",
        ],
        hookable=True,
        hook_suggestion="""
# Check assert filenames match the current file or known inline headers
# grep for __assert calls and validate the filename argument
""",
    ),
    IssueCategory(
        name="extern_instead_of_include",
        description="Using extern declarations instead of including proper headers",
        patterns=[
            r"extern",
            r"include the relevant header",
            r"create.*header",
        ],
        hookable=True,
        hook_suggestion="""
# Flag new extern declarations in .c files (should use headers)
git diff --cached -- '*.c' | grep -E '^\\+extern ' && echo "WARNING: Consider using header includes"
""",
    ),
    IssueCategory(
        name="wrong_variable_type",
        description="Using wrong type for variable (e.g., u8* instead of GXColor)",
        patterns=[
            r"this is.*GXColor",
            r"this is.*type",
            r"change.*type.*to",
        ],
        hookable=False,
        hook_suggestion="# Manual review - requires understanding of correct types",
    ),
    IssueCategory(
        name="struct_field_missing",
        description="Struct needs a new field added instead of using offset arithmetic",
        patterns=[
            r"add.*field",
            r"create a type",
            r"filling in datatypes",
            r"add.*member",
        ],
        hookable=True,
        hook_suggestion="""
# Check for suspicious offset patterns that should be struct fields
grep -nE '\\+\\s*0x[0-9a-fA-F]+\\)' -- '*.c' | head -20
""",
    ),
    IssueCategory(
        name="descriptive_name_removed",
        description="Renaming descriptive symbols to addresses (should keep descriptive names)",
        patterns=[
            r"more descriptive",
            r"why.*rename",
        ],
        hookable=True,
        hook_suggestion="""
# Check for descriptive names being replaced with addresses
git diff --cached | grep -E '^-.*[A-Z][a-z]+.*=' | grep -v '0x' | head -10
""",
    ),
    IssueCategory(
        name="orig_folder_modified",
        description="Accidentally modifying the /orig folder",
        patterns=[
            r"orig/",
            r"revert",
            r"forbid.*orig",
        ],
        hookable=True,
        hook_suggestion="""
# Block modifications to /orig folder
git diff --cached --name-only | grep '^orig/' && echo "ERROR: Do not modify /orig folder"
""",
    ),
    IssueCategory(
        name="unused_code_added",
        description="Adding unused code from merges or that's no longer needed",
        patterns=[
            r"unused",
            r"not needed",
            r"came from.*merge",
        ],
        hookable=False,
        hook_suggestion="# Manual review - check for dead code after merges",
    ),
    IssueCategory(
        name="union_motion_vars",
        description="Fighter motion vars should use union ftXxx_MotionVars instead of raw offsets",
        patterns=[
            r"union.*MotionVars",
            r"ftCrazyHand_MotionVars",
        ],
        hookable=True,
        hook_suggestion="""
# Check for fp->0x offsets that should use MotionVars union
grep -nE 'fp\\s*\\+\\s*0x2' -- 'src/melee/ft/**/*.c'
""",
    ),
]


def categorize_comment(comment: dict) -> list[str]:
    """Categorize a comment based on its content."""
    body = comment.get("body", "").lower()
    matches = []

    for cat in ISSUE_CATEGORIES:
        for pattern in cat.patterns:
            if re.search(pattern, body, re.IGNORECASE):
                matches.append(cat.name)
                break

    return matches


def analyze_feedback(comments: list[dict]) -> dict:
    """Analyze all feedback and categorize issues."""
    # Filter to human reviewers
    human_comments = [c for c in comments if c["author"] not in ["decomp-dev[bot]"]]

    category_counts = defaultdict(int)
    category_examples = defaultdict(list)
    uncategorized = []

    for comment in human_comments:
        categories = categorize_comment(comment)
        if not categories:
            uncategorized.append(comment)
        for cat in categories:
            category_counts[cat] += 1
            if len(category_examples[cat]) < 3:  # Keep up to 3 examples
                category_examples[cat].append({
                    "pr": comment["pr_number"],
                    "file": comment.get("path", ""),
                    "comment": comment["body"][:200],
                    "reviewer": comment["author"],
                })

    return {
        "category_counts": dict(category_counts),
        "category_examples": dict(category_examples),
        "uncategorized": uncategorized,
        "total_human_comments": len(human_comments),
    }


def generate_hooks_file(analysis: dict) -> str:
    """Generate a pre-commit hooks script based on analysis."""
    lines = [
        "#!/bin/bash",
        "# Pre-commit hooks for melee decompilation",
        "# Generated from PR feedback analysis",
        "",
        "set -e",
        "",
        "# Get list of staged C files",
        'STAGED_C_FILES=$(git diff --cached --name-only --diff-filter=ACMR | grep -E "\\.(c|h)$" || true)',
        "",
        'if [ -z "$STAGED_C_FILES" ]; then',
        '    exit 0',
        'fi',
        "",
    ]

    # Add checks for hookable categories
    for cat in ISSUE_CATEGORIES:
        if cat.hookable and cat.name in analysis["category_counts"]:
            count = analysis["category_counts"][cat.name]
            lines.append(f"# {cat.name} ({count} occurrences in PR feedback)")
            lines.append(f"# {cat.description}")
            if cat.hook_suggestion:
                lines.append(cat.hook_suggestion.strip())
            lines.append("")

    return "\n".join(lines)


def print_report(analysis: dict):
    """Print analysis report to console."""
    print("=" * 80)
    print("PR FEEDBACK ANALYSIS - Issue Categories for Hooks")
    print("=" * 80)
    print(f"\nTotal human reviewer comments: {analysis['total_human_comments']}")
    print(f"Categorized: {sum(analysis['category_counts'].values())}")
    print(f"Uncategorized: {len(analysis['uncategorized'])}")

    print("\n" + "-" * 60)
    print("ISSUE CATEGORIES (sorted by frequency)")
    print("-" * 60)

    sorted_cats = sorted(
        analysis["category_counts"].items(),
        key=lambda x: x[1],
        reverse=True
    )

    for cat_name, count in sorted_cats:
        cat = next((c for c in ISSUE_CATEGORIES if c.name == cat_name), None)
        if cat:
            hookable = "✓" if cat.hookable else "✗"
            print(f"\n[{hookable}] {cat_name}: {count} occurrences")
            print(f"    {cat.description}")

            if cat_name in analysis["category_examples"]:
                print("    Examples:")
                for ex in analysis["category_examples"][cat_name][:2]:
                    print(f"      - PR #{ex['pr']} ({ex['reviewer']}): {ex['comment'][:80]}...")

    print("\n" + "-" * 60)
    print("UNCATEGORIZED COMMENTS")
    print("-" * 60)
    for comment in analysis["uncategorized"][:5]:
        print(f"\nPR #{comment['pr_number']} ({comment['author']}):")
        print(f"  {comment['body'][:150]}...")

    print("\n" + "=" * 80)
    print("LEGEND: [✓] = Can be automated in hooks, [✗] = Requires manual review")
    print("=" * 80)


def main():
    import argparse

    parser = argparse.ArgumentParser(description="Analyze PR feedback for hook patterns")
    parser.add_argument(
        "--input", "-i",
        type=Path,
        default=Path("pr_feedback.json"),
        help="Input JSON file from fetch_pr_feedback.py"
    )
    parser.add_argument(
        "--hooks-output", "-o",
        type=Path,
        help="Output file for generated pre-commit hooks"
    )

    args = parser.parse_args()

    if not args.input.exists():
        print(f"Error: {args.input} not found. Run fetch_pr_feedback.py first.")
        return 1

    with open(args.input) as f:
        comments = json.load(f)

    analysis = analyze_feedback(comments)
    print_report(analysis)

    if args.hooks_output:
        hooks_content = generate_hooks_file(analysis)
        with open(args.hooks_output, "w") as f:
            f.write(hooks_content)
        print(f"\nHooks script written to: {args.hooks_output}")


if __name__ == "__main__":
    main()
