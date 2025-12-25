"""
Main agent loop for decompilation matching.

DEPRECATED: This module uses a multi-call approach where the LLM is invoked
multiple times without context. This leads to oscillating results and poor
convergence.

RECOMMENDED: Use the /decomp skill in Claude Code instead, which uses MCP
tools directly in a single session with full context maintained.

Example: /decomp fn_80393C14

This module is kept for backwards compatibility but is not recommended.

---

This module orchestrates the process of:
1. Selecting a function to match
2. Creating a scratch on decomp.me (for tracking/web UI)
3. Compiling locally with wine+mwcc
4. Iteratively refining the code until a match is achieved
5. Committing the result
"""

import asyncio
from pathlib import Path
from typing import Optional

from .models import MatchResult, MatchAttempt, MatchingContext
from .strategies import get_strategies_for_diff, Strategy
from .prompts import (
    make_initial_prompt,
    make_refinement_prompt,
    extract_code_from_response,
)
from .llm import LLMClient
from .source_reader import extract_function_source, get_surrounding_context
import re


def extract_local_definitions(source_code: str) -> tuple[str, str]:
    """Extract local type definitions (structs, etc.) from source code.

    Returns:
        Tuple of (local_definitions, function_code)
    """
    if not source_code:
        return "", ""

    # Look for static struct/union definitions at the start
    # These appear before the function definition
    lines = source_code.split('\n')

    def_lines = []
    func_start_idx = 0

    # Find where the function definition starts
    for i, line in enumerate(lines):
        # Look for function definition pattern (return_type func_name(...))
        if re.match(r'^(?:static\s+)?(?:inline\s+)?(?:void|int|float|s32|u32|f32|bool|BOOL)\s+\w+\s*\(', line):
            func_start_idx = i
            break
        def_lines.append(line)

    local_defs = '\n'.join(def_lines).strip()
    func_code = '\n'.join(lines[func_start_idx:]).strip()

    return local_defs, func_code


def ensure_local_definitions(new_code: str, local_defs: str) -> str:
    """Ensure local type definitions are included in the code.

    If the new code doesn't contain the local definitions, prepend them.
    """
    if not local_defs:
        return new_code

    # Check if the key identifier from local_defs is in the new code
    # Extract struct/variable names from local_defs
    struct_names = re.findall(r'\}\s+(\w+)\s*;', local_defs)

    # Check if any of these are missing from the new code definition section
    # (they should be used in the function body, but defined at top)
    new_code_lines = new_code.split('\n')
    has_struct_def = False
    for line in new_code_lines[:10]:  # Check first 10 lines
        if 'struct {' in line or 'struct{' in line:
            has_struct_def = True
            break

    if not has_struct_def and struct_names:
        # Need to prepend the local definitions
        return local_defs + '\n\n' + new_code

    return new_code


# Try to initialize local compiler
_local_compiler = None
_local_compiler_error = None

def _get_local_compiler():
    """Lazily initialize the local compiler."""
    global _local_compiler, _local_compiler_error
    if _local_compiler is not None or _local_compiler_error is not None:
        return _local_compiler

    try:
        from src.compiler import MWCCCompiler
        _local_compiler = MWCCCompiler()
        return _local_compiler
    except FileNotFoundError as e:
        _local_compiler_error = str(e)
        return None
    except Exception as e:
        _local_compiler_error = str(e)
        return None


