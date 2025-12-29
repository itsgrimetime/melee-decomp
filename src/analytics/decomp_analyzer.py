"""
Decomp Agent Performance Analyzer

Analyzes Claude Code sessions that use the /decomp skill to track:
- Success rates (per-function, commit, worktree usage)
- Efficiency (tokens, turns per function)
- Error rates and categories
- Workflow stage progression
"""

import json
import re
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from pathlib import Path
from typing import Optional
from enum import Enum


class WorkflowStage(Enum):
    """Stages in the decomp workflow."""
    CLAIM = "claim"
    CREATE_SCRATCH = "create_scratch"
    READ_CONTEXT = "read_context"
    ITERATE = "iterate"
    COMMIT = "commit"
    COMPLETE = "complete"


class ErrorCategory(Enum):
    """Categories of errors encountered."""
    CLAIM_CONFLICT = "claim_conflict"
    SERVER_ERROR = "server_error"
    BUILD_FAILURE = "build_failure"
    TYPE_MISMATCH = "type_mismatch"
    MISSING_STUB = "missing_stub"
    MISSING_CONTEXT = "missing_context"
    PROTO_MISMATCH = "proto_mismatch"
    SYNTAX_ERROR = "syntax_error"
    GENERAL = "general"


@dataclass
class MatchProgress:
    """A single match percentage observation."""
    timestamp: Optional[datetime]
    match_pct: float
    iteration: int


@dataclass
class ErrorEvent:
    """An error encountered during the workflow."""
    timestamp: Optional[datetime]
    category: ErrorCategory
    message: str
    tool_name: Optional[str] = None


@dataclass
class FunctionAttempt:
    """Tracks work on a single function within a session."""
    function_name: str
    slug: Optional[str] = None

    # Timestamps
    started_at: Optional[datetime] = None
    finished_at: Optional[datetime] = None

    # Workflow progression
    stages_completed: list[WorkflowStage] = field(default_factory=list)
    current_stage: Optional[WorkflowStage] = None

    # Match progression
    match_history: list[MatchProgress] = field(default_factory=list)
    final_match_pct: Optional[float] = None

    # Iteration tracking
    compile_count: int = 0
    context_lookups: int = 0  # struct offset, search-context calls

    # Outcome
    committed: bool = False
    abandoned: bool = False

    # Error tracking
    errors: list[ErrorEvent] = field(default_factory=list)

    # Resource usage (for this function)
    input_tokens: int = 0
    output_tokens: int = 0
    turn_count: int = 0

    # Worktree usage
    used_worktree: bool = False
    worktree_correct: bool = True  # False if committed to main repo

    # Build validation
    dry_run_used: bool = False
    build_passed_first_try: bool = True
    header_fixes_needed: int = 0

    @property
    def duration(self) -> Optional[timedelta]:
        if self.started_at and self.finished_at:
            return self.finished_at - self.started_at
        return None

    @property
    def success(self) -> bool:
        return self.committed and not self.abandoned

    @property
    def match_improved(self) -> bool:
        if len(self.match_history) < 2:
            return False
        return self.match_history[-1].match_pct > self.match_history[0].match_pct

    @property
    def had_thrashing(self) -> bool:
        """Detect if match % oscillated (went down then up or vice versa)."""
        if len(self.match_history) < 3:
            return False

        # Check for direction changes
        direction_changes = 0
        prev_direction = None

        for i in range(1, len(self.match_history)):
            curr = self.match_history[i].match_pct
            prev = self.match_history[i-1].match_pct

            if curr > prev:
                direction = 1
            elif curr < prev:
                direction = -1
            else:
                continue

            if prev_direction is not None and direction != prev_direction:
                direction_changes += 1
            prev_direction = direction

        return direction_changes >= 2


