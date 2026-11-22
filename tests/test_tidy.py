"""Paper-ready LaTeX from Word / PowerPoint equations (tidy.py), on what Word really writes.

The fixtures in fixtures/word/ were captured from Word 16.0.20527 with tools/capture_word_fixtures.py:
each equation was typed into Word as LaTeX, then copied (MathML) or saved (OMML).
"""
import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from equation_tool import tidy  # noqa: E402
from equation_tool.convert import convert  # noqa: E402
from equation_tool.pandoc_engine import strip_tex  # noqa: E402

FIXTURES = os.path.join(os.path.dirname(__file__), "fixtures", "word")

EXPECTED = {
    "rm_subscript": r"P_{\mathrm{OOD}}^{(t)} = \frac{a}{b}",
    "aligned": "\\begin{aligned}\na & = b + c \\\\\nd & = e\n\\end{aligned}",
    "softmax": r"\operatorname{softmax}\left( z_{i} \right)",
    "binom": r"\binom{n}{k}",
    "bold": r"\boldsymbol{\theta}^{\top}\mathbf{x} + \boldsymbol{w}",
    "cases": "f(x) = \\begin{cases}\n1 & x > 0 \\\\\n0 & \\text{otherwise}\n\\end{cases}",
    "display_sum": r"\mathcal{L} = \frac{1}{n}\sum_{i = 1}^{n}w_{i}\ell\left( x_{i} \right)",
    "differential": r"\int_{0}^{1}f(x)\,\mathrm{d}x",
}


def fixture(name, kind):
    with open(os.path.join(FIXTURES, "%s.%s.xml" % (name, kind)), encoding="utf-8") as f:
        return f.read()


def squash(tex):
    """texmath's grouping of an n-ary operator with its operand ({\\sum ... w_{i}}) is kept in
    MathML input; compare without the braces texmath adds there."""
    tex = tex.replace("{\\sum_{i = 1}^{n}w_{i}}", "\\sum_{i = 1}^{n}w_{i}").replace("{\\int_{0}^{1}f}", "\\int_{0}^{1}f")
    return tex.replace("\\, ", "\\,")  # the OMML reader spaces a thin space, the MathML reader does not


@pytest.mark.parametrize("name", sorted(EXPECTED))
@pytest.mark.parametrize("kind", ["mathml", "omml"])
def test_word_equations_come_back_as_an_author_writes_them(name, kind):
    out = convert(fixture(name, kind), kind, "latex", wrapper="bare").text
    assert squash(out) == EXPECTED[name]


@pytest.mark.parametrize("name", sorted(EXPECTED))
def test_tidy_can_be_switched_off(name):
    raw = convert(fixture(name, "mathml"), "mathml", "latex", wrapper="bare", tidy=False).text
    assert raw  # texmath's own rendering, as before


def test_without_tidy_texmath_misreads_these():
    raw = convert(fixture("softmax", "mathml"), "mathml", "latex", wrapper="bare", tidy=False).text
    assert raw.startswith("{softmax}")
    raw = convert(fixture("binom", "mathml"), "mathml", "latex", wrapper="bare", tidy=False).text
    assert "\\frac" in raw


def test_tidied_latex_round_trips():
    """LaTeX -> MathML (Swap) -> LaTeX keeps names, binomials and bold symbols."""
    for tex in (r"\operatorname{softmax}(z)", r"\binom{n}{k}", r"\boldsymbol{\theta}", r"P_{\mathrm{OOD}}",
                r"\mathrm{d}x"):
        mathml = convert(tex, "latex", "mathml").text
        assert convert(mathml, "mathml", "latex", wrapper="bare").text == tex.replace("(z)", "(z)")


def test_system_of_equations_is_not_cases():
    tex = r"\[\left\{\begin{array}{rl} x+y &= 1 \\ x-y &= 0 \end{array}\right.\]"
    out = convert(convert(tex, "latex", "mathml").text, "mathml", "latex", wrapper="bare").text
    assert "\\begin{cases}" not in out and "\\left\\{" in out


