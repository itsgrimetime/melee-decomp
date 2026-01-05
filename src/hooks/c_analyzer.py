"""C code analysis using tree-sitter for detecting anti-patterns.

This module provides AST-based detection of C code patterns that should be
avoided in the melee decompilation project, replacing fragile regex matching.

Detected patterns:
- Pointer arithmetic for struct field access (use proper structs instead)
- Lowercase hex literals (should be uppercase)
- Missing F suffix on float literals
- TRUE/FALSE instead of true/false
"""

from dataclasses import dataclass
from typing import Iterator

try:
    import tree_sitter_c as tsc
    from tree_sitter import Language, Parser, Node

    C_LANGUAGE = Language(tsc.language())
    TREE_SITTER_AVAILABLE = True
except ImportError:
    TREE_SITTER_AVAILABLE = False
    C_LANGUAGE = None
    Parser = None
    Node = None


@dataclass
class CodeIssue:
    """A detected code issue."""

    message: str
    line: int
    column: int
    snippet: str
    suggestion: str | None = None


def get_parser() -> "Parser | None":
    """Get a tree-sitter C parser, or None if not available."""
    if not TREE_SITTER_AVAILABLE:
        return None
    parser = Parser(C_LANGUAGE)
    return parser


def _get_node_text(node: "Node", source: bytes) -> str:
    """Extract the source text for a node."""
    return source[node.start_byte : node.end_byte].decode("utf-8")


def _find_nodes_by_type(node: "Node", type_name: str) -> Iterator["Node"]:
    """Recursively find all nodes of a given type."""
    if node.type == type_name:
        yield node
    for child in node.children:
        yield from _find_nodes_by_type(child, type_name)


def _find_nodes_by_types(node: "Node", type_names: set[str]) -> Iterator["Node"]:
    """Recursively find all nodes matching any of the given types."""
    if node.type in type_names:
        yield node
    for child in node.children:
        yield from _find_nodes_by_types(child, type_names)


def _is_pointer_arithmetic(node: "Node", source: bytes) -> bool:
    """Check if a binary expression represents pointer arithmetic.

    Looks for patterns like:
    - (type*)ptr + offset
    - ptr + offset (where ptr is cast)
    """
    if node.type != "binary_expression":
        return False

    # Get the operator
    op_node = None
    for child in node.children:
        if child.type in ("+", "-"):
            op_node = child
            break

    if not op_node:
        return False

    # Check if left side involves a pointer cast or is a pointer type
    left = node.child_by_field_name("left")
    if left is None:
        return False

    # Look for cast expressions to pointer types
    if left.type == "cast_expression":
        type_node = left.child_by_field_name("type")
        if type_node:
            type_text = _get_node_text(type_node, source)
            if "*" in type_text:
                return True

    # Check for parenthesized cast: ((type*)expr)
    if left.type == "parenthesized_expression":
        inner = left.children[1] if len(left.children) > 1 else None
        if inner and inner.type == "cast_expression":
            type_node = inner.child_by_field_name("type")
            if type_node:
                type_text = _get_node_text(type_node, source)
                if "*" in type_text:
                    return True

    return False


