"""End-to-end conversions through pandoc/texmath (needs pandoc installed)."""
import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from equation_tool import pandoc_engine as pe  # noqa: E402
from equation_tool.convert import convert  # noqa: E402
from equation_tool.pandoc_engine import ConversionError, Equation, read_equations, write_equation  # noqa: E402

ALIGN = "\\begin{align}\na &= b + c \\\\\nd &= \\int_0^1 e^{-t}\\,dt\n\\end{align}"


# ---- LaTeX input: every delimiter style -------------------------------------------------------
@pytest.mark.parametrize("src, display", [
    ("x^2", True),
    ("$x^2$", False),
    ("\\(x^2\\)", False),
    ("$$x^2$$", True),
    ("\\[x^2\\]", True),
    ("\\begin{math}x^2\\end{math}", False),
    ("\\begin{displaymath}x^2\\end{displaymath}", True),
    ("\\begin{equation}x^2\\end{equation}", True),
    ("\\begin{equation*}x^2\\end{equation*}", True),
])
def test_latex_delimiters_are_recognised(src, display):
    eqs = read_equations(src, "latex")
    assert len(eqs) == 1
    assert eqs[0].display is display
    assert "x^2" in eqs[0].tex


@pytest.mark.parametrize("env", ["align", "align*", "gather", "gather*", "multline", "multline*",
                                 "eqnarray", "eqnarray*", "flalign", "flalign*"])
def test_environments_convert_to_mathml_and_omml(env):
    src = "\\begin{%s}\na &= b \\\\\nc &= d\n\\end{%s}" % (env, env)
    r = convert(src, "latex", "mathml")
    assert "<mtable>" in r.text or "<mtable " in r.text
    r = convert(src, "latex", "omml")
    assert ("<m:eqArr>" in r.text or "<m:m>" in r.text) and r.text.startswith("<m:oMathPara")


def test_alignat_with_argument():
    src = "\\begin{alignat}{2}\na &= b & c &= d\n\\end{alignat}"
    assert "<mtable" in convert(src, "latex", "mathml").text


def test_labels_tags_nonumber_are_tolerated():
    src = "\\begin{align}\na &= b \\label{eq:a} \\\\\nc &= d \\nonumber \\\\\ne &= f \\tag{X}\n\\end{align}"
    assert "<m:eqArr>" in convert(src, "latex", "omml").text


def test_multiple_equations_with_prose():
    src = "where $x$ is given by\n\\[ x = \\int_0^1 f(t)\\,dt \\]\nand $$y = 2$$."
    r = convert(src, "latex", "latex", wrapper="auto")
    assert r.outputs == ["$x$", "\\[ x = \\int_0^1 f(t)\\,dt \\]", "\\[ y = 2 \\]"]
    assert r.notes


def test_newcommand_is_expanded():
    src = "\\newcommand{\\R}{\\mathbb{R}}\n$f: \\R \\to \\R$"
    assert "\\mathbb{R}" in convert(src, "latex", "latex", wrapper="bare").text


@pytest.mark.parametrize("cmd", ["\\mbox", "\\textrm", "\\textit", "\\hbox", "\\text"])
def test_bare_equation_with_math_inside_text_is_one_equation(cmd):
    # pandoc's LaTeX reader would return only the nested "$n$" as the equation
    src = "a_n \\le b_n \\quad %s{for all $n$}" % cmd
    r = convert(src, "latex", "latex", wrapper="bare")
    assert r.outputs == [src]
    assert convert(src + " % note", "latex", "latex", mode="inline").text == "$%s %% note\n$" % src
    if cmd == "\\mbox":
        assert "for all" in convert(src, "latex", "mathml").text


def test_math_delimiters_detection():
    has = pe._has_math_delimiters
    assert has("where $x$ holds") and has("\\(x\\)") and has("text \\[ x \\]") and has("\\begin{align*} a \\end{align*}")
    assert not has("a \\\\[2pt] b")                   # a line break with spacing, not \[
    assert not has("\\text{cost in \\$} x")           # escaped dollar
    assert not has("x % comment with $")
    assert not has("\\begin{aligned} a &= b \\end{aligned}")  # math-mode environment
    assert not has("\\mbox{if $x > 0$}")


