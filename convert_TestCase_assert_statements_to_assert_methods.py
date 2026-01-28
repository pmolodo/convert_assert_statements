#!uv run
# /// script
# dependencies = ["libcst==1.8.6"]
# requires-python = "==3.11.*"
# ///

"""Convert bare assert statements in TestCase methods to self.assert* using LibCST.

Currently:
- Finds test*.py files
- converts `assert X == Y` statements to `self.assertEqual(X, Y)`

Usage:
    uv run convert_TestCase_assert_statements_to_assert_methods.py
"""

import glob
import os

import libcst as cst


def get_assert_statement(line: cst.SimpleStatementLine) -> cst.Assert | None:
    if len(line.body) != 1:
        return None
    statement = line.body[0]
    if isinstance(statement, cst.Assert):
        return statement


class AssertEqualTransformer(cst.CSTTransformer):
    """Transform `assert X == Y` to `self.assertEqual(X, Y)` inside TestCase methods."""

    def __init__(self):
        super().__init__()
        self.in_test_class = False
        self.in_method = False
        self.num_changes = 0

    def visit_ClassDef(self, node: cst.ClassDef) -> bool:
        # Check if this class inherits from TestCase
        for base in node.bases:
            base_str = self._node_to_string(base.value)
            if "TestCase" in base_str:
                self.in_test_class = True
                return True
        return True

    def leave_ClassDef(self, _original: cst.ClassDef, updated: cst.ClassDef) -> cst.ClassDef:
        self.in_test_class = False
        return updated

    def visit_FunctionDef(self, _node: cst.FunctionDef) -> bool:
        if self.in_test_class:
            # only convert inside normal methods, not classmethods or staticmethods
            params = _node.params.params
            if params and params[0].name.value == "self":
                self.in_method = True
        return True

    def leave_FunctionDef(self, _original: cst.FunctionDef, updated: cst.FunctionDef) -> cst.FunctionDef:
        self.in_method = False
        return updated

    def _strip_line_continuation(
        self, ws: cst.BaseParenthesizableWhitespace
    ) -> cst.BaseParenthesizableWhitespace:
        """Remove line continuation character from SimpleWhitespace if present.
        """
        if isinstance(ws, cst.SimpleWhitespace):
            value = ws.value.replace("\r\n", "\n")
            # Check for backslash-newline sequences embedded in the string
            if "\\\n" in value:
                # SimpleWhitespace can't contain newlines, so convert to
                # ParenthesizedWhitespace. The last line is the indentation.
                before_newline, after_newline = value.rsplit("\\\n", 1)
                # strip trailing whitespace, that was before continuation backslash
                before_newline = before_newline.rstrip()

                return cst.ParenthesizedWhitespace(
                    first_line=cst.TrailingWhitespace(
                        whitespace=cst.SimpleWhitespace(before_newline),
                        newline=cst.Newline(),
                    ),
                    indent=False,
                    last_line=cst.SimpleWhitespace(after_newline),
                )
        return ws

    def _node_to_string(self, node: cst.BaseExpression) -> str:
        """Convert a CST node to its string representation."""
        return cst.parse_module("").code_for_node(node)

    def leave_SimpleStatementLine(
        self, _original: cst.SimpleStatementLine, updated: cst.SimpleStatementLine
    ) -> cst.SimpleStatementLine:
        if not (self.in_test_class and self.in_method):
            return updated

        # Check if we have an assert statement
        assert_statement = get_assert_statement(updated)
        if not assert_statement:
            return updated

        # Check if the test is a comparison with ==
        test = assert_statement.test
        if not isinstance(test, cst.Comparison):
            return updated

        # Only handle simple X == Y comparisons (single comparison operator)
        if len(test.comparisons) != 1:
            return updated

        comp = test.comparisons[0]
        if not isinstance(comp.operator, cst.Equal):
            return updated

        # Build self.assertEqual(left, right) preserving original whitespace
        left = test.left
        right = comp.comparator
        message = assert_statement.msg

        # Try to preserve original whitespace from assert statement, except we
        # strip line continuation characters since they're not needed inside
        # parentheses.

        # first arg is left, with new whitespace after comma the same as old
        # whitespace after ==
        first_arg = cst.Arg(value=left,
            comma=cst.Comma(whitespace_after=self._strip_line_continuation(comp.operator.whitespace_after)))

        # second arg is right; if original assert had a message, use the same
        # whitepace after comma
        if message and isinstance(assert_statement.comma, cst.Comma):
            # Get whitespace after the comma (before message) from original assert
            comma = assert_statement.comma.with_changes(
                whitespace_after=self._strip_line_continuation(assert_statement.comma.whitespace_after))
            second_arg = cst.Arg(value=right, comma=comma)
        else:
            second_arg = cst.Arg(value=right)

        args = [first_arg, second_arg]
        if message:
            args.append(cst.Arg(value=message))

        new_call = cst.Call(
            func=cst.Attribute(
                value=cst.Name("self"),
                attr=cst.Name("assertEqual"),
            ),
            args=args,
        )

        new_statement = cst.Expr(value=new_call)
        self.num_changes += 1

        return updated.with_changes(body=[new_statement])


def transform_file(filepath: str) -> int:
    """Transform a single file. Returns number of changes made."""
    with open(filepath, newline="", encoding="utf-8") as f:
        source = f.read()

    try:
        tree = cst.parse_module(source)
    except cst.ParserSyntaxError:
        return 0

    transformer = AssertEqualTransformer()
    new_tree = tree.visit(transformer)

    if transformer.num_changes > 0:
        with open(filepath, "w", encoding="utf-8", newline="") as f:
            f.write(new_tree.code)

    return transformer.num_changes


def transform_folder(directory_root: str):
    pattern = os.path.join(directory_root, "**", "test*.py")
    test_files = sorted(glob.glob(pattern, recursive=True))
    print(f"Found {len(test_files)} test files in {directory_root}")

    total_changes = 0
    num_changed_files = 0

    for filepath in test_files:
        num_changes = transform_file(filepath)
        if num_changes > 0:
            relpath = os.path.relpath(filepath, directory_root)
            num_changed_files += 1
            total_changes += num_changes
            print(f"  Changed {num_changes} assert(s) in {relpath}")

    print(f"\nTotal: {total_changes} assertions converted in {num_changed_files} files")


if __name__ == "__main__":
    transform_folder(os.getcwd())