def detect_pointer_arithmetic(source_code: str) -> list[CodeIssue]:
    """Detect pointer arithmetic used for struct field access.

    Catches patterns like:
    - *(f32*)((u8*)fp + 0x844)
    - ((u8*)ptr + offset)->field
    - *(type*)(base + offset)
    - ((Type*)(ptr + 0x10))->member

    These should use proper struct definitions or M2C_FIELD macro.
    """
    parser = get_parser()
    if parser is None:
        return []

    issues = []
    source_bytes = source_code.encode("utf-8")
    tree = parser.parse(source_bytes)

    # Find all pointer dereference expressions: *(expr)
    for node in _find_nodes_by_type(tree.root_node, "pointer_expression"):
        # Check if this is a dereference (starts with *)
        if node.children and node.children[0].type == "*":
            operand = node.child_by_field_name("argument")
            if operand is None:
                continue

            # Check for cast + arithmetic pattern
            # Pattern: *(type*)((type*)base + offset)
            if operand.type == "cast_expression":
                cast_value = operand.child_by_field_name("value")
                if cast_value and _is_pointer_arithmetic_expr(cast_value, source_bytes):
                    snippet = _get_node_text(node, source_bytes)
                    issues.append(
                        CodeIssue(
                            message="Pointer arithmetic for struct field access",
                            line=node.start_point[0] + 1,
                            column=node.start_point[1] + 1,
                            snippet=snippet,
                            suggestion="Use proper struct definition or M2C_FIELD macro",
                        )
                    )

            # Also check for parenthesized versions
            elif operand.type == "parenthesized_expression":
                inner = _unwrap_parens(operand)
                if inner and inner.type == "cast_expression":
                    cast_value = inner.child_by_field_name("value")
                    if cast_value and _is_pointer_arithmetic_expr(cast_value, source_bytes):
                        snippet = _get_node_text(node, source_bytes)
                        issues.append(
                            CodeIssue(
                                message="Pointer arithmetic for struct field access",
                                line=node.start_point[0] + 1,
                                column=node.start_point[1] + 1,
                                snippet=snippet,
                                suggestion="Use proper struct definition or M2C_FIELD macro",
                            )
                        )

    # Find field access via pointer arithmetic: ((type*)(base + offset))->field
    for node in _find_nodes_by_type(tree.root_node, "field_expression"):
        # Check if the base is a cast with pointer arithmetic
        argument = node.child_by_field_name("argument")
        if argument is None:
            continue

        base = _unwrap_parens(argument)
        if base and base.type == "cast_expression":
            cast_value = base.child_by_field_name("value")
            if cast_value and _is_pointer_arithmetic_expr(cast_value, source_bytes):
                snippet = _get_node_text(node, source_bytes)
                issues.append(
                    CodeIssue(
                        message="Pointer arithmetic for struct field access",
                        line=node.start_point[0] + 1,
                        column=node.start_point[1] + 1,
                        snippet=snippet,
                        suggestion="Use proper struct definition or M2C_FIELD macro",
                    )
                )

    # Find subscript access with pointer cast: ((u8*)ptr)[offset]
    for node in _find_nodes_by_type(tree.root_node, "subscript_expression"):
        argument = node.child_by_field_name("argument")
        if argument is None:
            continue

        base = _unwrap_parens(argument)
        if base and base.type == "cast_expression":
            type_node = base.child_by_field_name("type")
            if type_node:
                type_text = _get_node_text(type_node, source_bytes)
                # Check if casting to byte pointer (common pattern for offset access)
                if any(t in type_text for t in ("u8*", "s8*", "char*", "uint8_t*")):
                    index = node.child_by_field_name("index")
                    if index:
                        index_text = _get_node_text(index, source_bytes)
                        # Flag if index looks like a struct offset (hex or large number)
                        if "0x" in index_text.lower() or (
                            index_text.isdigit() and int(index_text) > 32
                        ):
                            snippet = _get_node_text(node, source_bytes)
                            issues.append(
                                CodeIssue(
                                    message="Array indexing with byte cast for struct access",
                                    line=node.start_point[0] + 1,
                                    column=node.start_point[1] + 1,
                                    snippet=snippet,
                                    suggestion="Use proper struct definition",
                                )
                            )

    return issues


def _unwrap_parens(node: "Node") -> "Node | None":
    """Unwrap parenthesized expressions to get the inner node."""
    while node and node.type == "parenthesized_expression":
        if len(node.children) > 1:
            node = node.children[1]
        else:
            break
    return node


def _is_pointer_arithmetic_expr(node: "Node", source: bytes) -> bool:
    """Check if an expression involves pointer arithmetic."""
    node = _unwrap_parens(node)
    if node is None:
        return False

    if node.type == "binary_expression":
        return _is_pointer_arithmetic(node, source)

    return False


def detect_lowercase_hex(source_code: str) -> list[CodeIssue]:
    """Detect lowercase hex literals (should be uppercase)."""
    parser = get_parser()
    if parser is None:
        return []

    issues = []
    source_bytes = source_code.encode("utf-8")
    tree = parser.parse(source_bytes)

    for node in _find_nodes_by_type(tree.root_node, "number_literal"):
        text = _get_node_text(node, source_bytes)

        # Check for hex literals
        if text.lower().startswith("0x"):
            hex_part = text[2:]
            # Check if there are lowercase letters in hex digits
            if any(c.islower() and c in "abcdef" for c in hex_part):
                issues.append(
                    CodeIssue(
                        message="Lowercase hex literal",
                        line=node.start_point[0] + 1,
                        column=node.start_point[1] + 1,
                        snippet=text,
                        suggestion=f"Use uppercase: 0x{hex_part.upper()}",
                    )
                )

    return issues