@pytest.mark.parametrize("tex, display, expected", [
    (r"\sum\limits_{i}x_{i}", True, r"\sum_{i}x_{i}"),
    (r"\sum\limits_{i}x_{i}", False, r"\sum\limits_{i}x_{i}"),           # inline: the limits are a choice
    (r"\frac{\sum\limits_{i}x_{i}}{n}", True, r"\frac{\sum\limits_{i}x_{i}}{n}"),  # text style in a fraction
    (r"\frac{1}{n}{\sum\limits_{i}x}", True, r"\frac{1}{n}{\sum_{i}x}"),
    (r"x^{\sum\limits_{i}y}", True, r"x^{\sum\limits_{i}y}"),            # a script
    (r"\begin{matrix} \sum\limits_{i} \end{matrix}", True, r"\begin{matrix} \sum\limits_{i} \end{matrix}"),
    (r"\begin{aligned} a & = \sum\limits_{i} \end{aligned}", True, r"\begin{aligned} a & = \sum_{i} \end{aligned}"),
    (r"\int\limits_{0}^{1}", True, r"\int\limits_{0}^{1}"),              # integrals: \limits is not the default
    (r"x_{i}^{}", True, r"x_{i}"),
    (r"\lbrack y = k\rbrack", True, r"[y = k]"),
    (r"\mathbf{θ} + \mathbf{\Theta} + \mathbf{\theta}", True, r"\boldsymbol{\theta} + \mathbf{\Theta} + \boldsymbol{\theta}"),
    (r"\text{θ}", True, r"\text{θ}"),
    (r"{\mathcal{l}}(x)", True, r"\ell(x)"),
    ("\\begin{matrix}\n1 & {x > 0}\n\\end{matrix}", True, "\\begin{matrix}\n1 & x > 0\n\\end{matrix}"),
    (r"\begin{aligned} {a} & {} \end{aligned}", True, r"\begin{aligned} a & {} \end{aligned}"),  # an empty group stays
    (r"\parallel \mathbf{c} \parallel_{2}", True, r"\| \mathbf{c} \|_{2}"),  # a norm in U+2225
    (r"a \parallel b", True, r"a \parallel b"),                               # a relation
    (r"a \parallel b, c \parallel d", True, r"a \parallel b, c \parallel d"),
    (r"A\backslash B + C \smallsetminus D = \varnothing", True, r"A\setminus B + C \setminus D = \emptyset"),
    (r"\mathbf{2}\mathbf{5}.\mathbf{2} + \mathbf{1}", True, r"\mathbf{25.2} + \mathbf{1}"),
])
def test_tidy_tex(tex, display, expected):
    assert tidy.tidy_tex(tex, display) == expected


def test_trailing_control_space_is_not_cut_to_a_backslash():
    """texmath writes Word's cases construct (OMML) as ... \\right.\\  - a backslash must not be left."""
    assert strip_tex("\\left\\{ x \\right.\\ ") == "\\left\\{ x \\right."
    assert strip_tex("a \\\\ ") == "a \\\\"
    m = 'xmlns:m="http://schemas.openxmlformats.org/officeDocument/2006/math"'
    omml = ('<m:oMath %s><m:r><m:t>f=</m:t></m:r><m:d><m:dPr><m:begChr m:val="{"/><m:endChr m:val=""/></m:dPr>'
            '<m:e><m:eqArr><m:e><m:r><m:t>1,&amp;x&gt;0</m:t></m:r></m:e><m:e><m:r><m:t>0,&amp;x≤0</m:t></m:r></m:e>'
            '</m:eqArr></m:e></m:d></m:oMath>' % m)
    for wrapper, opening, closing in (("dollar", "$", "$"), ("paren", "\\(", "\\)"), ("bracket", "\\[", "\\]")):
        out = convert(omml, "omml", "latex", wrapper=wrapper).text
        assert out.startswith(opening) and out.endswith(closing)
        body = out[len(opening):-len(closing)].rstrip()
        assert (len(body) - len(body.rstrip("\\"))) % 2 == 0  # no lone backslash escaping the delimiter
    raw = convert(omml, "omml", "latex", wrapper="dollar", tidy=False).text
    assert raw.endswith("\\right.$")


def test_placeholder_text_in_the_input_disables_marking():
    mathml = '<math xmlns="http://www.w3.org/1998/Math/MathML"><mi>QQXJAJXQQ</mi><mi>OOD</mi></math>'
    out = convert(mathml, "mathml", "latex", wrapper="bare").text
    assert "QQXJAJXQQ" in out and "\\mathrm" not in out