async def run_matching_agent(
    function_name: Optional[str] = None,
    melee_root: Path = Path("melee"),
    api_url: str = "http://localhost:8000",
    max_iterations: int = 50,
    auto_commit: bool = False,
    llm_client: Optional[LLMClient] = None,
    use_local_compiler: bool = True,
) -> MatchResult:
    """Run the matching agent loop.

    Args:
        function_name: Specific function to match, or None to auto-select
        melee_root: Path to the melee project root
        api_url: URL of the decomp.me API
        max_iterations: Maximum refinement iterations
        auto_commit: Whether to automatically commit on success
        llm_client: LLM client instance (or None to create a new one)
        use_local_compiler: Use local wine+mwcc instead of decomp.me compilation

    Returns:
        MatchResult with the outcome
    """
    # Import here to avoid circular imports
    import sys
    print("DEBUG: Starting agent...", flush=True)
    from src.client import DecompMeAPIClient
    from src.extractor import extract_function, extract_unmatched_functions
    print("DEBUG: Imports done", flush=True)

    # Initialize clients
    print("DEBUG: Creating client...", flush=True)
    client = DecompMeAPIClient(base_url=api_url)
    print("DEBUG: Client created", flush=True)

    # Set up local compiler if requested
    # NOTE: Local compilation is disabled for now because the project headers
    # have complex interdependencies that require full build system setup.
    # The decomp.me context is more reliable for matching.
    local_compiler = None
    if use_local_compiler:
        print("Using decomp.me compilation (local compilation disabled)")

    # Create LLM client if not provided
    print("DEBUG: Creating LLM client...", flush=True)
    if llm_client is None:
        try:
            llm_client = LLMClient()
            print("DEBUG: LLM client created", flush=True)
        except ValueError as e:
            return MatchResult(
                function_name=function_name or "",
                matched=False,
                best_match=0.0,
                error=f"Failed to initialize LLM client: {e}",
            )

    # Get function to work on
    print("DEBUG: Getting function...", flush=True)
    if function_name:
        func_info = await extract_function(melee_root, function_name)
        print("DEBUG: Got function", flush=True)
        if func_info is None:
            return MatchResult(
                function_name=function_name,
                matched=False,
                best_match=0.0,
                error=f"Function '{function_name}' not found",
            )
    else:
        # Auto-select a good candidate
        print("Scanning for unmatched functions...")
        result = await extract_unmatched_functions(
            melee_root=melee_root,
            include_asm=True,
            include_context=True,
        )
        print(f"Found {len(result.functions)} unmatched functions")

        # Filter by size - prefer medium-sized functions
        functions = [
            f for f in result.functions
            if 50 <= f.size_bytes <= 500
            and f.asm is not None  # Must have ASM available
        ]
        print(f"After filtering by size (50-500 bytes) and ASM availability: {len(functions)}")

        # Prefer functions with partial matches if available
        partial_matches = [f for f in functions if 0.3 <= f.current_match <= 0.99]
        if partial_matches:
            functions = partial_matches
            print(f"Found {len(functions)} with partial matches (30-99%)")

        if not functions:
            return MatchResult(
                function_name="",
                matched=False,
                best_match=0.0,
                error="No suitable unmatched functions found. Make sure the melee project is built to generate ASM files.",
            )

        # Pick the best candidate
        # Prioritize: partial match > smaller size (easier to match)
        func_info = max(functions, key=lambda f: (f.current_match, -f.size_bytes))

    # Create matching context
    print(f"DEBUG: Creating context for {func_info.name}...", flush=True)
    print(f"DEBUG: ASM len={len(func_info.asm) if func_info.asm else 0}", flush=True)
    ctx = MatchingContext(
        function_name=func_info.name,
        file_path=func_info.file_path,
        asm=func_info.asm,
        context=func_info.context,
        current_match=func_info.current_match,
        size_bytes=func_info.size_bytes,
        address=func_info.address,
    )
    print("DEBUG: Context created", flush=True)

    print(f"DEBUG: About to print starting message", flush=True)
    import sys
    sys.stdout.flush()
    print(f"Starting matching attempt for: {ctx.function_name}")
    print(f"DEBUG: Starting message printed", flush=True)
    print(f"  File: {ctx.file_path}", flush=True)
    print(f"  Size: {ctx.size_bytes} bytes", flush=True)
    print(f"  Current match: {ctx.current_match * 100:.1f}%", flush=True)

    print("DEBUG: About to create scratch...", flush=True)
    # Create scratch on decomp.me
    # Use the Melee compiler (Metrowerks CodeWarrior for GameCube)
    melee_compiler = "mwcc_233_163n"
    melee_compiler_flags = "-O4,p -nodefaults -fp hard -Cpp_exceptions off -enum int -fp_contract on -inline auto"
    melee_platform = "gc_wii"

    # Basic platform types needed for Melee compilation
    # These are normally in platform.h but local decomp.me may not have presets
    MELEE_BASE_TYPES = """
/* Basic types for Melee decomp */
typedef signed char s8;
typedef signed short s16;
typedef signed int s32;
typedef signed long long s64;
typedef unsigned char u8;
typedef unsigned short u16;
typedef unsigned int u32;
typedef unsigned long long u64;
typedef float f32;
typedef double f64;
typedef int BOOL;
typedef s32 enum_t;
#define NULL ((void*)0)
#define TRUE 1
#define FALSE 0

/* HSD types */
typedef struct HSD_GObj HSD_GObj;
typedef HSD_GObj Fighter_GObj;
typedef struct Fighter Fighter;
typedef struct ftCommonData ftCommonData;
typedef u32 FtMotionId;
typedef u32 MotionFlags;

/* Common macros */
#define GET_FIGHTER(gobj) ((Fighter*)(gobj)->user_data)

/* HSD_GObj structure */
struct HSD_GObj {
    u16 classifier;
    u8 p_link;
    u8 gx_link;
    u8 p_priority;
    u8 render_priority;
    u8 obj_kind;
    u8 user_data_kind;
    struct HSD_GObj* next;
    struct HSD_GObj* prev;
    struct HSD_GObj* next_gx;
    struct HSD_GObj* prev_gx;
    void (*proc)(struct HSD_GObj*);
    void (*render_cb)(struct HSD_GObj*, int);
    u64 gxlink_prios;
    void* hsd_obj;
    void* user_data;
    void (*user_data_remove_func)(void*);
    void* x60_gobjproc;
};

"""

    # Get context for compilation
    # Priority: 1. Generated ctx.c from m2ctx tool, 2. Reference scratch, 3. Base types + project context
    melee_context = ""
    print("DEBUG: Looking for generated context...", flush=True)

    # Try to use the generated context from m2ctx tool (most complete)
    ctx_file = melee_root / "build" / "ctx.c"
    if ctx_file.exists():
        try:
            melee_context = ctx_file.read_text()
            print(f"Using generated context from m2ctx ({len(melee_context)} chars)", flush=True)
        except Exception as e:
            print(f"Warning: Could not read ctx.c: {e}", flush=True)

    # Fall back to fetching from a reference scratch on decomp.me
    if not melee_context:
        print("DEBUG: Fetching reference scratch context...", flush=True)
        try:
            reference_scratch = await client.get_scratch("XMeXG")  # Known good Melee scratch with full context
            print("DEBUG: Got reference scratch", flush=True)
            melee_context = reference_scratch.context
            print(f"Fetched Melee preset context ({len(melee_context)} chars)", flush=True)
        except Exception as e:
            print(f"Warning: Could not fetch preset context: {e}", flush=True)

    # Last resort: use base types + project context
    if not melee_context:
        project_context = ctx.context or ""
        melee_context = MELEE_BASE_TYPES + project_context
        print(f"Using base types + project context ({len(melee_context)} chars)", flush=True)

    print("DEBUG: About to create scratch on decomp.me...", flush=True)
    try:
        from src.client import ScratchCreate
        print("DEBUG: Imported ScratchCreate", flush=True)
        create_params = ScratchCreate(
            name=ctx.function_name,
            target_asm=ctx.asm or "",
            context=melee_context,
            source_code="// Initial placeholder\n",
            diff_label=ctx.function_name,
            compiler=melee_compiler,
            compiler_flags=melee_compiler_flags,
            platform=melee_platform,
        )
        print(f"DEBUG: ScratchCreate params ready, target_asm len={len(create_params.target_asm)}", flush=True)
        scratch = await client.create_scratch(create_params)
        print("DEBUG: Scratch created", flush=True)
        ctx.scratch_slug = scratch.slug
        print(f"Created scratch: {api_url}/scratch/{scratch.slug}", flush=True)

        # Claim ownership of the scratch so we can update it
        print("DEBUG: Claiming scratch...", flush=True)
        if scratch.claim_token:
            claimed = await client.claim_scratch(scratch.slug, scratch.claim_token)
            if claimed:
                print("Claimed scratch ownership", flush=True)
            else:
                print("Warning: Failed to claim scratch", flush=True)
        print("DEBUG: Scratch claim done", flush=True)
    except Exception as e:
        print(f"DEBUG: Scratch creation exception: {e}", flush=True)
        return MatchResult(
            function_name=ctx.function_name,
            matched=False,
            best_match=0.0,
            error=f"Failed to create scratch: {e}",
        )

    print("DEBUG: Getting initial code from project...", flush=True)
    # Try to get initial code from the project's existing source first
    # This is much better than m2c since it has the right types and patterns
    existing_source = extract_function_source(
        ctx.file_path, ctx.function_name, melee_root
    )
    print(f"DEBUG: existing_source len={len(existing_source) if existing_source else 0}", flush=True)
    surrounding_context = get_surrounding_context(
        ctx.file_path, ctx.function_name, melee_root
    )
    print(f"DEBUG: surrounding_context len={len(surrounding_context) if surrounding_context else 0}", flush=True)

    # Extract local type definitions (structs, etc.) that need to be preserved
    # when the LLM refines the code
    local_definitions = ""
    if existing_source:
        local_definitions, _ = extract_local_definitions(existing_source)
        if local_definitions:
            print(f"DEBUG: Extracted local definitions ({len(local_definitions)} chars)", flush=True)

    initial_code = None

    if existing_source:
        print("DEBUG: Testing if existing source compiles...", flush=True)
        print(f"Got existing source code ({len(existing_source)} chars)")
        # Test if existing source compiles with decomp.me context (use source override)
        try:
            from src.client import CompileRequest, ScratchUpdate
            print("DEBUG: Compiling with existing source...", flush=True)
            test_result = await client.compile_scratch(
                scratch.slug,
                overrides=CompileRequest(source_code=existing_source),
            )
            print(f"DEBUG: Compilation done, success={test_result.success}", flush=True)
            if test_result.success:
                initial_code = existing_source
                print("Existing source compiles successfully", flush=True)
                # Update scratch with the compilable source code
                try:
                    await client.update_scratch(scratch.slug, ScratchUpdate(source_code=existing_source))
                    print("Updated scratch source code", flush=True)
                except Exception as e:
                    print(f"Note: Could not update scratch source: {e}", flush=True)
            else:
                print("Existing source has compilation errors, trying m2c...", flush=True)
        except Exception as e:
            print(f"Failed to test existing source: {e}", flush=True)

    if initial_code is None:
        # Fall back to m2c decompilation
        print("Trying m2c decompilation...")
        try:
            decompiled = await client.decompile_scratch(scratch.slug)
            m2c_code = decompiled.decompilation

            # Check if decompilation produced valid code
            # m2c outputs invalid syntax markers when context parsing fails
            has_syntax_errors = (
                "Decompilation failure" in m2c_code
                or "? " in m2c_code  # Unknown type marker
                or "M2C_UNK" in m2c_code  # Another unknown marker
            )

            if has_syntax_errors:
                print("m2c decompilation has syntax issues, using basic placeholder")
                # Use a minimal compilable placeholder that the LLM will refine
                initial_code = f"""// Auto-generated placeholder for {ctx.function_name}
// TODO: Decompile this function based on the assembly

void {ctx.function_name}(void) {{
    // Placeholder - LLM will refine this
}}
"""
            else:
                initial_code = m2c_code
                print("Got initial m2c decompilation")
        except Exception as e:
            # Fall back to empty implementation
            initial_code = f"void {ctx.function_name}(void) {{\n    // TODO: implement\n}}"
            print(f"Using placeholder code (m2c failed: {e})")

    # Main iteration loop
    print("DEBUG: Setting up iteration loop...", flush=True)
    current_code = initial_code
    print(f"DEBUG: current_code len={len(current_code) if current_code else 0}", flush=True)
    best_match = 0.0
    strategies = get_strategies_for_diff({})
    print(f"DEBUG: Got {len(strategies)} strategies", flush=True)

    # Track errors for stuck detection
    recent_errors: list[str] = []
    consecutive_same_error = 0
    last_error_hash = ""

    print("DEBUG: Starting iteration loop...", flush=True)
    for iteration in range(max_iterations):
        print(f"\n--- Iteration {iteration + 1}/{max_iterations} ---", flush=True)

        # Compile with source override (avoids PATCH permission issues on local server)
        print("DEBUG: Compiling with source override...", flush=True)
        compile_success = False
        compiler_output = ""
        score = 0
        max_score = 0
        match_pct = 0.0
        diff_result = None  # Local diff result for better error messages

        if local_compiler:
            # Use local wine+mwcc compilation with project headers
            try:
                from src.compiler import diff_asm

                # Build include directories from the melee project
                # The project structure is:
                # melee/src/ - main source (contains melee/, sysdolphin/, MSL/, etc.)
                # melee/extern/dolphin/include/ - Dolphin SDK headers
                # Note: Must be absolute paths since compilation happens in a temp dir
                include_dirs = [
                    (melee_root / "src").resolve(),
                    (melee_root / "src" / "melee").resolve(),
                    (melee_root / "extern" / "dolphin" / "include").resolve(),
                ]
                # Filter to existing directories
                include_dirs = [d for d in include_dirs if d.exists()]

                # Read the actual source file to get its includes
                # This ensures we use the exact same includes as the real file
                context = ""
                if ctx.file_path:
                    source_file = melee_root / "src" / ctx.file_path
                    if source_file.exists():
                        try:
                            file_content = source_file.read_text()
                            # Extract all #include lines from the beginning of the file
                            import re
                            include_lines = []
                            for line in file_content.split('\n'):
                                if line.strip().startswith('#include'):
                                    include_lines.append(line)
                                elif line.strip() and not line.strip().startswith('//') and not line.strip().startswith('/*'):
                                    # Stop at first non-include, non-comment line
                                    break
                            context = '\n'.join(include_lines) + '\n'
                        except Exception:
                            pass

                # If no context from file, use minimal fallback
                if not context:
                    context = """
typedef signed char s8;
typedef unsigned char u8;
typedef signed short s16;
typedef unsigned short u16;
typedef signed int s32;
typedef unsigned int u32;
typedef float f32;
typedef double f64;
"""

                compile_result, asm_output = await local_compiler.compile_and_get_asm(
                    source_code=current_code,
                    context=context,
                    include_dirs=include_dirs if include_dirs else None,
                )

                if not compile_result.success:
                    compiler_output = compile_result.error_output
                    print(f"Compilation error: {compiler_output[:500]}")

                    # Track error for stuck detection
                    error_hash = hash(compiler_output[:200])
                    if error_hash == last_error_hash:
                        consecutive_same_error += 1
                    else:
                        consecutive_same_error = 1
                        last_error_hash = error_hash

                    # If stuck on same error 3+ times, try drastic recovery
                    if consecutive_same_error >= 3:
                        print(f"Stuck on same error {consecutive_same_error} times, trying recovery...")
                        # Reset to existing source if available
                        if existing_source and current_code != existing_source:
                            current_code = existing_source
                            consecutive_same_error = 0
                            print("Reset to existing source code")
                        else:
                            # Add surrounding context to help LLM understand types
                            if surrounding_context:
                                print("Adding surrounding file context to prompt")
                    continue

                compile_success = True
                compiler_output = compile_result.warnings
                consecutive_same_error = 0  # Reset on success

                # Diff against target assembly
                if asm_output and ctx.asm:
                    diff_result = diff_asm(ctx.asm, asm_output)
                    score = diff_result.score
                    max_score = diff_result.max_score
                    match_pct = diff_result.match_percent / 100.0
                else:
                    print("Could not get assembly for diffing")
                    continue

            except Exception as e:
                print(f"Local compilation failed: {e}")
                continue
        else:
            # Fall back to decomp.me compilation with source override
            print(f"DEBUG: Remote compiling scratch {scratch.slug}...", flush=True)
            try:
                from src.client import CompileRequest
                result = await client.compile_scratch(
                    scratch.slug,
                    overrides=CompileRequest(source_code=current_code),
                )
                print(f"DEBUG: Remote compile done, success={result.success}", flush=True)
            except Exception as e:
                print(f"Compilation failed: {e}", flush=True)
                continue

            print(f"DEBUG: Checking result.success...", flush=True)
            if not result.success:
                print(f"Compilation error: {result.compiler_output[:500]}", flush=True)

                # Track error for stuck detection
                error_hash = hash(result.compiler_output[:200])
                if error_hash == last_error_hash:
                    consecutive_same_error += 1
                else:
                    consecutive_same_error = 1
                    last_error_hash = error_hash

                # If stuck on same error 3+ times, try drastic recovery
                if consecutive_same_error >= 3:
                    print(f"Stuck on same error {consecutive_same_error} times, trying recovery...")
                    # Reset to existing source if available
                    if existing_source and current_code != existing_source:
                        current_code = existing_source
                        consecutive_same_error = 0
                        print("Reset to existing source code")
                continue

            print(f"DEBUG: diff_output={result.diff_output is not None}", flush=True)
            if result.diff_output is None:
                print("No diff output", flush=True)
                continue

            print("DEBUG: Parsing diff output...", flush=True)
            compile_success = True
            compiler_output = result.compiler_output
            consecutive_same_error = 0  # Reset on success
            score = result.diff_output.current_score
            max_score = result.diff_output.max_score
            match_pct = 1.0 if score == 0 else (1.0 - score / max_score) if max_score > 0 else 0.0
            print(f"DEBUG: Diff parsed, score={score}/{max_score}", flush=True)

        print(f"Score: {score}/{max_score} ({match_pct * 100:.1f}% match)", flush=True)

        print("DEBUG: Recording attempt...", flush=True)
        # Record attempt
        attempt = MatchAttempt(
            iteration=iteration,
            source_code=current_code,
            score=score,
            max_score=max_score,
            match_percent=match_pct,
            compiler_output=compiler_output,
        )
        ctx.attempts.append(attempt)

        # Track best
        if match_pct > best_match:
            best_match = match_pct
            ctx.best_attempt = attempt
            # Update scratch with the better source code
            try:
                from src.client import ScratchUpdate
                await client.update_scratch(scratch.slug, ScratchUpdate(source_code=current_code))
                print(f"Updated scratch with {match_pct*100:.1f}% match code", flush=True)
            except Exception as e:
                print(f"Note: Could not update scratch source: {e}", flush=True)

        # Check for success
        if score == 0:
            print("\n*** 100% MATCH ACHIEVED! ***")

            pr_url = None
            if auto_commit:
                from src.commit import auto_detect_and_commit

                try:
                    pr_url = await auto_detect_and_commit(
                        function_name=ctx.function_name,
                        new_code=current_code,
                        scratch_id=scratch.slug,
                        scratch_url=f"{api_url}/scratch/{scratch.slug}",
                        melee_root=melee_root,
                        author="agent",
                        create_pull_request=True,
                    )
                except Exception as e:
                    print(f"Failed to commit: {e}")

            return MatchResult(
                function_name=ctx.function_name,
                matched=True,
                best_match=1.0,
                scratch_slug=scratch.slug,
                iterations=iteration + 1,
                pr_url=pr_url,
            )

        print("DEBUG: Checked for success, continuing...", flush=True)
        # Get next strategy
        strategy = strategies[iteration % len(strategies)] if strategies else None
        strategy_hint = strategy.prompt_hint if strategy else "Try different approaches"
        print(f"DEBUG: Strategy: {strategy.name if strategy else 'None'}", flush=True)

        # Generate refinement prompt
        print("DEBUG: Generating refinement prompt...", flush=True)
        # Build diff snippet based on compilation mode
        if diff_result is not None:
            diff_snippet = f"Match: {diff_result.match_percent:.1f}%\n"
            for line_num, target, current in diff_result.diff_lines[:10]:
                diff_snippet += f"  Line {line_num}: target='{target}' current='{current}'\n"
        else:
            diff_snippet = "No detailed diff available"

        # Include surrounding context when there are type errors or we're stuck
        include_context = (
            consecutive_same_error >= 2
            or (compiler_output and "undefined" in compiler_output.lower())
        )

        prompt = make_refinement_prompt(
            function_name=ctx.function_name,
            current_code=current_code,
            score=score,
            max_score=max_score,
            compiler_output=compiler_output,
            diff_snippet=diff_snippet,
            strategy_hint=strategy_hint,
            surrounding_context=surrounding_context if include_context else "",
        )

        # Call LLM to get refined code
        print(f"Calling LLM for refinement (prompt: {len(prompt)} chars)...", flush=True)
        print("DEBUG: About to call LLM...", flush=True)
        try:
            llm_response = await llm_client.generate_code(prompt)
            print(f"DEBUG: LLM response received, len={len(llm_response) if llm_response else 0}", flush=True)
            if llm_response is None:
                print("LLM returned no response, skipping iteration", flush=True)
                continue

            # Extract code from response
            new_code = extract_code_from_response(llm_response)
            if new_code:
                # Ensure local type definitions are preserved
                new_code = ensure_local_definitions(new_code, local_definitions)
                print(f"Got refined code ({len(new_code)} chars)")
                current_code = new_code
                # Record the strategy used
                if ctx.attempts:
                    ctx.attempts[-1].strategy_used = strategy.name if strategy else "general"
            else:
                print("Could not extract code from LLM response")
                # Still continue - maybe try again with different strategy

        except RuntimeError as e:
            print(f"LLM CLI error: {e}")
            # CLI errors might be recoverable, try to continue
            continue
        except Exception as e:
            print(f"Unexpected error during LLM call: {e}")
            # Try to continue with next iteration
            continue

    return MatchResult(
        function_name=ctx.function_name,
        matched=False,
        best_match=best_match,
        scratch_slug=ctx.scratch_slug,
        iterations=len(ctx.attempts),
    )