# ---- mode handling ------------------------------------------------------------------------------
def test_mode_controls_output_form():
    assert 'display="block"' in convert("x^2", "latex", "mathml").text
    assert 'display="inline"' in convert("x^2", "latex", "mathml", mode="inline").text
    assert 'display="inline"' in convert("$x^2$", "latex", "mathml").text
    assert 'display="block"' in convert("$x^2$", "latex", "mathml", mode="display").text
    assert convert("$x^2$", "latex", "omml").text.startswith("<m:oMath ")
    assert convert("$x^2$", "latex", "omml", mode="display").text.startswith("<m:oMathPara")
    # LaTeX output: the wrapper decides
    assert convert("$$x^2$$", "latex", "latex", wrapper="dollar").text == "$x^2$"
    assert convert("$x^2$", "latex", "latex", wrapper="bracket").text == "\\[ x^2 \\]"
    assert convert("$x^2$", "latex", "latex").text == "$x^2$"
    assert convert("x^2", "latex", "latex").text == "\\[ x^2 \\]"
    assert convert("x^2", "latex", "latex", mode="inline").text == "$x^2$"


def test_latex_to_latex_align_wrappers():
    assert convert(ALIGN, "latex", "latex", wrapper="align*").text.startswith("\\begin{align*}\na &= b + c")
    out = convert(ALIGN, "latex", "latex", wrapper="bracket").text
    assert out.startswith("\\[\n\\begin{aligned}") and out.endswith("\\end{aligned}\n\\]")
    assert convert(ALIGN, "latex", "latex").text == ALIGN


# ---- other formats -----------------------------------------------------------------------------
def test_outputs_eqn_typst():
    assert convert("\\frac{a}{b}", "latex", "eqn").text == "a over b"
    assert convert("\\frac{a}{b}", "latex", "typst").text == "a / b"
    assert convert("\\sqrt{x}", "latex", "typst", mode="inline").text == "sqrt(x)"
    # a \label becomes a Typst label after the closing $: still only the body is returned
    assert convert("\\begin{equation}x = 1\\label{eq:a}\\end{equation}", "latex", "typst").text == "x = 1"


def test_multiline_outputs_have_no_carriage_returns():
    # pandoc writes CRLF on Windows unless told otherwise
    aligned = "\\begin{aligned} a &= b \\\\ c &= d \\end{aligned}"
    assert convert(aligned, "latex", "typst").text == "a & = b\\\nc & = d"
    matrix = convert("\\begin{pmatrix} a & b \\\\ c & d \\end{pmatrix}", "latex", "eqn").text
    assert "\r" not in matrix and matrix.startswith("left ( matrix{\n")


@pytest.mark.parametrize("fmt", ["mathml", "omml", "eqn", "typst"])
def test_multiline_texmath_error_is_reported(fmt):
    # the TeX in pandoc's warning spans several lines; eqn / typst used to return the raw TeX
    with pytest.raises(ConversionError) as e:
        convert("\\begin{align}\na &= \\frac{b}\n\\end{align}", "latex", fmt)
    assert str(e.value).startswith("texmath could not parse this LaTeX:\na &= \\frac{b}\n     ^")


def test_mathml_roundtrip():
    mathml = convert("\\frac{a}{b} + x^2", "latex", "mathml").text
    r = convert(mathml, "mathml", "latex", wrapper="bare")
    assert r.text == "\\frac{a}{b} + x^{2}"
    assert r.equations[0].display
    assert not convert(mathml.replace('display="block"', 'display="inline"'), "mathml", "latex").equations[0].display


def test_mathml_without_namespace_and_with_prolog():
    src = '<?xml version="1.0"?>\n<math><mi>x</mi></math>'
    assert convert(src, "mathml", "latex", wrapper="bare").text == "x"


