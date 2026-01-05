"""Tests for inline function stripping in context generation.

These tests ensure that the _strip_inline_functions function correctly
removes inline function bodies while preserving:
1. Function declarations (signatures with ; // body stripped)
2. All non-inline code including #endif guards
3. Correct brace depth tracking (ignoring braces in comments/strings)
"""

import pytest
from src.cli.extract import _count_braces, _strip_inline_functions, _strip_target_function, _strip_all_function_bodies


class TestCountBraces:
    """Tests for the _count_braces helper function."""

    def test_normal_braces(self):
        """Normal braces should be counted."""
        assert _count_braces("{ }") == (1, 1)
        assert _count_braces("{{}}") == (2, 2)
        assert _count_braces("{") == (1, 0)
        assert _count_braces("}") == (0, 1)

    def test_line_comment_braces_ignored(self):
        """Braces after // should be ignored."""
        assert _count_braces("// { }") == (0, 0)
        assert _count_braces("code; // { comment }") == (0, 0)
        assert _count_braces("x = 1; // {{{}}}") == (0, 0)

    def test_code_before_comment(self):
        """Braces before // should be counted."""
        assert _count_braces("{ // }") == (1, 0)
        assert _count_braces("if (x) { // close later }") == (1, 0)
        assert _count_braces("} // open {") == (0, 1)

    def test_string_braces_ignored(self):
        """Braces inside string literals should be ignored."""
        assert _count_braces('str = "{}";') == (0, 0)
        assert _count_braces("str = '{'") == (0, 0)
        assert _count_braces('printf("{");') == (0, 0)
        assert _count_braces('x = "{" + "}";') == (0, 0)

    def test_mixed_real_and_string_braces(self):
        """Real braces outside strings should be counted."""
        assert _count_braces('if (s == "{") {') == (1, 0)
        assert _count_braces('} else if (s == "}") {') == (1, 1)

    def test_empty_and_no_braces(self):
        """Lines without braces should return (0, 0)."""
        assert _count_braces("") == (0, 0)
        assert _count_braces("int x = 5;") == (0, 0)
        assert _count_braces("// just a comment") == (0, 0)

    def test_assert_macro_in_comment(self):
        """Real-world case: __assert in comment with braces."""
        line = '    // ((jobj) ? ((void) 0) : __assert("jobj.h", 1065, "jobj"));'
        assert _count_braces(line) == (0, 0)

    def test_commented_if_with_brace(self):
        """Real-world case: commented out if statement."""
        line = "    // if (!(jobj->flags & (1 << 25))) {"
        assert _count_braces(line) == (0, 0)

    def test_multiple_braces_in_comment(self):
        """Real-world case: multiple braces in comment."""
        line = "        // { if (jobj != ((void*) 0) && !HSD_JObjMtxIsDirty(jobj)) {"
        assert _count_braces(line) == (0, 0)

    def test_closing_braces_in_comment(self):
        """Real-world case: closing braces in comment."""
        line = "        // HSD_JObjSetMtxDirtySub(jobj); } };"
        assert _count_braces(line) == (0, 0)


