"""Regression tests for the 25 findings of a Codex review of the commit that added compat.py and
tidy.py (the numbers below are the findings'); each input made the code of that commit give wrong
maths or invalid LaTeX without an error."""
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


# ---- the re-review of the fixes (R1...) and the two findings that were only partly fixed ----------

def test_r14_placeholder_lookalike_does_not_disable_other_equations():
    other = "<math>" + "".join("<mi>%s</mi>" % c for c in "QQXJAJXQQ") + "<mo>+</mo><mn>1</mn></math>"
    out = convert("<math><mi>OOD</mi></math>" + other, "mathml", "latex", wrapper="bare").text
    assert out.startswith("\\mathrm{OOD}") and "QQXJAJXQQ + 1" in out


def test_r15_unfenced_zero_rule_is_a_stack():
    out = to_latex('<mfrac linethickness="0"><mi>a</mi><mi>b</mi></mfrac>')
    assert "frac" not in out and "binom" not in out
    back = mathml(out)
    assert "<mfrac" not in back and "<mtable" in back


def test_r_grouped_definition_is_refused():
    with pytest.raises(ConversionError, match="inside a group"):
        convert(r"\def\foo{x}\foo+{\def\foo{y}\foo}+\foo", "latex", "mathml")


def test_r_paired_delimiter_is_one_group():
    m = macros(r"\DeclarePairedDelimiter{\abs}{\lvert}{\rvert}")
    out = convert(r"\abs{x}^2", "latex", "mathml", macros=m).text
    sup = out[out.index("<msup>"):out.index("</msup>")]
    assert sup.count("|") == 2  # the whole |x| is the base
    out = convert(r"\abs{\rm x}+y", "latex", "mathml", macros=m).text
    assert "<mi>y</mi>" in out  # \rm stays inside the argument
    with pytest.raises(ConversionError, match="needs an argument"):
        convert(r"\abs", "latex", "mathml", macros=m)


def test_r_paired_delimiter_made_of_macros():
    m = macros(r"\def\ld{\langle}\DeclarePairedDelimiter{\abs}{\ld}{\rangle}")
    out = convert(r"\abs{x}", "latex", "mathml", macros=m).text
    assert "⟨" in out and "⟩" in out


def test_r_fence_attribute_makes_a_norm():
    out = to_latex('<mi>a</mi><mo fence="true">∥</mo><mi>x</mi><mo fence="true">∥</mo>')
    assert "\\parallel" not in out and "\\|" in out


def test_r_backslash_operator_kept_in_mathml_and_omml():
    for dst in ("mathml", "omml"):
        assert "\\" in convert(M % "<mi>A</mi><mo>\\</mo><mi>B</mi>", "mathml", dst).text.replace("\\\\", "")
    assert "∖" in convert(M % "<mi>A</mi><mo>∖</mo><mi>B</mi>", "mathml", "omml").text


def test_r_content_mathml_falls_back_to_its_annotation():
    m = ('<math xmlns="http://www.w3.org/1998/Math/MathML"><semantics><apply><plus/><ci>x</ci><ci>y</ci></apply>'
         '<annotation encoding="application/x-tex">x+y</annotation></semantics></math>')
    assert convert(m, "mathml", "latex", wrapper="bare").text == "x+y"


def test_r_starred_cases_text_column_tokens():
    out = mathml("\\begin{cases*}a&\\textbf{if $x$}\\end{cases*}")
    assert "𝐢𝐟" in out and "<mi>x</mi>" in out  # bold text, maths inside it
    out = mathml("\\begin{cases*}a&$\\alpha$ $b$\\end{cases*}")
    assert "α" in out and "<mi>b</mi>" in out
    out = mathml("\\begin{cases*}a&if % z\ntrue\\end{cases*}")
    assert "z" not in out.replace("mathvariant", "") and "true" in out


def test_r_starred_operator_keeps_its_limits():
    out = convert(r"\foo_i", "latex", "mathml", macros=macros(r"\DeclareMathOperator*{\foo}{foo}")).text
    assert "<munder>" in out


def test_r_bare_inline_output_keeps_limits():
    m = '<math xmlns="http://www.w3.org/1998/Math/MathML" display="inline"><munderover><mo>∑</mo><mi>i</mi><mi>n</mi></munderover></math>'
    assert "\\limits" in convert(m, "mathml", "latex", wrapper="bare").text


def test_r_omml_bold_digits_stay_bold():
    omml = ('<m:oMath xmlns:m="http://schemas.openxmlformats.org/officeDocument/2006/math"><m:r><m:rPr>'
            '<m:sty m:val="b"/></m:rPr><m:t>A1</m:t></m:r></m:oMath>')
    assert convert(omml, "omml", "latex", wrapper="bare").text == r"\mathbf{A1}"


def test_r_switch_ends_at_closing_paren_math():
    out = mathml(r"\text{before \(\rm x\) after}")
    assert "after" in out and 'mathvariant="normal">x<' in out.replace("\n", "").replace(" ", "")


def test_r_comment_before_the_macro_name():
    assert "<mi>x</mi>" in convert(r"\foo", "latex", "mathml", macros=macros("\\newcommand% note\n{\\foo}{x}")).text