def test_mathml_input_variants():
    ns = 'xmlns:mml="http://www.w3.org/1998/Math/MathML"'
    # namespace prefix (Word's XSLT, MathType, JATS), declared or not
    assert convert('<mml:math %s><mml:mi>x</mml:mi><mml:mo>+</mml:mo><mml:mn>1</mml:mn></mml:math>' % ns,
                   "mathml", "latex", wrapper="bare").text == "x + 1"
    assert convert('<mml:math><mml:mi>x</mml:mi></mml:math>', "mathml", "latex", wrapper="bare").text == "x"
    # named entities (Mathematica, Maple, hand-written MathML)
    assert convert('<math><mi>a</mi><mo>&le;</mo><mi>b</mi><mo>&InvisibleTimes;</mo><mi>c</mi></math>',
                   "mathml", "latex", wrapper="bare").text == "a \\leq bc"
    # a BOM, and several documents each with its own XML declaration
    decl = '<?xml version="1.0" encoding="UTF-8"?>\n'
    assert convert('\ufeff' + decl + '<math><mi>x</mi></math>', "mathml", "latex", wrapper="bare").text == "x"
    r = convert(decl + '<math><mi>x</mi></math>\n' + decl + '<math><mi>y</mi></math>', "mathml", "latex", wrapper="bare")
    assert r.outputs == ["x", "y"]


def test_mathml_element_texmath_rejects_is_not_dropped():
    with pytest.raises(ConversionError) as e:
        convert('<math><mi>x</mi></math><math><mfoo><mi>y</mi></mfoo></math>', "mathml", "latex")
    assert "element 2 of the 2" in str(e.value)


def test_mathml_annotation_option():
    assert "<annotation" not in convert("x", "latex", "mathml").text
    assert 'annotation encoding="application/x-tex"' in convert("x", "latex", "mathml", annotate=True).text


def test_office_equation_array_becomes_aligned():
    word = ('<math xmlns="http://www.w3.org/1998/Math/MathML" display="block"><mtable><mtr><mtd><mi>a</mi></mtd>'
            '<mtd><mo>=</mo><mi>b</mi></mtd></mtr><mtr><mtd><mi>c</mi></mtd><mtd><mo>≤</mo><mi>d</mi></mtd></mtr></mtable></math>')
    out = convert(word, "mathml", "latex", wrapper="bare").text
    assert out.startswith("\\begin{aligned}")
    # a real 2x2 matrix is left alone
    matrix = word.replace("<mo>=</mo>", "").replace("<mo>≤</mo>", "")
    assert convert(matrix, "mathml", "latex", wrapper="bare").text.startswith("\\begin{matrix}")
    # continuation lines (a &= b \\ &+ c) do not turn the block into a matrix
    cont = word.replace("<mtd><mi>c</mi></mtd><mtd><mo>≤</mo>", "<mtd></mtd><mtd><mo>+</mo>")
    assert convert(cont, "mathml", "latex", wrapper="bare").text.startswith("\\begin{aligned}")
    # ... but a table without any relation is still a matrix
    signs = matrix.replace("<mtd><mi>b</mi>", "<mtd><mo>−</mo><mi>b</mi>").replace("<mtd><mi>d</mi>", "<mtd><mo>+</mo><mi>d</mi>")
    assert convert(signs, "mathml", "latex", wrapper="bare").text.startswith("\\begin{matrix}")


OFFICE_TABLES = {  # LaTeX -> cells per row in the MathML for Office (None: an ordinary table, unchanged)
    ALIGN: [2, 2],
    "\\begin{align} f &= a \\\\ &\\quad + c \\end{align}": [2, 2],
    "\\begin{align} a &= b & c &= d \\end{align}": [4],
    "\\begin{equation}\\begin{split} a &= b \\\\ &= c \\end{split}\\end{equation}": [2, 2],
    "\\begin{eqnarray} a &=& b \\\\ c &=& d \\end{eqnarray}": [2, 2],  # the relation joins the right side
    "$$\\begin{aligned} a &= b \\end{aligned} + x$$": [2],
    "$$\\begin{pmatrix} a & b \\\\ c & d \\end{pmatrix}$$": None,
    "$$f = \\begin{cases} 1 & x > 0 \\\\ 0 & \\text{else} \\end{cases}$$": None,
    "\\begin{gather} a = b \\\\ c = d \\end{gather}": None,
}


