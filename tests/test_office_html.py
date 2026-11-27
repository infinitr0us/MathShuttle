"""Equations from the HTML that Word copies (office_html.py), and two things found with Mac Word 16.115:
a math paragraph of lines aligned at "=", and the space typed after the last symbol.

The fixtures in fixtures/mac_word/ are what Mac Word 16.115 put on the pasteboard
(tools/mac_office_probe.py, 2026-11-26), cut down to the copied fragment: the picture Word adds for
programs without equations (a local file path and image data) is replaced by a placeholder.
"""
import os
import re
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from mathshuttle.convert import convert  # noqa: E402
from mathshuttle.office_html import cf_html_fragment, clean_block, omml_from_html  # noqa: E402

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
    ("powerpoint", r"\[ x^{2} + \alpha \]"),                 # PowerPoint: &#119909; entities, other tags
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
    ("powerpoint", r"\[ x^{2} + \alpha \]"),
])
def test_space_typed_at_the_end_in_word(name, expected):
    assert latex(fixture(name + ".mathml.xml"), "mathml") == expected


@pytest.mark.parametrize("mathml, expected, raw", [
    ("<mo>&#xA0;</mo><mi>a</mi><mo>=</mo><mi>b</mi><mo>&#xA0;</mo><mo>&#xA0;</mo>", "a = b", "~a = b~~"),
    ("<mi>a</mi><mo>&#xA0;</mo><mo>=</mo><mi>b</mi>", "a~ = b", "a~ = b"),  # inside: left as it is
    ("<mi>a</mi><mtext>&#xA0;and&#xA0;</mtext><mi>b</mi>", "a\\text{~and~}b", "a\\text{~and~}b"),
    ("<mo>&#xA0;</mo>", "~", "~"),                                            # nothing else: kept
    # a medium space next to the typed one: both go, and no "\ " is left to escape the closing $
    ('<mi>a</mi><mspace width="mediummathspace"/><mo>&#xA0;</mo>', "a", "a\\ ~"),
])
def test_edge_spaces(mathml, expected, raw):
    math = '<math xmlns="http://www.w3.org/1998/Math/MathML">%s</math>' % mathml
    assert body(math, "mathml") == expected
    assert body(math, "mathml", tidy=False) == raw  # texmath's own TeX
    assert convert(math, "mathml", "latex").text == "$%s$" % expected


def test_edge_spaces_at_row_starts_and_ends():
    from mathshuttle.tidy import _edge_spaces
    # rows of an environment with an argument, and an escaped accent after a row break
    assert _edge_spaces("\\begin{array}{rl}~a & b~\\\\~c & d~\\end{array}") == \
        "\\begin{array}{rl}a & b\\\\c & d\\end{array}"
    assert _edge_spaces("a\\\\\\~{n}") == "a\\\\\\~{n}"
    assert _edge_spaces("\\begin{aligned}\na & = b~ \\\\\n~& = c~\n\\end{aligned}") == \
        "\\begin{aligned}\na & = b \\\\\n& = c\n\\end{aligned}"
    assert _edge_spaces("a\\,") == "a\\,"          # a thin space alone is the author's
    assert _edge_spaces("a \\\\ ~[x]") == "a \\\\ ~[x]"  # \\[x] would read [x] as the row's spacing
    # through the whole conversion: a row that starts with a bracket once its space is gone keeps \lbrack
    math = ('<math xmlns="http://www.w3.org/1998/Math/MathML"><mtable columnalign="left right"><mtr><mtd><mi>a</mi>'
            '</mtd><mtd><mi>b</mi></mtd></mtr><mtr><mtd><mo>&#xA0;</mo><mo>[</mo><mi>x</mi><mo>]</mo></mtd><mtd>'
            '<mi>c</mi></mtd></mtr></mtable></math>')
    tex = body(math, "mathml")
    assert "\\\\\n\\lbrack x]" in tex and not re.search(r"\\\\\s*\[", tex)


def block(inner: str) -> str:
    return "<!--[if gte msEquation 12]>%s<![endif]-->" % inner