def detect_float_without_suffix(source_code: str) -> list[CodeIssue]:
    """Detect float literals missing the F suffix."""
    parser = get_parser()
    if parser is None:
        return []

    issues = []
    source_bytes = source_code.encode("utf-8")
    tree = parser.parse(source_bytes)

    for node in _find_nodes_by_type(tree.root_node, "number_literal"):
        text = _get_node_text(node, source_bytes)

        # Check for float literals (contains decimal point)
        if "." in text and not text.lower().startswith("0x"):
            # Check if it has F/f/L/l suffix
            if not text[-1].lower() in ("f", "l"):
                issues.append(
                    CodeIssue(
                        message="Float literal missing F suffix",
                        line=node.start_point[0] + 1,
                        column=node.start_point[1] + 1,
                        snippet=text,
                        suggestion=f"Use {text}F for f32",
                    )
                )

    return issues


def detect_uppercase_bool(source_code: str) -> list[CodeIssue]:
    """Detect TRUE/FALSE (should be true/false)."""
    parser = get_parser()
    if parser is None:
        return []

    issues = []
    source_bytes = source_code.encode("utf-8")
    tree = parser.parse(source_bytes)

    # Tree-sitter parses TRUE as 'true' node type and FALSE as 'false' node type
    # We need to check the actual text to see if it's uppercase
    for node in _find_nodes_by_types(tree.root_node, {"true", "false"}):
        text = _get_node_text(node, source_bytes)

        if text == "TRUE":
            issues.append(
                CodeIssue(
                    message="Use lowercase boolean",
                    line=node.start_point[0] + 1,
                    column=node.start_point[1] + 1,
                    snippet=text,
                    suggestion="Use 'true' instead of 'TRUE'",
                )
            )
        elif text == "FALSE":
            issues.append(
                CodeIssue(
                    message="Use lowercase boolean",
                    line=node.start_point[0] + 1,
                    column=node.start_point[1] + 1,
                    snippet=text,
                    suggestion="Use 'false' instead of 'FALSE'",
                )
            )

    return issues


def analyze_c_code(source_code: str) -> list[CodeIssue]:
    """Run all C code analyses and return combined issues."""
    if not TREE_SITTER_AVAILABLE:
        return []

    issues = []
    issues.extend(detect_pointer_arithmetic(source_code))
    issues.extend(detect_lowercase_hex(source_code))
    issues.extend(detect_float_without_suffix(source_code))
    issues.extend(detect_uppercase_bool(source_code))
    return issues


def strip_function_bodies(
    source_code: str,
    keep_functions: set[str] | None = None,
) -> tuple[str, int]:
    """Strip function bodies from C code, keeping only declarations.

    Uses tree-sitter to properly distinguish between:
    - Function definitions (strip body, convert to declaration)
    - Struct/union bodies (keep intact)
    - Typedefs including function pointers (keep intact)

    This is much more accurate than regex-based approaches which can
    accidentally strip struct bodies or mangle typedefs.

    Args:
        source_code: C source code to process
        keep_functions: Optional set of function names to NOT strip

    Returns:
        Tuple of (processed source, number of functions stripped)
    """
    if not TREE_SITTER_AVAILABLE:
        return source_code, 0

    if keep_functions is None:
        keep_functions = set()

    parser = get_parser()
    if parser is None:
        return source_code, 0

    source_bytes = source_code.encode("utf-8")
    tree = parser.parse(source_bytes)

    # Collect all function definitions to strip
    # Each entry: (start_byte, end_byte, replacement_text)
    replacements: list[tuple[int, int, str]] = []
    stripped_count = 0

    for node in _find_nodes_by_type(tree.root_node, "function_definition"):
        # Get the function name from the declarator
        declarator = node.child_by_field_name("declarator")
        if declarator is None:
            continue

        # Navigate to find the actual function name
        func_name = _extract_function_name(declarator, source_bytes)
        if func_name is None:
            continue

        # Skip if this function should be kept
        if func_name in keep_functions:
            continue

        # Get the body (compound_statement)
        body = node.child_by_field_name("body")
        if body is None:
            continue

        # Build the declaration by taking everything before the body
        # and replacing the body with ";"
        decl_end = body.start_byte
        declaration_text = source_bytes[:decl_end].decode("utf-8")

        # Extract just this function's declaration part
        func_start = node.start_byte
        func_decl = source_bytes[func_start:decl_end].decode("utf-8").rstrip()

        # Remove 'inline' and 'static' keywords from declaration
        # - inline: invalid without body in C89
        # - static: MWCC expects a body after static declarations
        import re
        func_decl = re.sub(r'\bstatic\s+', '', func_decl)
        func_decl = re.sub(r'\binline\s+', '', func_decl)

        # Add semicolon and comment (match regex version format)
        replacement = func_decl + ";  /* body stripped: auto-inline prevention */"

        replacements.append((node.start_byte, node.end_byte, replacement))
        stripped_count += 1

    if not replacements:
        return source_code, 0

    # Apply replacements in reverse order to maintain byte offsets
    replacements.sort(key=lambda x: x[0], reverse=True)

    result_bytes = bytearray(source_bytes)
    for start, end, replacement in replacements:
        result_bytes[start:end] = replacement.encode("utf-8")

    return result_bytes.decode("utf-8"), stripped_count