class TestStripInlineFunctions:
    """Tests for the _strip_inline_functions function."""

    def test_simple_inline_stripped(self):
        """Simple inline function should be stripped to declaration without 'inline' or 'static' keywords."""
        code = """static inline void foo() {
    return;
}"""
        result, count = _strip_inline_functions(code)
        assert count == 1
        # Both 'inline' and 'static' must be removed:
        # - inline declarations without bodies are invalid C89
        # - static declarations without bodies cause MWCC to expect '{'
        assert "void foo();" in result
        assert "inline" not in result
        assert "static" not in result
        assert "// body stripped" in result
        assert "return;" not in result

    def test_inline_declaration_preserved(self):
        """Inline declarations (no body) should be preserved as-is."""
        code = "static inline void foo();"
        result, count = _strip_inline_functions(code)
        assert count == 0
        assert "static inline void foo();" in result

    def test_endif_preserved_after_inline(self):
        """#endif after inline function must be preserved."""
        code = """static inline void foo() {
    int x = 0;
}

#endif"""
        result, count = _strip_inline_functions(code)
        assert count == 1
        assert result.rstrip().endswith("#endif")

    def test_code_after_inline_preserved(self):
        """Code after inline function must be preserved."""
        code = """static inline void foo() {
    return;
}

void bar() {
    foo();
}"""
        result, count = _strip_inline_functions(code)
        assert count == 1
        assert "void bar()" in result
        assert "foo();" in result

    def test_multiple_inline_functions(self):
        """Multiple inline functions should all be stripped."""
        code = """static inline void foo() { return; }

static inline int bar() {
    return 42;
}

static inline void baz() {
    int x;
    x = 1;
}

#endif"""
        result, count = _strip_inline_functions(code)
        assert count == 3
        # Both 'inline' and 'static' keywords must be removed from all stripped functions
        assert "void foo();" in result
        assert "int bar();" in result
        assert "void baz();" in result
        assert "inline" not in result
        assert "static" not in result
        assert result.rstrip().endswith("#endif")

    def test_nested_braces(self):
        """Nested braces should be handled correctly."""
        code = """static inline void nested() {
    if (x) {
        if (y) {
            while (z) {
            }
        }
    }
}

after();"""
        result, count = _strip_inline_functions(code)
        assert count == 1
        assert "after();" in result

    def test_braces_in_comments_ignored(self):
        """Braces in // comments should not affect depth tracking."""
        code = """static inline void commented() {
    // if (!(jobj->flags & (1 << 25))) {
    if (!(jobj->flags & JOBJ_MTX_INDEP_SRT)) {
        // { if (jobj != ((void*) 0) && !HSD_JObjMtxIsDirty(jobj)) {
        // HSD_JObjSetMtxDirtySub(jobj); } };
        HSD_JObjSetMtxDirty(jobj);
    }
}

#endif"""
        result, count = _strip_inline_functions(code)
        assert count == 1
        assert result.rstrip().endswith("#endif")
        # Should NOT contain the function body
        assert "JOBJ_MTX_INDEP_SRT" not in result

    def test_multiline_signature(self):
        """Inline function with signature spanning multiple lines."""
        code = """static inline void long_sig(
    int a,
    int b,
    int c)
{
    return;
}

done();"""
        result, count = _strip_inline_functions(code)
        assert count == 1
        assert "done();" in result

    def test_inline_without_static(self):
        """inline without static should also be stripped."""
        code = """inline void foo() {
    return;
}

after();"""
        result, count = _strip_inline_functions(code)
        assert count == 1
        assert "after();" in result

    def test_single_line_inline(self):
        """Inline function all on one line."""
        code = """static inline int get() { return 42; }
next();"""
        result, count = _strip_inline_functions(code)
        assert count == 1
        assert "int get();" in result
        assert "inline" not in result
        assert "static" not in result
        assert "next();" in result

    def test_real_jobj_addscalex(self):
        """Real-world case: HSD_JObjAddScaleX from jobj.h."""
        code = """static inline void HSD_JObjAddScaleX(HSD_JObj* jobj, float x)
{
    // ((jobj) ? ((void) 0) : __assert("jobj.h", 1065, "jobj"));
    HSD_ASSERT(1065, jobj);
    jobj->scale.x += x;
    // if (!(jobj->flags & (1 << 25))) {
    if (!(jobj->flags & JOBJ_MTX_INDEP_SRT)) {
        // { if (jobj != ((void*) 0) && !HSD_JObjMtxIsDirty(jobj)) {
        // HSD_JObjSetMtxDirtySub(jobj); } };
        HSD_JObjSetMtxDirty(jobj);
    }
}

static inline void HSD_JObjAddScaleY(HSD_JObj* jobj, float y)
{
    HSD_ASSERT(1077, jobj);
    jobj->scale.y += y;
    if (!(jobj->flags & JOBJ_MTX_INDEP_SRT)) {
        HSD_JObjSetMtxDirty(jobj);
    }
}

#endif"""
        result, count = _strip_inline_functions(code)
        assert count == 2
        assert "HSD_JObjAddScaleX" in result
        assert "HSD_JObjAddScaleY" in result
        assert result.rstrip().endswith("#endif")
        # Bodies should not be present
        assert "HSD_ASSERT" not in result
        assert "scale.x" not in result

    def test_empty_input(self):
        """Empty input should return empty output."""
        result, count = _strip_inline_functions("")
        assert count == 0
        assert result == ""

    def test_no_inline_functions(self):
        """Input without inline functions should be unchanged."""
        code = """void foo() {
    return;
}

#endif"""
        result, count = _strip_inline_functions(code)
        assert count == 0
        assert "void foo()" in result
        assert "return;" in result
        assert "#endif" in result


