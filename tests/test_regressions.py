"""Regression tests for the findings of the Codex review of d63b58d
(reviews/2026-11-22/d63b58d-codex-review.md; numbers below are the findings')."""
import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from mathshuttle import compat  # noqa: E402
from mathshuttle.convert import convert  # noqa: E402
from mathshuttle.pandoc_engine import ConversionError, strip_tex  # noqa: E402

M = '<math xmlns="http://www.w3.org/1998/Math/MathML" display="block">%s</math>'


def to_latex(mathml, **kw):
    return convert(M % mathml, "mathml", "latex", wrapper=kw.pop("wrapper", "bare"), **kw).text


def mathml(tex, **kw):
    return convert(tex, "latex", "mathml", **kw).text


def macros(text):
    return compat.Macros(text=text)


# 1: a placeholder must keep its braces when it is an argument
def test_sqrt_keeps_its_argument():
    assert to_latex("<msqrt><mi>FOO</mi></msqrt>") == r"\sqrt{\mathrm{FOO}}"


# 2: Greek variants must not be folded together (NFKC folds ϕ into φ)
def test_greek_variants_stay_distinct():
    out = to_latex('<mi mathvariant="bold-italic">φ</mi><mo>≠</mo><mi mathvariant="bold-italic">ϕ</mi>')
    assert out == r"\boldsymbol{\varphi} \neq \boldsymbol{\phi}"
    # U+1D753 is BOLD ITALIC PHI SYMBOL (ϕ, \phi), U+1D74B BOLD ITALIC SMALL PHI (φ, \varphi)
    assert to_latex("<mi>𝝓</mi><mo>+</mo><mi>𝝋</mi>") == r"\boldsymbol{\phi} + \boldsymbol{\varphi}"


# 3: MathML -> MathML / OMML keeps a set minus
def test_set_minus_survives_mathml_to_mathml_and_omml():
    assert "∖" in convert(M % "<mi>A</mi><mo>∖</mo><mi>B</mi>", "mathml", "mathml").text
    assert "∖" in convert(M % "<mi>A</mi><mo>∖</mo><mi>B</mi>", "mathml", "omml").text


# 4: a chain of parallel relations is not a norm
def test_parallel_relations_are_not_a_norm():
    out = to_latex('<mi>a</mi><mo form="infix">∥</mo><mi>b</mi><mo form="infix">∥</mo><mi>c</mi>')
    assert out == r"a \parallel b \parallel c"
    out = to_latex("<mi>a</mi><mo>∥</mo><mi>b</mi><mo>∥</mo><mi>c</mi>")
    assert "\\|" not in out


def test_norm_from_texmath_mathml_is_a_norm():
    assert to_latex(mathml(r"\|x\|_2").split(">", 1)[1].rsplit("<", 1)[0]).replace(" ", "") == r"\|x\|_{2}"


# 5: cases recovery must not take the first cell for a column specification
def test_cases_recovery_keeps_the_first_cell():
    tex = "\\left\\{\\begin{aligned}{ll}x & y \\\\ a & b \\end{aligned}\\right."
    back = convert(mathml(tex), "mathml", "latex", wrapper="bare").text
    assert "{ll}" in back.replace(" ", "") or "ll" in back


# 6: [ after a row break is not an option
def test_bracket_after_row_break_is_kept_safe():
    back = convert(mathml("\\begin{matrix}a\\\\\\lbrack 2\\rbrack\\end{matrix}", annotate=True),
                   "mathml", "latex", wrapper="bare").text
    assert "\\\\[" not in back.replace(" ", "").replace("\n", "")
    again = mathml(back)
    assert again.count("<mn>2</mn>") == 1


# 7: an interval at the start of dcases is not an option
def test_dcases_keeps_a_leading_interval():
    out = mathml("\\begin{dcases}[a,b] & x>0 \\\\ 0 & x<0\\end{dcases}")
    assert "<mi>a</mi>" in out and "<mi>b</mi>" in out


# 8: starred cases have a text column
def test_starred_cases_keep_their_text_column():
    out = mathml("\\begin{dcases*}x & if $x>0$ \\\\ 0 & otherwise\\end{dcases*}")
    assert "<mtext" in out and "otherwise" in out and "<mn>0</mn>" in out
    out = mathml("\\begin{cases*}x & if even \\\\ y & otherwise\\end{cases*}")
    assert "if even" in out


# 9: a removed phantom leaves an empty atom
def test_dropped_phantom_keeps_atoms_apart():
    assert "sinh" not in mathml(r"\sin\vphantom{x}h")
    out = mathml(r"X\vphantom{Y}^T")
    assert "<msup>\n    <mi>X</mi>" not in out


# 10: arguments after comments; only the options a command takes
def test_argument_scanning():
    assert "<mi>x</mi>" not in mathml("\\vphantom% ignored\n{x}+y")
    out = mathml(r"\smash[b][x]+y")
    assert "<mi>x</mi>" in out


# 11: \let copies the definition it sees
def test_let_takes_a_snapshot():
    out = convert(r"\copyfoo+\foo", "latex", "mathml", macros=macros(r"\def\foo{x}\let\copyfoo=\foo\def\foo{y}")).text
    assert out.index("<mi>x</mi>") < out.index("<mi>y</mi>")
    out = mathml(r"\def\join#1#2{#1+#2}\let\alias=\join\alias{a}{b}")
    assert out.index("<mi>a</mi>") < out.index("<mo>+</mo>") < out.index("<mi>b</mi>")