@dataclass
class DecompSession:
    """A session that used the /decomp skill."""
    session_id: str
    project: str
    session_path: Path

    # Timestamps
    started_at: Optional[datetime] = None
    ended_at: Optional[datetime] = None

    # Functions worked on
    functions: list[FunctionAttempt] = field(default_factory=list)

    # Aggregate token usage
    total_input_tokens: int = 0
    total_output_tokens: int = 0
    total_turns: int = 0

    # Tool usage counts
    tool_counts: dict[str, int] = field(default_factory=dict)
    tool_errors: dict[str, int] = field(default_factory=dict)

    @property
    def duration(self) -> Optional[timedelta]:
        if self.started_at and self.ended_at:
            return self.ended_at - self.started_at
        return None

    @property
    def functions_attempted(self) -> int:
        return len(self.functions)

    @property
    def functions_completed(self) -> int:
        return sum(1 for f in self.functions if f.committed)

    @property
    def success_rate(self) -> float:
        if not self.functions:
            return 0.0
        return self.functions_completed / self.functions_attempted


@dataclass
class AggregateMetrics:
    """Aggregate metrics across all analyzed sessions."""

    # Session counts
    total_sessions: int = 0
    total_functions_attempted: int = 0
    total_functions_completed: int = 0

    # Success rates
    overall_success_rate: float = 0.0
    commit_success_rate: float = 0.0  # Of those attempted, how many committed
    worktree_correct_rate: float = 0.0  # Used worktree correctly
    build_first_try_rate: float = 0.0  # Build passed on first attempt
    dry_run_usage_rate: float = 0.0  # How often dry-run was used

    # Efficiency (averages)
    avg_tokens_per_function: float = 0.0
    avg_turns_per_function: float = 0.0
    avg_iterations_per_function: float = 0.0
    avg_duration_per_function: Optional[timedelta] = None

    # Error rates
    total_errors: int = 0
    errors_per_function: float = 0.0
    errors_by_category: dict[str, int] = field(default_factory=dict)

    # Match progression
    avg_initial_match: float = 0.0
    avg_final_match: float = 0.0
    thrashing_rate: float = 0.0  # % of attempts with oscillating match %

    # Distribution data for histograms
    final_match_distribution: list[float] = field(default_factory=list)
    tokens_distribution: list[int] = field(default_factory=list)
    turns_distribution: list[int] = field(default_factory=list)