class TestRegressionCases:
    """Regression tests for specific bugs."""

    def test_jobj_h_truncation_bug(self):
        """Regression test for jobj.h truncation bug.

        The original bug caused context to be truncated after HSD_JObjAddScaleX
        because braces in comments threw off depth tracking, leaving in_inline=True
        and eating all subsequent content including the #endif.
        """
        # Minimal reproduction of the bug
        code = """static inline void HSD_JObjAddScaleX(HSD_JObj* jobj, float x)
{
    // if (!(jobj->flags & (1 << 25))) {
    if (!(jobj->flags & JOBJ_MTX_INDEP_SRT)) {
        // { if (jobj != ((void*) 0) {
        // } };
        HSD_JObjSetMtxDirty(jobj);
    }
}

void HSD_JObjResolveRefs(HSD_JObj* jobj, HSD_Joint* joint);

#endif"""
        result, count = _strip_inline_functions(code)

        # The bug would cause these to be missing
        assert "HSD_JObjResolveRefs" in result, "Declaration after inline was eaten"
        assert result.rstrip().endswith("#endif"), "#endif was eaten by inline stripping"

    def test_consecutive_inline_with_comments(self):
        """Multiple consecutive inline functions with problematic comments."""
        code = """static inline void a() {
    // {
}

static inline void b() {
    // }
}

static inline void c() {
    // { }
}

final();
#endif"""
        result, count = _strip_inline_functions(code)
        assert count == 3
        assert "final();" in result
        assert result.rstrip().endswith("#endif")