# 12: paired delimiters: starred and sized forms, braced delimiters
def test_paired_delimiters():
    m = macros(r"\DeclarePairedDelimiter{\abs}{\lvert}{\rvert}\DeclarePairedDelimiter{\set}{\{}{\}}")
    out = convert(r"\abs*{x}", "latex", "mathml", macros=m).text
    assert out.index("|") < out.index("<mi>x</mi>") < out.rindex("|") and "*" not in out
    out = convert(r"\abs[\big]{y}", "latex", "mathml", macros=m).text
    assert out.index("|") < out.index("<mi>y</mi>") < out.rindex("|") and "[" not in out
    out = convert(r"\set{x}", "latex", "mathml", macros=m).text
    assert out.index("{") < out.index("<mi>x</mi>") < out.rindex("}")


# 13: a macro file may redefine a command the tool rewrites
def test_macro_file_overrides_builtin_rewrites():
    out = convert(r"A\setminus B", "latex", "mathml", macros=macros(r"\renewcommand{\setminus}{+}")).text
    assert "<mo>+</mo>" in out and "∖" not in out


# 14: text that looks like a placeholder, split over several identifiers
def test_placeholder_lookalike_in_another_equation():
    other = "<math>" + "".join("<mi>%s</mi>" % c for c in "QQXJAJXQQ") + "<mo>+</mo><mn>1</mn></math>"
    out = convert("<math><mi>OOD</mi></math>" + other, "mathml", "latex", wrapper="bare").text
    assert "\\mathrm{OOD} + 1" not in out


# 15: a bar-less fraction without fences is not a binomial
def test_unfenced_zero_rule_is_not_a_binomial():
    assert "binom" not in to_latex('<mfrac linethickness="0"><mi>a</mi><mi>b</mi></mfrac>')
    assert to_latex('<mrow><mo>(</mo><mfrac linethickness="0"><mi>n</mi><mi>k</mi></mfrac><mo>)</mo></mrow>') == r"\binom{n}{k}"


# 16: \middle is passed through
def test_middle_is_kept():
    out = mathml(r"\left(a\middle.b\right)")
    assert "<mi>.</mi>" not in out
    assert mathml(r"\left(a\middle|b\right)").count("|") == 1


# 17: a renamed command before a macro parameter
def test_norm_in_a_macro_body():
    out = mathml(r"\newcommand{\foo}[1]{\|#1\|}\foo{x}")
    assert out.count("‖") == 2


# 18: font switches and \mathbf in macro bodies act where the macro is used
def test_font_switch_macros():
    out = mathml(r"\newcommand{\fontcmd}{\bf}\fontcmd x+y")
    assert "𝐱" in out and "𝐲" in out
    out = convert(r"{\foo Y}", "latex", "mathml", macros=macros(r"\def\foo{\rm X}")).text
    assert out.count('mathvariant="normal"') == 2
    assert "𝐱" in mathml(r"\newcommand{\bold}[1]{\mathbf{#1}}\bold{x}")


# 19: annotated MathML the tool wrote converts again
def test_annotated_mathml_converts_again():
    annotated = mathml(r"P_{\rm OOD}", annotate=True)
    assert convert(annotated, "mathml", "omml").text
    score = convert(r"\score", "latex", "mathml", annotate=True, macros=macros(r"\newcommand{\score}{42}")).text
    assert "42" in convert(score, "mathml", "omml").text


# 20: \limits after a radical in a fraction; inline output keeps \limits
def test_limits_tracking():
    back = convert(mathml(r"\frac{\sqrt{a}}{\sum\limits_i^n}"), "mathml", "latex", wrapper="bare").text
    assert "\\limits" in back
    out = convert(M % "<munderover><mo>∑</mo><mi>i</mi><mi>n</mi></munderover>", "mathml", "latex", wrapper="dollar").text
    assert "\\limits" in out


# 21: math inside text; a comment in a switch's scope
def test_switch_in_math_in_text():
    out = mathml(r"\text{before $\rm x$ after}")
    assert 'mathvariant="normal">x' in out.replace("\n", "").replace(" ", "") or "<mi mathvariant=\"normal\">x</mi>" in out
    assert mathml("{\\rm x%comment\n}")


# 22: definition syntax
@pytest.mark.parametrize("defs, tex, expected", [
    (r"\providecommand{\score}{x}", r"\score", "<mi>x</mi>"),
    (r"\def\score #1{#1}", r"\score{y}", "<mi>y</mi>"),
    (r"\newcommand{\score}[1][{[x]}]{#1}", r"\score", "<mi>x</mi>"),
])
def test_definition_syntax(defs, tex, expected):
    assert expected in convert(tex, "latex", "mathml", macros=macros(defs)).text


def test_unterminated_definition_is_reported():
    m = macros(r"\def\score{xy")
    assert "\\score" in m.skipped


# 24: diagnostics when every macro is rejected, and from macro bodies
def test_macro_diagnostics():
    m = macros(r"\def\weird#1{x")
    assert m is not None and m.skipped
    out = convert(r"\bigone", "latex", "mathml", macros=macros(r"\newcommand{\bigone}{\textcolor{red}{x}}"))
    assert any("colour" in n for n in out.notes)


# 25: a lone backslash typed by the user is still an error
def test_dangling_backslash_is_an_error():
    assert strip_tex("x\\") == "x\\"
    with pytest.raises(ConversionError):
        convert("x\\", "latex", "mathml")
    assert strip_tex("\\left\\{ x \\right.\\ ") == "\\left\\{ x \\right."
