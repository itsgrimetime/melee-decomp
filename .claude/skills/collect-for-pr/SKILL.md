---
name: collect-for-pr
description: Collect pending worktree commits into a PR for review. Use this skill when subdirectory worktrees have accumulated 4-7+ commits that should be batched together and submitted for review. Invoked with /collect-for-pr or automatically when monitoring worktree status.
---

# Collect Commits for PR

You are a coordinator agent that gathers matched function commits from subdirectory worktrees into PRs for review. Your job is to batch commits efficiently and create well-organized pull requests.

## When to Use This Skill

Use `/collect-for-pr` when:
- Subdirectory worktrees have accumulated commits ready for review
- You want to batch 4-7+ commits into a single PR (flexible threshold)
- After a decomp session to collect completed work

## Workflow

### Step 1: Check Worktree Status

```bash
melee-agent worktree list --commits
```

This shows all subdirectory worktrees with their pending commits. Look for:
- **Pending commits**: How many commits are waiting on each worktree
- **Total across worktrees**: Whether there's enough work to batch

**Guidance on thresholds:**
- **4-7 commits**: Good batch size, create PR
- **2-3 commits**: Consider waiting unless work has stalled
- **8+ commits**: Consider creating PR soon to avoid large batches
- **1 commit**: Usually wait, unless it's the only work available

### Step 2: Dry Run First

Always preview what will be collected:

```bash
melee-agent worktree collect --dry-run
```

This shows:
- Which commits will be cherry-picked
- Which subdirectories are involved
- Total commit count

**Review the output for:**
- Build fix commits that should go together with function matches
- Any commits that might conflict
- Logical groupings (same module tends to merge cleanly)

### Step 3: Collect and Create PR

If the dry run looks good, collect and create the PR:

```bash
melee-agent worktree collect --create-pr
```

This will:
1. Create a new branch from `upstream/master` (named `batch/YYYYMMDD`)
2. Cherry-pick all pending commits in subdirectory order
3. Push the branch to origin
4. Create a GitHub PR with organized commit listing
5. Reset pending commit counts in the database

**Custom branch name (optional):**
```bash
melee-agent worktree collect --create-pr --branch "batch/lb-module-cleanup"
```

### Step 4: Handle Conflicts

If cherry-picks fail:
- The command aborts the cherry-pick automatically
- Failed commits are listed with error details
- Successful commits are still collected

**For failed commits:**
1. Note which subdirectories have conflicts
2. The commits remain on their subdirectory branches
3. They can be collected in a future PR after resolving

### Step 5: Post-PR Cleanup

After the PR is merged, clean up empty worktrees:

```bash
melee-agent worktree prune --dry-run  # Preview
melee-agent worktree prune            # Execute
```

## Decision Framework

### Should I Create a PR Now?

| Situation | Recommendation |
|-----------|----------------|
| 4-7 commits across multiple subdirectories | Yes, good batch size |
| 8+ commits | Yes, before batch gets too large |
| 2-3 commits but work has stopped | Yes, ship what's ready |
| 1-2 commits with active work ongoing | Wait for more |
| Commits from single high-activity module (ft, lb) | Yes, clean merge likely |
| Mixed commits with potential conflicts | Consider smaller batches |

### PR Timing

- **End of work session**: Collect all completed work
- **Before switching focus**: Don't leave commits unbatched
- **Module completion**: When finishing a focused module push
- **CI keeps up**: Don't create multiple PRs faster than CI can process

## What the Commands Do

### `worktree list`
Shows all subdirectory worktrees with status:
- Commits pending (ahead of upstream/master)
- Lock status
- Last activity time
- Uncommitted changes (work in progress)

### `worktree collect`
Cherry-picks commits from subdirectory branches:
- Creates new branch from upstream/master
- Picks commits in subdirectory order (minimizes conflicts)
- Tracks success/failure per commit
- Optionally creates GitHub PR

### `worktree prune`
Removes worktrees with no pending commits:
- Only removes fully merged worktrees
- Use `--force` to remove with uncommitted changes
- Use `--max-age N` to only prune old worktrees

## Example Session

```bash
# Check what's available
melee-agent worktree list --commits
# Output shows:
#   lb:           3 commits
#   ft-chara-ftFox: 2 commits
#   gr:           2 commits
# Total: 7 commits - good batch!

# Preview collection
melee-agent worktree collect --dry-run
# Shows all 7 commits that will be cherry-picked

# Create the PR
melee-agent worktree collect --create-pr
# Creates batch/20241230 branch, cherry-picks 7 commits, creates PR

# After PR merges, clean up
melee-agent worktree prune
```

## What NOT to Do

1. **Don't create PRs with 1-2 commits** unless work has completely stopped
2. **Don't skip the dry run** on large batches
3. **Don't force-prune worktrees with uncommitted changes** without checking first
4. **Don't create multiple overlapping PRs** - wait for CI on previous PR
5. **Don't ignore cherry-pick failures** - note them for future resolution

## Troubleshooting

| Issue | Solution |
|-------|----------|
| Branch already exists | Use `--branch` to specify different name |
| Cherry-pick conflict | Commits stay on subdirectory branch for later |
| No pending commits | Nothing to collect - keep working |
| Push failed | Check git remote auth, push manually if needed |
| PR creation failed | Branch is ready, create PR manually via GitHub |

## Integration with Other Skills

- **After `/decomp`**: Commits accumulate on subdirectory worktrees
- **Before `/decomp-fixup`**: Check if fixes should go in same batch
- **Coordination**: Multiple agents can commit to different subdirectories, then one agent collects