async def select_best_candidate(
    melee_root: Path,
    min_size: int = 50,
    max_size: int = 500,
) -> Optional[str]:
    """Select the best function candidate for matching.

    Prioritizes:
    1. Functions with high partial match (easier to complete)
    2. Medium-sized functions (not too simple, not too complex)
    3. Functions in well-understood modules

    Args:
        melee_root: Path to melee project
        min_size: Minimum function size
        max_size: Maximum function size

    Returns:
        Function name or None
    """
    from src.extractor import extract_unmatched_functions

    result = await extract_unmatched_functions(
        melee_root=melee_root,
        include_asm=False,
        include_context=False,
    )

    # Filter by size and match criteria
    functions = [
        f for f in result.functions
        if min_size <= f.size_bytes <= max_size
        and 0.3 <= f.current_match <= 0.99
    ]

    if not functions:
        return None

    # Score candidates
    def score_candidate(func):
        score = 0.0
        # Higher partial match is better
        score += func.current_match * 50
        # Prefer medium size
        size_factor = 1.0 - abs(func.size_bytes - 200) / 300
        score += max(0, size_factor) * 20
        # Prefer certain modules (lb, ft are well-understood)
        if "/lb/" in func.file_path or "/ft/" in func.file_path:
            score += 10
        return score

    best = max(functions, key=score_candidate)
    return best.name
