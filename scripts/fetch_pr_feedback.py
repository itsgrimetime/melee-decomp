#!/usr/bin/env python3
"""
Fetch all PR comments/feedback from doldecomp/melee PRs by a specific author.
Extracts review comments with their associated code context for analysis.
"""

import json
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path


@dataclass
class ReviewComment:
    """A review comment with its context."""
    pr_number: int
    pr_title: str
    pr_url: str
    comment_id: int
    author: str
    body: str
    path: str
    line: int | None
    diff_hunk: str
    created_at: str
    in_reply_to_id: int | None = None


def run_gh(args: list[str]) -> dict | list:
    """Run a gh CLI command and return parsed JSON."""
    cmd = ["gh"] + args
    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0:
        print(f"Error running: {' '.join(cmd)}", file=sys.stderr)
        print(result.stderr, file=sys.stderr)
        sys.exit(1)
    return json.loads(result.stdout)


def get_user_prs(repo: str, author: str) -> list[dict]:
    """Get all PRs by the specified author."""
    print(f"Fetching PRs by {author} from {repo}...")

    # Fetch all PRs (open and closed) by the author
    prs = run_gh([
        "pr", "list",
        "--repo", repo,
        "--author", author,
        "--state", "all",
        "--limit", "500",
        "--json", "number,title,url,state,createdAt,mergedAt"
    ])

    print(f"Found {len(prs)} PRs")
    return prs


def get_pr_review_comments(repo: str, pr_number: int) -> list[dict]:
    """Get all review comments for a specific PR."""
    # Use the API directly for review comments (inline code comments)
    comments = run_gh([
        "api",
        f"repos/{repo}/pulls/{pr_number}/comments",
        "--paginate"
    ])
    return comments if isinstance(comments, list) else []


def get_pr_issue_comments(repo: str, pr_number: int) -> list[dict]:
    """Get general PR comments (not inline)."""
    comments = run_gh([
        "api",
        f"repos/{repo}/issues/{pr_number}/comments",
        "--paginate"
    ])
    return comments if isinstance(comments, list) else []


def fetch_all_feedback(repo: str, author: str) -> list[ReviewComment]:
    """Fetch all review comments from PRs by the author."""
    prs = get_user_prs(repo, author)
    all_comments = []

    for pr in prs:
        pr_number = pr["number"]
        pr_title = pr["title"]
        pr_url = pr["url"]

        print(f"  PR #{pr_number}: {pr_title[:50]}...")

        # Get inline review comments (these have code context)
        review_comments = get_pr_review_comments(repo, pr_number)

        for c in review_comments:
            # Skip comments by the PR author (self-comments)
            if c.get("user", {}).get("login", "").lower() == author.lower():
                continue

            comment = ReviewComment(
                pr_number=pr_number,
                pr_title=pr_title,
                pr_url=pr_url,
                comment_id=c["id"],
                author=c.get("user", {}).get("login", "unknown"),
                body=c.get("body", ""),
                path=c.get("path", ""),
                line=c.get("line") or c.get("original_line"),
                diff_hunk=c.get("diff_hunk", ""),
                created_at=c.get("created_at", ""),
                in_reply_to_id=c.get("in_reply_to_id"),
            )
            all_comments.append(comment)

        # Also get general PR comments
        issue_comments = get_pr_issue_comments(repo, pr_number)
        for c in issue_comments:
            if c.get("user", {}).get("login", "").lower() == author.lower():
                continue

            comment = ReviewComment(
                pr_number=pr_number,
                pr_title=pr_title,
                pr_url=pr_url,
                comment_id=c["id"],
                author=c.get("user", {}).get("login", "unknown"),
                body=c.get("body", ""),
                path="",  # General comments don't have a path
                line=None,
                diff_hunk="",  # No diff context
                created_at=c.get("created_at", ""),
            )
            all_comments.append(comment)

    return all_comments


def format_comment_report(comments: list[ReviewComment]) -> str:
    """Format comments into a readable report."""
    lines = []
    lines.append("# PR Review Feedback Analysis")
    lines.append(f"\nTotal comments from reviewers: {len(comments)}\n")

    # Group by PR
    by_pr: dict[int, list[ReviewComment]] = {}
    for c in comments:
        by_pr.setdefault(c.pr_number, []).append(c)

    lines.append(f"PRs with feedback: {len(by_pr)}\n")
    lines.append("=" * 80)

    for pr_number in sorted(by_pr.keys(), reverse=True):
        pr_comments = by_pr[pr_number]
        first = pr_comments[0]

        lines.append(f"\n## PR #{pr_number}: {first.pr_title}")
        lines.append(f"URL: {first.pr_url}\n")

        for c in pr_comments:
            lines.append("-" * 60)
            lines.append(f"**Reviewer:** {c.author}")
            lines.append(f"**Date:** {c.created_at[:10]}")

            if c.path:
                lines.append(f"**File:** `{c.path}`" + (f" (line {c.line})" if c.line else ""))

            if c.diff_hunk:
                lines.append("\n**Code Context:**")
                lines.append("```c")
                # Clean up the diff hunk for display
                for hunk_line in c.diff_hunk.split("\n"):
                    lines.append(hunk_line)
                lines.append("```")

            lines.append("\n**Comment:**")
            lines.append(c.body)
            lines.append("")

    return "\n".join(lines)


def export_to_json(comments: list[ReviewComment], output_path: Path) -> None:
    """Export comments to JSON for further analysis."""
    data = []
    for c in comments:
        data.append({
            "pr_number": c.pr_number,
            "pr_title": c.pr_title,
            "pr_url": c.pr_url,
            "comment_id": c.comment_id,
            "author": c.author,
            "body": c.body,
            "path": c.path,
            "line": c.line,
            "diff_hunk": c.diff_hunk,
            "created_at": c.created_at,
            "in_reply_to_id": c.in_reply_to_id,
        })

    with open(output_path, "w") as f:
        json.dump(data, f, indent=2)
    print(f"Exported {len(data)} comments to {output_path}")


def main():
    import argparse

    parser = argparse.ArgumentParser(
        description="Fetch PR review feedback from doldecomp/melee"
    )
    parser.add_argument(
        "--author", "-a",
        default="itsgrimetime",
        help="GitHub username to fetch PRs for (default: itsgrimetime)"
    )
    parser.add_argument(
        "--repo", "-r",
        default="doldecomp/melee",
        help="Repository in owner/repo format (default: doldecomp/melee)"
    )
    parser.add_argument(
        "--output", "-o",
        type=Path,
        default=Path("pr_feedback_report.md"),
        help="Output file for the report (default: pr_feedback_report.md)"
    )
    parser.add_argument(
        "--json", "-j",
        type=Path,
        help="Also export raw data to JSON file"
    )

    args = parser.parse_args()

    # Fetch all feedback
    comments = fetch_all_feedback(args.repo, args.author)

    if not comments:
        print("No review comments found!")
        return

    # Generate report
    report = format_comment_report(comments)

    with open(args.output, "w") as f:
        f.write(report)
    print(f"\nReport written to {args.output}")

    # Optional JSON export
    if args.json:
        export_to_json(comments, args.json)

    # Print summary
    print(f"\n=== Summary ===")
    print(f"Total reviewer comments: {len(comments)}")

    # Count by reviewer
    by_reviewer: dict[str, int] = {}
    for c in comments:
        by_reviewer[c.author] = by_reviewer.get(c.author, 0) + 1

    print("\nComments by reviewer:")
    for reviewer, count in sorted(by_reviewer.items(), key=lambda x: -x[1]):
        print(f"  {reviewer}: {count}")


if __name__ == "__main__":
    main()