@pytest.mark.parametrize("inner, expected", [
    # each run on its own: a run's properties do not swallow the next run
    ("<m:oMath><m:r><m:rPr></m:rPr><m:t>a</m:t></m:r><m:r>+</m:r><m:r><m:rPr></m:rPr>b</m:r></m:oMath>", "a + b"),
    ("<m:oMath><m:r>x</m:r><m:r><m:rPr/>+b</m:r></m:oMath>", "x + b"),
    ("<m:oMath><m:r>\r\n<m:rPr><m:sty m:val=\"p\"/></m:rPr>max</m:r></m:oMath>", "\\max"),
    ("<m:oMath><m:r>x</m:r><m:r class=x>+</m:r><m:r>y</m:r></m:oMath>", "x + y"),
    # a space typed inside an equation is kept: 1 2 is not 12
    ("<m:oMath><m:r>1</m:r><m:r> </m:r><m:r>2</m:r></m:oMath>", "1\\ 2"),
    # HTML attribute syntax: unquoted values, entities
    ("<m:oMath><m:r><m:rPr><m:sty m:val=p /></m:rPr>sin</m:r><m:r>x</m:r></m:oMath>", "\\sin x"),
    ("<m:oMath><m:d><m:dPr><m:begChr m:val=\"&lang;\"/><m:endChr m:val='&rang;'/></m:dPr><m:e><m:r>x</m:r>"
     "</m:e></m:d></m:oMath>", "\\left\\langle x \\right\\rangle"),
])
def test_runs_and_attributes_from_html(inner, expected):
    assert body(omml_from_html(block(inner))) == expected


def test_only_the_copied_fragment():
    other, copied = block("<m:oMath><m:r>q</m:r></m:oMath>"), block("<m:oMath><m:r>x</m:r></m:oMath>")
    html = "<html><body>%s<!--StartFragment--><p>%s</p><!--EndFragment--></body></html>" % (other, copied)
    assert body(omml_from_html(html)) == "x"
    # Windows "HTML Format": byte offsets in the header
    fragment = "<p>%s</p>" % copied
    page = "<html><body>%s<!--StartFragment-->%s<!--EndFragment--></body></html>" % (other, fragment)
    header = "Version:0.9\r\nStartHTML:%010d\r\nEndHTML:%010d\r\nStartFragment:%010d\r\nEndFragment:%010d\r\n"
    size = len(header % (0, 0, 0, 0))
    start = size + page.index(fragment)
    raw = (header % (size, size + len(page), start, start + len(fragment)) + page).encode("utf-8")
    assert cf_html_fragment(raw).decode("utf-8") == fragment
    assert cf_html_fragment(b"<html>no header</html>") == b"<html>no header</html>"
    assert cf_html_fragment(b"StartFragment:9999\r\nEndFragment:99999\r\n<p>") == b"StartFragment:9999\r\nEndFragment:99999\r\n<p>"


def test_alignment_marks():
    def r(text, props=""):
        return "<m:r>%s<m:t>%s</m:t></m:r>" % ("<m:rPr>%s</m:rPr>" % props if props else "", text)

    def para(*lines):
        return "<m:oMathPara>%s</m:oMathPara>" % "".join("<m:oMath>%s</m:oMath>" % line for line in lines)

    # the mark on a run of upright text: the & stays outside \text
    assert body(para(r("a") + r("=", "<m:aln/><m:nor/>") + r("b"), r("=", "<m:aln/><m:nor/>") + r("c"))) == \
        "\\begin{aligned}\na & \\text{=}b \\\\\n & \\text{=}c\n\\end{aligned}"
    # a line with an & of its own: not an equation array (texmath would align at it)
    tex = latex(para(r("a") + r("=", "<m:aln/>") + r("R&amp;D"), r("=", "<m:aln/>") + r("c")))
    assert "aligned" not in tex and "R\\&" in tex
    # a mark that is off does not count; the one that is on does
    assert "aligned" not in latex(para(r("a") + r("=", '<m:aln m:val="0"/>') + r("b"), r("c") + r("=") + r("d")))
    assert body(para(r("a") + r("=", '<m:aln m:val="off"/>') + r("b") + r("+", "<m:aln/>") + r("c"),
                     r("+", "<m:aln/>") + r("d"))) == "\\begin{aligned}\na = b & + c \\\\\n & + d\n\\end{aligned}"
    # a mark on an operator box (Word's "operator emulator"), and only the first mark of a line
    box = '<m:box><m:boxPr><m:opEmu m:val="1"/><m:aln/></m:boxPr><m:e><m:r><m:t>=</m:t></m:r></m:e></m:box>'
    assert body(para(r("a") + box + r("b"), box + r("c"))).count("&") == 2
    tex = body(para(r("a") + r("=", "<m:aln/>") + r("b") + r("+", "<m:aln/>") + r("c"), r("=", "<m:aln/>") + r("d")))
    assert tex == "\\begin{aligned}\na & = b + c \\\\\n & = d\n\\end{aligned}"