@pytest.mark.parametrize("tex, cells", list(OFFICE_TABLES.items()))
def test_mathml_for_office_marks_equation_arrays(tex, cells):
    # Word pastes a table as an equation array only with columnspacing="0em" (see tests/office_smoke.py)
    import xml.etree.ElementTree as ET
    ns = "{http://www.w3.org/1998/Math/MathML}"
    plain = write_equation(read_equations(tex, "latex")[0], "mathml")
    office = write_equation(read_equations(tex, "latex")[0], "mathml", for_office=True)
    if cells is None:
        assert office == plain
    else:
        table = next(ET.fromstring(office).iter(ns + "mtable"))
        assert table.get("columnspacing") == "0em"
        assert [len(r.findall(ns + "mtd")) for r in table] == cells
    # Copy for Word, then Paste from Word in this app gives the same LaTeX as the plain MathML -
    # except eqnarray, whose r/c/l columns became an align-style array (as Word now holds it)
    back = convert(office, "mathml", "latex").text
    if "eqnarray" in tex:
        assert back == "\\[\n\\begin{aligned}\na & {= b} \\\\\nc & {= d}\n\\end{aligned}\n\\]"
    else:
        assert back == convert(plain, "mathml", "latex").text
    assert 'columnspacing' not in convert(tex, "latex", "mathml").text  # the MathML output stays texmath's


def test_numbering_commands_moved_into_aligned_are_flagged():
    src = "\\begin{align}\na &= b \\tag{1} \\\\\nc &= d \\label{eq:c}\n\\end{align}"
    assert convert(src, "latex", "latex", wrapper="equation").notes
    assert convert(src, "latex", "latex", wrapper="bracket").notes
    assert not convert(src, "latex", "latex", wrapper="align*").notes
    assert not convert(src, "latex", "latex").notes  # auto keeps the align environment
    one_label = "\\begin{align}\na &= b \\label{eq:a} \\\\\nc &= d\n\\end{align}"
    assert not convert(one_label, "latex", "latex", wrapper="equation").notes


def test_omml_roundtrip_and_loose_input():
    omml = convert(ALIGN, "latex", "omml").text
    r = convert(omml, "omml", "latex", wrapper="align")
    assert r.text.startswith("\\begin{align}\na & = b + c")
    # bare <m:oMath> without namespace declarations, and inline
    bare = omml[omml.index("<m:oMath>"):omml.index("</m:oMath>") + len("</m:oMath>")]
    r2 = convert(bare, "omml", "latex")
    assert not r2.equations[0].display and r2.text.startswith("$")
    # OMML embedded in a whole Word paragraph / document is found too
    doc = ('<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main" '
           'xmlns:m="http://schemas.openxmlformats.org/officeDocument/2006/math"><w:body><w:p><w:r><w:t>hi</w:t></w:r>'
           + bare + '</w:p></w:body></w:document>')
    assert convert(doc, "omml", "latex", wrapper="bare").text == r2.outputs[0][1:-1]
    # an equation in a text box is stored twice (DrawingML choice + VML fallback): read it once
    alt = ('<mc:AlternateContent><mc:Choice Requires="wps">%s</mc:Choice><mc:Fallback>%s</mc:Fallback>'
           '</mc:AlternateContent>' % (bare, bare))
    assert convert(alt, "omml", "latex").outputs == r2.outputs


def test_latex_output_mode_follows_the_wrapper():
    assert convert("$x$", "latex", "latex", wrapper="bracket").equations[0].display
    assert not convert("$$x$$", "latex", "latex", wrapper="dollar").equations[0].display


# ---- errors ------------------------------------------------------------------------------------
def test_errors_are_reported():
    with pytest.raises(ConversionError) as e:
        convert("\\frac{a}", "latex", "mathml")
    assert "unexpected eof" in str(e.value)
    with pytest.raises(ConversionError):
        convert("", "latex", "mathml")
    with pytest.raises(ConversionError) as e:
        convert("<math><mi>x</math>", "mathml", "latex")
    assert "well-formed" in str(e.value)
    with pytest.raises(ConversionError):
        convert("<p>no math here</p>", "mathml", "latex")
    with pytest.raises(ConversionError):
        convert("<w:p/>", "omml", "latex")
    # LaTeX -> LaTeX never touches texmath, so even odd input passes through
    assert convert("\\frac{a}", "latex", "latex", wrapper="bare").text == "\\frac{a}"


def test_write_equation_unknown_format():
    with pytest.raises(ValueError):
        write_equation(Equation("x"), "docx")