class DecompAnalyzer:
    """Analyzes decomp sessions from Claude Code conversation history."""

    CLAUDE_DIR = Path.home() / '.claude'
    PROJECTS_DIR = CLAUDE_DIR / 'projects'

    # Patterns for detecting decomp-related activity
    DECOMP_SKILL_PATTERN = re.compile(r'/decomp\s+(\w+)?', re.IGNORECASE)
    MELEE_AGENT_PATTERN = re.compile(r'melee-agent\s+(\w+)\s+(\w+)?')
    MATCH_PCT_PATTERN = re.compile(r'Match:\s*([\d.]+)%')
    MATCH_HISTORY_PATTERN = re.compile(r'History:\s*([\d.%\s→]+)')
    SLUG_PATTERN = re.compile(r'[Ss]lug[:\s]+[`\']?(\w+)[`\']?|scratch[:\s]+[`\']?(\w+)[`\']?')

    def __init__(self, project_filter: str = "melee-decomp"):
        self.project_filter = project_filter
        self.sessions: list[DecompSession] = []

    def find_project_dirs(self) -> list[Path]:
        """Find project directories matching the filter."""
        if not self.PROJECTS_DIR.exists():
            return []

        dirs = []
        for d in self.PROJECTS_DIR.iterdir():
            if d.is_dir() and not d.name.startswith('.'):
                if self.project_filter.lower() in d.name.lower():
                    dirs.append(d)
        return sorted(dirs)

    def load_session(self, session_path: Path) -> list[dict]:
        """Load all entries from a session JSONL file."""
        entries = []
        with open(session_path, 'r') as f:
            for line in f:
                line = line.strip()
                if line:
                    try:
                        entries.append(json.loads(line))
                    except json.JSONDecodeError:
                        continue
        return entries

    def is_decomp_session(self, entries: list[dict]) -> bool:
        """Check if a session involves decomp work."""
        for entry in entries:
            if entry.get('type') == 'user':
                msg = entry.get('message', {})
                content = msg.get('content', '')
                if isinstance(content, str):
                    # Check for /decomp skill invocation
                    if self.DECOMP_SKILL_PATTERN.search(content):
                        return True
                    # Check for melee-agent commands
                    if 'melee-agent' in content.lower():
                        return True

            if entry.get('type') == 'assistant':
                msg = entry.get('message', {})
                content = msg.get('content', [])
                if isinstance(content, list):
                    for item in content:
                        if isinstance(item, dict) and item.get('type') == 'tool_use':
                            tool_input = item.get('input', {})
                            cmd = tool_input.get('command', '')
                            if 'melee-agent' in cmd:
                                return True
        return False

    def parse_timestamp(self, ts_str: Optional[str]) -> Optional[datetime]:
        """Parse ISO timestamp string."""
        if not ts_str:
            return None
        try:
            return datetime.fromisoformat(ts_str.replace('Z', '+00:00'))
        except (ValueError, TypeError):
            return None

    def extract_tool_calls(self, entries: list[dict]) -> list[dict]:
        """Extract all tool calls with their results."""
        tools = []
        pending = {}  # tool_id -> tool info

        for entry in entries:
            ts = self.parse_timestamp(entry.get('timestamp'))

            if entry.get('type') == 'assistant':
                msg = entry.get('message', {})
                content = msg.get('content', [])
                usage = msg.get('usage', {})

                if isinstance(content, list):
                    for item in content:
                        if isinstance(item, dict) and item.get('type') == 'tool_use':
                            tool_id = item.get('id', '')
                            pending[tool_id] = {
                                'id': tool_id,
                                'name': item.get('name', 'unknown'),
                                'input': item.get('input', {}),
                                'timestamp': ts,
                                'result': None,
                                'is_error': False,
                                'input_tokens': usage.get('input_tokens', 0) +
                                               usage.get('cache_read_input_tokens', 0),
                                'output_tokens': usage.get('output_tokens', 0),
                            }

            elif entry.get('type') == 'user':
                msg = entry.get('message', {})
                content = msg.get('content', [])

                if isinstance(content, list):
                    for item in content:
                        if isinstance(item, dict) and item.get('type') == 'tool_result':
                            tool_id = item.get('tool_use_id', '')
                            if tool_id in pending:
                                tool_info = pending.pop(tool_id)
                                tool_info['result'] = item.get('content', '')
                                tool_info['is_error'] = item.get('is_error', False)
                                tools.append(tool_info)

        return tools

    def analyze_session(self, session_path: Path) -> Optional[DecompSession]:
        """Analyze a single session for decomp metrics."""
        entries = self.load_session(session_path)

        if not self.is_decomp_session(entries):
            return None

        project_name = session_path.parent.name.replace('-Users-mike-code-', '')

        session = DecompSession(
            session_id=session_path.stem,
            project=project_name,
            session_path=session_path,
        )

        # Extract timestamps
        for entry in entries:
            ts = self.parse_timestamp(entry.get('timestamp'))
            if ts:
                if session.started_at is None or ts < session.started_at:
                    session.started_at = ts
                if session.ended_at is None or ts > session.ended_at:
                    session.ended_at = ts

        # Extract tool calls
        tool_calls = self.extract_tool_calls(entries)

        # Count tool usage
        for tool in tool_calls:
            name = tool['name']
            session.tool_counts[name] = session.tool_counts.get(name, 0) + 1
            if tool['is_error']:
                session.tool_errors[name] = session.tool_errors.get(name, 0) + 1
            session.total_input_tokens += tool['input_tokens']
            session.total_output_tokens += tool['output_tokens']

        # Count turns
        session.total_turns = sum(1 for e in entries if e.get('type') in ('user', 'assistant'))

        # Parse function attempts from tool calls
        current_function: Optional[FunctionAttempt] = None

        for tool in tool_calls:
            if tool['name'] == 'Bash':
                cmd = tool.get('input', {}).get('command', '')
                result = tool.get('result', '') or ''

                # Detect melee-agent commands
                if 'melee-agent' not in cmd:
                    continue

                # Claim command
                if 'claim add' in cmd:
                    match = re.search(r'claim add\s+(\w+)', cmd)
                    if match:
                        func_name = match.group(1)
                        # Start new function attempt
                        current_function = FunctionAttempt(
                            function_name=func_name,
                            started_at=tool['timestamp'],
                        )
                        current_function.stages_completed.append(WorkflowStage.CLAIM)
                        current_function.current_stage = WorkflowStage.CLAIM
                        session.functions.append(current_function)

                        # Check for claim conflict
                        if 'already claimed' in result.lower() or 'conflict' in result.lower():
                            current_function.errors.append(ErrorEvent(
                                timestamp=tool['timestamp'],
                                category=ErrorCategory.CLAIM_CONFLICT,
                                message=result[:200],
                                tool_name='Bash',
                            ))

                # Extract get / create scratch
                elif 'extract get' in cmd and '--create-scratch' in cmd:
                    match = re.search(r'extract get\s+(\w+)', cmd)
                    if match:
                        func_name = match.group(1)
                        if current_function and current_function.function_name == func_name:
                            current_function.stages_completed.append(WorkflowStage.CREATE_SCRATCH)
                            current_function.current_stage = WorkflowStage.CREATE_SCRATCH
                        elif not current_function or current_function.function_name != func_name:
                            # Started working on function without explicit claim
                            current_function = FunctionAttempt(
                                function_name=func_name,
                                started_at=tool['timestamp'],
                            )
                            current_function.stages_completed.append(WorkflowStage.CREATE_SCRATCH)
                            session.functions.append(current_function)

                        # Extract slug from result
                        slug_match = self.SLUG_PATTERN.search(result)
                        if slug_match:
                            current_function.slug = slug_match.group(1) or slug_match.group(2)

                # Scratch compile
                elif 'scratch compile' in cmd:
                    if current_function:
                        current_function.compile_count += 1
                        current_function.turn_count += 1
                        current_function.input_tokens += tool['input_tokens']
                        current_function.output_tokens += tool['output_tokens']

                        if WorkflowStage.ITERATE not in current_function.stages_completed:
                            current_function.stages_completed.append(WorkflowStage.ITERATE)
                        current_function.current_stage = WorkflowStage.ITERATE

                        # Extract match percentage
                        match_match = self.MATCH_PCT_PATTERN.search(result)
                        if match_match:
                            pct = float(match_match.group(1))
                            current_function.match_history.append(MatchProgress(
                                timestamp=tool['timestamp'],
                                match_pct=pct,
                                iteration=current_function.compile_count,
                            ))
                            current_function.final_match_pct = pct

                # Context lookups
                elif 'struct offset' in cmd or 'search-context' in cmd or 'struct show' in cmd:
                    if current_function:
                        current_function.context_lookups += 1
                        if WorkflowStage.READ_CONTEXT not in current_function.stages_completed:
                            current_function.stages_completed.append(WorkflowStage.READ_CONTEXT)

                # Commit apply (with or without dry-run)
                elif 'commit apply' in cmd:
                    if current_function:
                        if '--dry-run' in cmd:
                            current_function.dry_run_used = True
                        else:
                            current_function.stages_completed.append(WorkflowStage.COMMIT)
                            current_function.current_stage = WorkflowStage.COMMIT

                        # Check for build failures
                        if tool['is_error'] or 'error' in result.lower():
                            if 'proto' in result.lower() or 'prototype' in result.lower():
                                current_function.errors.append(ErrorEvent(
                                    timestamp=tool['timestamp'],
                                    category=ErrorCategory.PROTO_MISMATCH,
                                    message=result[:200],
                                    tool_name='Bash',
                                ))
                                current_function.build_passed_first_try = False
                            elif 'undefined' in result.lower() or 'undeclared' in result.lower():
                                current_function.errors.append(ErrorEvent(
                                    timestamp=tool['timestamp'],
                                    category=ErrorCategory.MISSING_CONTEXT,
                                    message=result[:200],
                                    tool_name='Bash',
                                ))
                                current_function.build_passed_first_try = False
                            elif 'syntax' in result.lower():
                                current_function.errors.append(ErrorEvent(
                                    timestamp=tool['timestamp'],
                                    category=ErrorCategory.SYNTAX_ERROR,
                                    message=result[:200],
                                    tool_name='Bash',
                                ))
                                current_function.build_passed_first_try = False

                # Workflow finish (combined commit + complete)
                elif 'workflow finish' in cmd:
                    if current_function:
                        current_function.dry_run_used = True  # workflow finish does dry-run internally
                        current_function.stages_completed.append(WorkflowStage.COMMIT)
                        current_function.stages_completed.append(WorkflowStage.COMPLETE)
                        current_function.current_stage = WorkflowStage.COMPLETE

                        if 'success' in result.lower() or 'committed' in result.lower():
                            current_function.committed = True
                            current_function.finished_at = tool['timestamp']
                        elif tool['is_error'] or 'error' in result.lower() or 'failed' in result.lower():
                            current_function.errors.append(ErrorEvent(
                                timestamp=tool['timestamp'],
                                category=ErrorCategory.BUILD_FAILURE,
                                message=result[:200],
                                tool_name='Bash',
                            ))

                # Complete mark
                elif 'complete mark' in cmd:
                    if current_function:
                        current_function.stages_completed.append(WorkflowStage.COMPLETE)
                        current_function.finished_at = tool['timestamp']
                        if '--committed' in cmd:
                            current_function.committed = True

                # Stub add
                elif 'stub add' in cmd:
                    if current_function:
                        current_function.errors.append(ErrorEvent(
                            timestamp=tool['timestamp'],
                            category=ErrorCategory.MISSING_STUB,
                            message="Missing stub marker",
                            tool_name='Bash',
                        ))

                # Worktree usage detection
                if 'worktree' in cmd or 'melee-worktrees' in result:
                    if current_function:
                        current_function.used_worktree = True

                # Check for committing to main repo warning
                if 'Warning: Committing to main melee repo' in result:
                    if current_function:
                        current_function.worktree_correct = False

                # Server errors
                if 'connection' in result.lower() or 'unreachable' in result.lower() or 'timeout' in result.lower():
                    error = ErrorEvent(
                        timestamp=tool['timestamp'],
                        category=ErrorCategory.SERVER_ERROR,
                        message=result[:200],
                        tool_name='Bash',
                    )
                    if current_function:
                        current_function.errors.append(error)

            # Read tool - context gathering
            elif tool['name'] == 'Read':
                file_path = tool.get('input', {}).get('file_path', '')
                if current_function and 'melee/src' in file_path:
                    if WorkflowStage.READ_CONTEXT not in current_function.stages_completed:
                        current_function.stages_completed.append(WorkflowStage.READ_CONTEXT)

        return session

    def analyze_all(self, since_days: Optional[int] = None, include_subagents: bool = True) -> list[DecompSession]:
        """Analyze all decomp sessions.

        Args:
            since_days: Only analyze sessions from the last N days
            include_subagents: Include agent-* subagent sessions (where actual decomp work happens)
        """
        self.sessions = []

        cutoff = None
        if since_days:
            cutoff = datetime.now() - timedelta(days=since_days)

        for project_dir in self.find_project_dirs():
            for session_file in project_dir.glob('*.jsonl'):
                # Optionally skip subagent sessions
                if not include_subagents and session_file.stem.startswith('agent-'):
                    continue

                try:
                    session = self.analyze_session(session_file)
                    if session:
                        # Apply time filter
                        if cutoff and session.started_at:
                            ts = session.started_at.replace(tzinfo=None) if session.started_at.tzinfo else session.started_at
                            if ts < cutoff:
                                continue
                        self.sessions.append(session)
                except Exception as e:
                    continue

        # Sort by start time
        self.sessions.sort(key=lambda s: s.started_at or datetime.min)

        return self.sessions

    def compute_aggregate_metrics(self) -> AggregateMetrics:
        """Compute aggregate metrics across all analyzed sessions."""
        metrics = AggregateMetrics()

        metrics.total_sessions = len(self.sessions)

        all_functions: list[FunctionAttempt] = []
        for session in self.sessions:
            all_functions.extend(session.functions)

        metrics.total_functions_attempted = len(all_functions)
        metrics.total_functions_completed = sum(1 for f in all_functions if f.committed)

        # Success rates
        if all_functions:
            metrics.overall_success_rate = metrics.total_functions_completed / metrics.total_functions_attempted

            committed_attempts = [f for f in all_functions if f.committed]
            worktree_users = [f for f in all_functions if f.used_worktree]

            if worktree_users:
                metrics.worktree_correct_rate = sum(1 for f in worktree_users if f.worktree_correct) / len(worktree_users)

            metrics.build_first_try_rate = sum(1 for f in all_functions if f.build_passed_first_try) / len(all_functions)
            metrics.dry_run_usage_rate = sum(1 for f in all_functions if f.dry_run_used) / len(all_functions)

        # Efficiency metrics
        total_tokens = sum(f.input_tokens + f.output_tokens for f in all_functions)
        total_turns = sum(f.turn_count for f in all_functions)
        total_iterations = sum(f.compile_count for f in all_functions)

        if all_functions:
            metrics.avg_tokens_per_function = total_tokens / len(all_functions)
            metrics.avg_turns_per_function = total_turns / len(all_functions)
            metrics.avg_iterations_per_function = total_iterations / len(all_functions)

            durations = [f.duration for f in all_functions if f.duration]
            if durations:
                avg_seconds = sum(d.total_seconds() for d in durations) / len(durations)
                metrics.avg_duration_per_function = timedelta(seconds=avg_seconds)

        # Error rates
        all_errors = []
        for f in all_functions:
            all_errors.extend(f.errors)

        metrics.total_errors = len(all_errors)
        if all_functions:
            metrics.errors_per_function = len(all_errors) / len(all_functions)

        for error in all_errors:
            cat = error.category.value
            metrics.errors_by_category[cat] = metrics.errors_by_category.get(cat, 0) + 1

        # Match progression
        initial_matches = [f.match_history[0].match_pct for f in all_functions if f.match_history]
        final_matches = [f.final_match_pct for f in all_functions if f.final_match_pct is not None]

        if initial_matches:
            metrics.avg_initial_match = sum(initial_matches) / len(initial_matches)
        if final_matches:
            metrics.avg_final_match = sum(final_matches) / len(final_matches)
            metrics.final_match_distribution = sorted(final_matches)

        thrashing_count = sum(1 for f in all_functions if f.had_thrashing)
        if all_functions:
            metrics.thrashing_rate = thrashing_count / len(all_functions)

        # Distribution data
        metrics.tokens_distribution = [f.input_tokens + f.output_tokens for f in all_functions]
        metrics.turns_distribution = [f.turn_count for f in all_functions]

        return metrics

    def get_function_details(self) -> list[dict]:
        """Get detailed data for each function attempt."""
        details = []
        for session in self.sessions:
            for func in session.functions:
                details.append({
                    'session_id': session.session_id[:8],
                    'function': func.function_name,
                    'slug': func.slug,
                    'committed': func.committed,
                    'final_match': func.final_match_pct,
                    'iterations': func.compile_count,
                    'tokens': func.input_tokens + func.output_tokens,
                    'turns': func.turn_count,
                    'errors': len(func.errors),
                    'error_types': [e.category.value for e in func.errors],
                    'stages': [s.value for s in func.stages_completed],
                    'duration_mins': func.duration.total_seconds() / 60 if func.duration else None,
                    'thrashing': func.had_thrashing,
                    'dry_run': func.dry_run_used,
                    'worktree': func.used_worktree,
                })
        return details