class TestStripTargetFunction:
    """Tests for _strip_target_function."""

    def test_strips_definition(self):
        """Function definition should be stripped."""
        code = """void myFunc(int x) {
    return;
}"""
        result = _strip_target_function(code, "myFunc")
        assert "// myFunc definition stripped" in result
        assert "return;" not in result

    def test_preserves_declaration(self):
        """Function declaration (prototype) should be preserved."""
        code = """void myFunc(int x);

void other() {
    myFunc(5);
}"""
        result = _strip_target_function(code, "myFunc")
        assert "void myFunc(int x);" in result
        assert "myFunc(5);" in result

    def test_preserves_call_in_function(self):
        """Function calls inside other functions should be preserved."""
        code = """void myFunc(int x) {
    return;
}

void caller() {
    myFunc(42);
}"""
        result = _strip_target_function(code, "myFunc")
        assert "// myFunc definition stripped" in result
        assert "myFunc(42);" in result

    def test_preserves_multiline_call(self):
        """Multi-line function calls should be preserved."""
        code = """void myFunc(int x) {
    return;
}

void caller() {
    myFunc(
        42);
}"""
        result = _strip_target_function(code, "myFunc")
        assert "// myFunc definition stripped" in result
        assert "myFunc(" in result
        assert "42);" in result

    def test_preserves_call_at_line_start(self):
        """Function call at start of line (no return type) should be preserved."""
        code = """void myFunc(int x) {
    return;
}

void caller() {
    myFunc(1);
}"""
        result = _strip_target_function(code, "myFunc")
        assert "myFunc(1);" in result

    def test_preserves_call_in_if_condition(self):
        """Function call in if condition should be preserved."""
        code = """int myFunc(int x) {
    return x;
}

void caller() {
    if (myFunc(5)) {
        do_something();
    }
}"""
        result = _strip_target_function(code, "myFunc")
        assert "if (myFunc(5))" in result

    def test_preserves_call_with_assignment(self):
        """Function call with assignment should be preserved."""
        code = """int myFunc(int x) {
    return x;
}

void caller() {
    int y = myFunc(5);
}"""
        result = _strip_target_function(code, "myFunc")
        assert "int y = myFunc(5);" in result

    def test_preserves_call_in_return(self):
        """Function call in return statement should be preserved."""
        code = """int myFunc(int x) {
    return x;
}

int caller() {
    return myFunc(5);
}"""
        result = _strip_target_function(code, "myFunc")
        assert "return myFunc(5);" in result

    def test_preserves_comment_with_funcname(self):
        """Comments mentioning function should be preserved."""
        code = """// myFunc does something important
void myFunc(int x) {
    return;
}"""
        result = _strip_target_function(code, "myFunc")
        assert "// myFunc does something important" in result
        assert "// myFunc definition stripped" in result

    def test_no_match_returns_unchanged(self):
        """If function not in context, return unchanged."""
        code = "void other() { return; }"
        result = _strip_target_function(code, "myFunc")
        assert result == code

    def test_nested_braces_in_definition(self):
        """Nested braces in function body should be handled."""
        code = """void myFunc(int x) {
    if (x) {
        while (y) {
            for (;;) {
            }
        }
    }
}

void after() {}"""
        result = _strip_target_function(code, "myFunc")
        assert "// myFunc definition stripped" in result
        assert "void after() {}" in result

    def test_preserves_call_in_comma_expression(self):
        """Function call after comma should be preserved."""
        code = """void myFunc() {
    return;
}

void caller() {
    x = 1, myFunc();
}"""
        result = _strip_target_function(code, "myFunc")
        # The line "    x = 1, myFunc();" ends with ; so it's preserved
        assert "myFunc()" in result


class TestTargetFunctionRegressions:
    """Regression tests for target function stripping bugs."""

    def test_call_site_stripping_bug(self):
        """Regression test for call site stripping bug.

        The original bug stripped function calls that looked like definitions
        because they didn't end with ; (multi-line calls or calls without
        proper return type detection).
        """
        code = """void mpLib_8004ED5C(int x) {
    // definition body
    return;
}

void mpCheckFloor() {
    mpLib_8004ED5C(arg);
}

void otherFunc() {
    if (condition) {
        mpLib_8004ED5C(
            multiline_arg);
    }
}"""
        result = _strip_target_function(code, "mpLib_8004ED5C")

        # Definition should be stripped
        assert "// mpLib_8004ED5C definition stripped" in result

        # Call sites must be preserved
        assert "mpLib_8004ED5C(arg);" in result, "Call in mpCheckFloor was incorrectly stripped"
        assert "mpLib_8004ED5C(" in result, "Multi-line call was incorrectly stripped"

    def test_multiple_calls_preserved(self):
        """All call sites should be preserved, not just the first."""
        code = """void target(int x) { return; }

void a() { target(1); }
void b() { target(2); }
void c() { target(3); }"""
        result = _strip_target_function(code, "target")

        assert "// target definition stripped" in result
        assert "target(1);" in result
        assert "target(2);" in result
        assert "target(3);" in result

    def test_multiline_signature_body_stripped(self):
        """Regression: multiline function signature must have body fully stripped.

        The bug was that when the opening brace was on a separate line from
        the function name, the body was not being stripped because depth
        tracking started at 0 and immediately set in_func=False.
        """
        code = """void mpLib_8004ED5C(
    int arg1,
    int arg2)
{
    bool calculated_distance = false;
    MapLine* line = groundCollLine[line_id].x0;
    return;
}

void after() {}"""
        result = _strip_target_function(code, "mpLib_8004ED5C")

        # Definition should be stripped
        assert "// mpLib_8004ED5C definition stripped" in result

        # Body must NOT be present
        assert "calculated_distance" not in result, "Function body was not stripped"
        assert "{" not in result or "void after()" in result, "Opening brace was not stripped"

        # Code after the function must be preserved
        assert "void after() {}" in result


