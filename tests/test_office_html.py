"""Equations from the HTML that Word copies (office_html.py), and two things found with Mac Word 16.115:
a math paragraph of lines aligned at "=", and the space typed after the last symbol.

The fixtures in fixtures/mac_word/ are what Mac Word 16.115 put on the pasteboard
(tools/mac_office_probe.py, 2026-11-26), cut down to the copied fragment: the picture Word adds for
programs without equations (a local file path and image data) is replaced by a placeholder.
"""
import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from mathshuttle.convert import convert  # noqa: E402
from mathshuttle.office_html import clean_block, omml_from_html  # noqa: E402

FIXTURES = os.path.join(os.path.dirname(__file__), "fixtures", "mac_word")


def fixture(name: str) -> str:
    with open(os.path.join(FIXTURES, name), encoding="utf-8") as f:
        return f.read()


def latex(text: str, src: str = "omml") -> str:
    return convert(text, src, "latex").text


def body(text: str, src: str = "omml", tidy: bool = True) -> str:
    return convert(text, src, "latex", wrapper="bare", tidy=tidy).text


@pytest.mark.parametrize("name, expected", [
    ("inline", r"\[ x^{2} + \alpha \]"),                     # the run of the trailing space is dropped
    ("display", r"\[ \frac{a}{b} \]"),
    ("sentence", r"$\frac{a}{b}$"),                           # text around an inline equation: no MathML
    ("aligned", "\\[\n\\begin{aligned}\na & = b + c \\\\\n & = c + b\n\\end{aligned}\n\\]"),
])
def test_equations_from_mac_word_html(name, expected):
    assert latex(omml_from_html(fixture(name + ".html"))) == expected


def test_html_without_equations():
    assert omml_from_html(None) is None
    assert omml_from_html("") is None
    assert omml_from_html("<html><body><p class=MsoNormal>Text only</p></body></html>") is None
    assert omml_from_html("<!--[if gte msEquation 12]><span>no maths</span><![endif]-->") is None


def test_several_equations_in_one_selection():
    one = "<!--[if gte msEquation 12]><m:oMath><m:r>%s</m:r></m:oMath><![endif]-->"
    html = "<p>Let %s and %s.</p>" % (one % "x", one % "y")
    assert latex(omml_from_html(html)) == "$x$\n\n$y$"


def test_html_text_becomes_xml_text():
    # entities, and a line break from the HTML's wrapping inside a run (a space in the text)
    block = "<m:oMath><m:r>a&lt;b&amp;c&nbsp;</m:r><m:r><m:rPr><m:sty m:val=\"p\"/></m:rPr>max\r\n  x</m:r></m:oMath>"
    omml = clean_block(block)
    assert '<m:t xml:space="preserve">a&lt;b&amp;c\xa0</m:t>' in omml
    assert '<m:rPr><m:sty m:val="p"/></m:rPr><m:t xml:space="preserve">max x</m:t>' in omml
    # HTML tags go, OMML tags with their attributes stay, runs that held only <br> or wrapping go
    block = "<m:oMath><i style='a:b'><span\r\n lang=EN-CA style='x:\"y>\"'><m:r>z</m:r><m:r><br>\r\n </m:r></span></i></m:oMath>"
    assert clean_block(block) == '<m:oMath><m:r><m:t xml:space="preserve">z</m:t></m:r></m:oMath>'


def test_aligned_lines_in_omml():
    line = "<m:oMath><m:r><m:t>%s</m:t></m:r><m:r><m:rPr><m:aln/></m:rPr><m:t>=</m:t></m:r><m:r><m:t>%s</m:t></m:r></m:oMath>"
    para = "<m:oMathPara>%s%s</m:oMathPara>" % (line % ("a", "b"), line % ("c", "d"))
    assert body(para) == "\\begin{aligned}\na & = b \\\\\nc & = d\n\\end{aligned}"
    # a line without a mark, and a mark on a run with other properties: kept, without the mark
    styled = "<m:oMath><m:r><m:rPr><m:aln/><m:sty m:val=\"p\"/></m:rPr><m:t>=e</m:t></m:r></m:oMath>"
    para = "<m:oMathPara>%s<m:oMath><m:r><m:t>f</m:t></m:r></m:oMath>%s</m:oMathPara>" % (line % ("a", "b"), styled)
    tex = body(para)
    assert tex.startswith("\\begin{aligned}\na & = b \\\\\nf") and tex.count("&") == 2 and "aln" not in tex
    # without marks, the lines are still separate equations
    plain = "<m:oMathPara><m:oMath><m:r><m:t>a=1</m:t></m:r></m:oMath><m:oMath><m:r><m:t>b=2</m:t></m:r></m:oMath></m:oMathPara>"
    assert latex(plain) == "\\[ a = 1 \\]\n\n\\[ b = 2 \\]"
    # a mark inside a fraction is not a line's alignment point
    nested = ("<m:oMathPara><m:oMath><m:f><m:num><m:r><m:rPr><m:aln/></m:rPr><m:t>x</m:t></m:r></m:num><m:den>"
              "<m:r><m:t>y</m:t></m:r></m:den></m:f></m:oMath><m:oMath><m:r><m:t>z</m:t></m:r></m:oMath></m:oMathPara>")
    assert "&" not in latex(nested)


@pytest.mark.parametrize("name, expected", [
    ("inline", r"\[ x^{2} + \alpha \]"),   # Word: <mo>&#xA0;</mo> at the end, the space typed after \alpha
    ("display", r"\[ \frac{a}{b} \]"),
])
def test_space_typed_at_the_end_in_word(name, expected):
    assert latex(fixture(name + ".mathml.xml"), "mathml") == expected


@pytest.mark.parametrize("mathml, expected", [
    ("<mo>&#xA0;</mo><mi>a</mi><mo>=</mo><mi>b</mi><mo>&#xA0;</mo><mo>&#xA0;</mo>", "a = b"),
    ("<mi>a</mi><mo>&#xA0;</mo><mo>=</mo><mi>b</mi>", "a~ = b"),          # inside: left as it is
    ("<mi>a</mi><mtext>&#xA0;and&#xA0;</mtext><mi>b</mi>", "a\\text{~and~}b"),
    ("<mo>&#xA0;</mo>", "~"),                                               # nothing else: kept
])
def test_edge_spaces(mathml, expected):
    math = '<math xmlns="http://www.w3.org/1998/Math/MathML">%s</math>' % mathml
    assert body(math, "mathml") == expected
    assert body(math, "mathml", tidy=False).startswith(("~", "a"))  # texmath's own TeX keeps them