def _extract_function_name(declarator: "Node", source: bytes) -> str | None:
    """Extract the function name from a declarator node.

    Handles various declarator patterns:
    - function_declarator -> identifier
    - pointer_declarator -> function_declarator -> identifier
    - etc.
    """
    # Direct function declarator
    if declarator.type == "function_declarator":
        inner = declarator.child_by_field_name("declarator")
        if inner and inner.type == "identifier":
            return _get_node_text(inner, source)
        elif inner and inner.type == "parenthesized_declarator":
            # Handle (*funcptr)(args) case
            return _extract_function_name(inner, source)
        elif inner:
            return _extract_function_name(inner, source)

    # Pointer declarator: *funcname or **funcname
    if declarator.type == "pointer_declarator":
        inner = declarator.child_by_field_name("declarator")
        if inner:
            return _extract_function_name(inner, source)

    # Parenthesized declarator: (funcname) or (*funcptr)
    if declarator.type == "parenthesized_declarator":
        for child in declarator.children:
            if child.type not in ("(", ")", "*", "pointer_declarator"):
                return _extract_function_name(child, source)
        # Check for pointer_declarator child
        for child in declarator.children:
            if child.type == "pointer_declarator":
                return _extract_function_name(child, source)

    # Direct identifier
    if declarator.type == "identifier":
        return _get_node_text(declarator, source)

    return None


def strip_target_function(source_code: str, func_name: str) -> str:
    """Strip a specific function's definition from source code.

    Preserves:
    - Function declarations (prototypes)
    - Function calls
    - Comments mentioning the function

    Args:
        source_code: C source code
        func_name: Name of the function to strip

    Returns:
        Source with function definition removed
    """
    if not TREE_SITTER_AVAILABLE:
        return source_code

    parser = get_parser()
    if parser is None:
        return source_code

    source_bytes = source_code.encode("utf-8")
    tree = parser.parse(source_bytes)

    # Find the specific function definition
    for node in _find_nodes_by_type(tree.root_node, "function_definition"):
        declarator = node.child_by_field_name("declarator")
        if declarator is None:
            continue

        name = _extract_function_name(declarator, source_bytes)
        if name != func_name:
            continue

        # Found the function - replace with a comment
        # Use // comment format to match regex version
        replacement = f"// {func_name} definition stripped"

        result = (
            source_bytes[: node.start_byte].decode("utf-8")
            + replacement
            + source_bytes[node.end_byte :].decode("utf-8")
        )
        return result

    # Function not found, return unchanged
    return source_code


def analyze_diff_additions(diff: str) -> list[CodeIssue]:
    """Analyze only the added lines from a diff.

    Extracts '+' lines from the diff, reconstructs approximate source,
    and runs analysis. Returns issues with adjusted line numbers.
    """
    if not TREE_SITTER_AVAILABLE:
        return []

    # Extract added lines and track their original line numbers
    added_lines = []
    line_mapping = {}  # reconstructed line -> original line number
    current_new_line = 0

    for line in diff.split("\n"):
        if line.startswith("@@"):
            # Parse hunk header for new file line number
            import re

            match = re.search(r"\+(\d+)", line)
            if match:
                current_new_line = int(match.group(1)) - 1
            continue

        if line.startswith("+") and not line.startswith("+++"):
            current_new_line += 1
            content = line[1:]  # Remove '+' prefix
            reconstructed_line = len(added_lines)
            added_lines.append(content)
            line_mapping[reconstructed_line] = current_new_line
        elif not line.startswith("-"):
            current_new_line += 1

    if not added_lines:
        return []

    # Join lines and analyze
    source = "\n".join(added_lines)
    issues = analyze_c_code(source)

    # Remap line numbers to original file
    for issue in issues:
        reconstructed_line = issue.line - 1  # 0-indexed
        if reconstructed_line in line_mapping:
            issue.line = line_mapping[reconstructed_line]

    return issues