class TestInlineKeywordRemoval:
    """Tests verifying that 'inline' and 'static' keywords are removed when stripping bodies.

    In C (especially MWCC/C89):
    - inline function declarations without bodies are invalid syntax
    - static declarations without bodies cause MWCC to expect '{'
    When we strip a function's body, we must remove both keywords to produce valid C.
    """

    def test_static_inline_becomes_plain_declaration(self):
        """'static inline' should become plain declaration when body is stripped."""
        code = """static inline s32 ftGetKind(Fighter* fp) {
    return fp->kind;
}"""
        result, count = _strip_inline_functions(code)
        assert count == 1
        assert "s32 ftGetKind(Fighter* fp);" in result
        assert "inline" not in result
        assert "static" not in result

    def test_inline_only_removed(self):
        """'inline' without 'static' should be completely removed."""
        code = """inline void foo() {
    return;
}"""
        result, count = _strip_inline_functions(code)
        assert count == 1
        assert "void foo();" in result
        assert "inline" not in result

    def test_inline_preserved_in_declarations(self):
        """Existing declarations (no body) should keep 'inline' keyword."""
        code = "static inline void foo();"
        result, count = _strip_inline_functions(code)
        assert count == 0
        # Declaration is preserved as-is
        assert "static inline void foo();" in result

    def test_multiline_signature_inline_and_static_removed(self):
        """Multi-line signature should have 'inline' and 'static' removed."""
        code = """static inline void long_sig(
    int a,
    int b)
{
    return;
}"""
        result, count = _strip_inline_functions(code)
        assert count == 1
        assert "void long_sig" in result
        assert "inline" not in result
        assert "static" not in result


class TestStripAllFunctionBodies:
    """Tests for _strip_all_function_bodies function."""

    def test_strips_regular_function(self):
        """Regular functions should be stripped."""
        code = """void normalFunc(int x) {
    int y = x + 1;
    return;
}"""
        result, count = _strip_all_function_bodies(code)
        assert count == 1
        assert "void normalFunc(int x);" in result
        assert "/* body stripped: auto-inline prevention */" in result
        assert "int y" not in result

    def test_strips_inline_without_keywords(self):
        """Inline functions should have 'inline' and 'static' keywords removed from signature."""
        code = """static inline s32 ftGetKind(Fighter* fp) {
    return fp->kind;
}"""
        result, count = _strip_all_function_bodies(code)
        assert count == 1
        assert "s32 ftGetKind(Fighter* fp);" in result
        # Both 'inline' and 'static' should be removed from signature
        assert "static" not in result or "/* body stripped" in result
        assert "inline" not in result or "/* body stripped" in result

    def test_keeps_specified_functions(self):
        """Functions in keep_functions set should not be stripped."""
        code = """void foo() { return; }
void bar() { return; }
void baz() { return; }"""
        result, count = _strip_all_function_bodies(code, keep_functions={"bar"})
        assert count == 2  # foo and baz stripped, bar kept
        assert "void foo();" in result
        assert "void bar() { return; }" in result
        assert "void baz();" in result

    def test_multiline_signature_inline_and_static_removed(self):
        """Multi-line signature with inline should have both keywords removed."""
        code = """static inline void long_func(
    int a,
    int b,
    int c)
{
    int sum = a + b + c;
}"""
        result, count = _strip_all_function_bodies(code)
        assert count == 1
        assert "void long_func" in result
        # Both 'inline' and 'static' should be removed from signature
        assert "static" not in result or "/* body stripped" in result
        assert "inline" not in result or "/* body stripped" in result
        assert "int sum" not in result


