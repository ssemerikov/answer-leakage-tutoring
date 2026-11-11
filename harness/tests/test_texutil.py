"""One comment stripper for every tool that reads the manuscript.

`verify_numbers` searched the RAW tex for each literal, so a number that
survived only in a `%%` comment "verified" -- the exact blind spot a presence
check exists to close. `promise_audit` had its own stripper, `abstract_words`
a third. Three strippers are three opinions about what the manuscript says.
"""
import texutil as T


def test_full_line_comments_are_removed():
    s = T.strip_comments("keep\n% gone\n   %% also gone\nkeep too\n")
    assert "gone" not in s
    assert "keep" in s and "keep too" in s


def test_trailing_comment_is_removed_and_the_text_before_it_kept():
    s = T.strip_comments("the value is 0.605 % was 0.611\n")
    assert "0.605" in s
    assert "0.611" not in s


def test_an_escaped_percent_is_text():
    s = T.strip_comments("a rate of 98\\% and we release the data\n")
    assert s.strip() == "a rate of 98\\% and we release the data"


def test_a_line_break_followed_by_percent_starts_a_comment():
    # `\\%` is a forced line break and THEN a real comment: the backslash before
    # the % is itself escaped. A `(?<!\\)` lookbehind reads it as `\%`.
    s = T.strip_comments("row one \\\\% hidden 0.611\n")
    assert "0.611" not in s
    assert s.strip() == "row one \\\\"


def test_three_backslashes_then_percent_is_a_break_and_an_escaped_percent():
    s = T.strip_comments("a \\\\\\% b\n")
    assert s.strip() == "a \\\\\\% b"


def test_text_order_is_kept():
    s = T.strip_comments("one\n% x\ntwo % y\nthree\n")
    assert [w for w in s.split()] == ["one", "two", "three"]


def test_a_number_only_in_a_comment_is_absent_after_stripping():
    """THE bug being fixed: a literal that survives only in a comment."""
    tex = "Mean leakage is 0.605.\n%% old: 0.611\n"
    assert "0.611" in tex
    assert "0.611" not in T.strip_comments(tex)


def test_no_comment_is_the_identity_on_content():
    tex = "plain line\nanother line"
    assert T.strip_comments(tex) == tex


def test_strip_comment_line_tells_a_comment_line_from_a_blank_one():
    # A full-line comment is NO line (None); a blank line is an empty one.
    # A paragraph splitter needs the difference: a comment line inside a
    # paragraph must not end it, a blank line must.
    assert T.strip_comment_line("   % gone") is None
    assert T.strip_comment_line("") == ""
    assert T.strip_comment_line("text 98\\% % note") == "text 98\\% "


def test_strip_comments_is_built_on_the_line_rule():
    tex = "a % x\n% y\n\nb \\\\% z\n"
    lines = [T.strip_comment_line(l) for l in tex.splitlines()]
    assert T.strip_comments(tex) == "\n".join(l for l in lines if l is not None)


# ------------------------------------------------- P0.6 (R2-a) ------------
def test_split_comment_returns_code_and_comment():
    assert T.split_comment("Section~\\auxref{x}. % was Section~4.5") == \
        ("Section~\\auxref{x}. ", "% was Section~4.5")
    assert T.split_comment("98\\% of replies") == ("98\\% of replies", "")
    assert T.split_comment("  % a note") == ("  ", "% a note")
    assert T.split_comment("a \\\\% comment") == ("a \\\\", "% comment")


def test_strip_comment_line_is_built_on_split_comment():
    for line in ["text % c", "  % full", "", "plain", "98\\% x % y"]:
        code, comment = T.split_comment(line)
        want = None if (not code.strip() and comment) else code
        assert T.strip_comment_line(line) == want, line


# ------------------------------------------------- P0.6 fix round 1 (M8) ---
def test_group_end_honours_escaped_braces_and_brackets():
    assert T.group_end("{a\\}b}c", 0) == 6
    assert T.group_end("{a\\{b}c", 0) == 6
    assert T.group_end("[a\\]b]c", 0) == 6
    assert T.group_end("[a={x]y}]c", 0) == 9


def test_ref_commands_cover_the_cleveref_and_varioref_family():
    for name in ("ref", "pageref", "eqref", "autoref", "cref", "Cref", "nameref",
                 "vref", "cpageref", "subref", "autopageref", "labelcref"):
        assert name in T.REF_COMMANDS


def test_one_tex_space_class_for_cross_references_and_cite_locators():
    r"""P0.6 fix round 2 (N6): the TeX space separator -- `~`, a space, `\ `,
    `\,`, `\;`, `\:`, `\!` -- is ONE class, read by make_variants (literal
    cross-references on the title page) and by verify_numbers (page locators
    in \cite notes). Two copies drift."""
    import re

    import make_variants as MV
    import verify_numbers as V
    assert MV._LITERAL_XREF_RE.pattern.count(T.TEX_SPACE) == 2
    assert V._LOCATOR_RE.pattern.count(T.TEX_SPACE) == 1
    for sep in ["~", " ", "\n", "\\ ", "\\,", "\\;", "\\:", "\\!"]:
        assert re.fullmatch(T.TEX_SPACE, sep), sep
    for not_sep in ["\\quad", "-", "\\!!"]:
        assert not re.fullmatch(T.TEX_SPACE, not_sep), not_sep