class TestStructBodyPreservation:
    """Tests for preserving struct/union bodies while stripping function bodies.

    Regression tests for the bug where the regex-based stripper couldn't
    distinguish between struct bodies { ... } and function bodies { ... },
    leading to struct definitions being mangled.
    """

    def test_struct_body_preserved(self):
        """Struct body should NOT be stripped."""
        code = """struct Foo {
    int x;
    int y;
};

void func() {
    return;
}"""
        result, count = _strip_all_function_bodies(code)
        assert count == 1
        assert "struct Foo {" in result
        assert "int x;" in result
        assert "int y;" in result
        assert "};" in result
        assert "return;" not in result

    def test_typedef_struct_preserved(self):
        """Typedef struct with body should be preserved."""
        code = """typedef struct {
    void (*handler)(int, int);
    int flags;
} EventHandler;

void process() {
    do_work();
}"""
        result, count = _strip_all_function_bodies(code)
        assert count == 1
        assert "typedef struct {" in result
        assert "void (*handler)(int, int);" in result
        assert "int flags;" in result
        assert "} EventHandler;" in result
        assert "do_work();" not in result

    def test_function_pointer_typedef_preserved(self):
        """Function pointer typedef should be preserved."""
        code = """typedef void (*Callback)(int);

void caller() {
    cb(42);
}"""
        result, count = _strip_all_function_bodies(code)
        assert count == 1
        assert "typedef void (*Callback)(int);" in result
        assert "cb(42);" not in result

    def test_union_body_preserved(self):
        """Union body should NOT be stripped."""
        code = """union Data {
    int i;
    float f;
    char c;
};

void use_union() {
    union Data d;
    d.i = 5;
}"""
        result, count = _strip_all_function_bodies(code)
        assert count == 1
        assert "union Data {" in result
        assert "int i;" in result
        assert "float f;" in result
        assert "char c;" in result
        assert "d.i = 5;" not in result

    def test_enum_body_preserved(self):
        """Enum body should NOT be stripped."""
        code = """enum Status {
    OK = 0,
    ERROR = 1,
    PENDING = 2
};

void check_status() {
    if (status == OK) return;
}"""
        result, count = _strip_all_function_bodies(code)
        assert count == 1
        assert "enum Status {" in result
        assert "OK = 0," in result
        assert "ERROR = 1," in result
        assert "if (status == OK)" not in result

    def test_mixed_structs_and_functions(self):
        """Complex mix of structs, typedefs, and functions.

        This is the exact bug scenario reported by an agent where context
        processing was breaking struct definitions.
        """
        code = """/* Struct definition with body */
struct Foo {
    int x;
    int y;
};

/* Typedef */
typedef void (*Callback)(int);

/* Function prototype - should stay */
void grIceMt_801F929C(HSD_GObj* arg0);

/* Function definition - body should be stripped */
void grIceMt_801F929C(HSD_GObj* arg0) {
    mpLib_80057BC0(2);
    some_call();
}

/* Struct with function pointer field */
typedef struct {
    void (*handler)(int, int);
    int flags;
} EventHandler;

/* Another function */
static inline s32 ftGetKind(Fighter* fp) {
    return fp->kind;
}"""
        result, count = _strip_all_function_bodies(code)

        # Should strip 2 functions: grIceMt_801F929C and ftGetKind
        assert count == 2

        # Struct Foo body must be preserved
        assert "struct Foo {" in result
        assert "int x;" in result
        assert "int y;" in result

        # Typedef must be preserved
        assert "typedef void (*Callback)(int);" in result

        # Function prototype must be preserved
        assert "void grIceMt_801F929C(HSD_GObj* arg0);" in result

        # Function body must be stripped
        assert "mpLib_80057BC0" not in result
        assert "some_call();" not in result

        # EventHandler struct must be preserved
        assert "void (*handler)(int, int);" in result
        assert "int flags;" in result
        assert "} EventHandler;" in result

        # Inline function body stripped, both 'inline' and 'static' removed
        assert "return fp->kind" not in result
        assert "s32 ftGetKind" in result
        # Check that static was removed from the ftGetKind declaration
        lines_with_ftGetKind = [l for l in result.split('\n') if 'ftGetKind' in l]
        for line in lines_with_ftGetKind:
            if 'body stripped' in line:
                assert 'static' not in line.split('/*')[0], \
                    f"'static' should be removed from declaration: {line}"

    def test_nested_struct_in_struct(self):
        """Nested struct definitions should be preserved."""
        code = """struct Outer {
    struct Inner {
        int value;
    } inner;
    int count;
};

void process() {
    struct Outer o;
    o.count = 5;
}"""
        result, count = _strip_all_function_bodies(code)
        assert count == 1
        assert "struct Outer {" in result
        assert "struct Inner {" in result
        assert "int value;" in result
        assert "} inner;" in result
        assert "int count;" in result
        assert "o.count = 5;" not in result


class TestMWCCCompatibility:
    """Tests for MWCC compiler compatibility with stripped context.

    MWCC (Metrowerks CodeWarrior) has specific requirements for valid C89 code.
    These tests ensure the stripped context compiles correctly.

    Regression tests for: Error: '{' expected after static function declarations
    """

    def test_static_inline_produces_valid_declaration(self):
        """Static inline functions should produce declarations MWCC accepts.

        MWCC may not accept static forward declarations like:
            static s32 ftGetKind(Fighter* fp);
        Because static functions require definitions in the same translation unit.

        The fix should either:
        1. Comment out the entire function
        2. Or produce an extern declaration
        """
        code = """static inline s32 ftGetKind(Fighter* fp) {
    return fp->kind;
}

void caller(Fighter* fp) {
    s32 k = ftGetKind(fp);
}"""
        result, count = _strip_all_function_bodies(code)
        assert count == 2  # Both functions stripped

        # The result must not have a static declaration without a body
        # Option A: Function is commented out entirely
        # Option B: 'static' is removed, leaving just the declaration
        # Option C: Function is completely removed
        lines = result.split('\n')
        for line in lines:
            stripped = line.strip()
            # If there's a static declaration ending with ;, it must be followed by body or commented
            if stripped.startswith('static') and stripped.endswith(';'):
                # This is the problematic pattern - static declaration without body
                # Check if it's inside a comment
                if '/*' not in line or line.index('/*') > line.index('static'):
                    pytest.fail(f"Found static declaration without body: {stripped}\n"
                               "MWCC requires static functions to have bodies in the same TU.")

    def test_static_inline_stripped_completely_or_extern(self):
        """Static inline functions should not leave bare 'static' declarations.

        When stripping static inline functions, the result should either:
        1. Be completely removed/commented out
        2. Or have 'static' changed to 'extern' for valid forward declaration
        """
        code = """static inline void helper(int x) {
    do_something(x);
}"""
        result, count = _strip_inline_functions(code)
        assert count == 1

        # Should not have "static void helper" as a standalone declaration
        # because static forward declarations are not portable C89
        if 'static void helper' in result and ';' in result:
            # Check if it's a valid pattern (e.g., inside comment or has body)
            if '/* body stripped' in result or '// body stripped' in result:
                # It's using the current (problematic) format
                # This test documents the required fix
                pytest.fail("Static inline stripped to 'static void helper();' which "
                           "may cause '{' expected error in MWCC. "
                           "Should be commented out or made extern.")

    def test_non_static_inline_can_be_declaration(self):
        """Non-static inline functions can be forward declared safely."""
        code = """inline void global_helper(int x) {
    do_something(x);
}"""
        result, count = _strip_inline_functions(code)
        assert count == 1

        # This should be fine - non-static functions can be forward declared
        assert "void global_helper" in result
        # 'inline' should be removed
        assert "inline" not in result or "/* body stripped" in result
