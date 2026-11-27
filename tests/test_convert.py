"""End-to-end conversions through pandoc/texmath (needs pandoc installed)."""
import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from mathshuttle import pandoc_engine as pe  # noqa: E402
from mathshuttle.convert import convert  # noqa: E402
from mathshuttle.pandoc_engine import ConversionError, Equation, read_equations, write_equation  # noqa: E402

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
    # text in a formula: any argument of a text command, also after options or a TeX box spec
    assert not has("\\raisebox{1pt}{$x$}") and not has("\\text {for $n$}") and not has("\\hbox to 3cm{$x$}")
    assert not has("\\text{\\textbf{$x$} {$y$}}") and not has("\\makebox[2cm][l]{$x$}")
    # but a group that is not a text-command argument is prose
    assert has("{$x$}") and has("\\emph{for $n$}") and has("{\\bf where $x$ is given}") and has("\\mbox{a} + $x$")


@pytest.mark.parametrize("src, tex", [("\\emph{for $n \\ge 1$}", "n \\ge 1"), ("{\\bf where $x$ is given}", "x"),
                                      ("{$x$}", "x")])
def test_prose_in_a_group_is_read_by_pandoc(src, tex):
    # used to be taken for one bare equation: "\[ \emph{for $n \ge 1$} \]", or a texmath error
    assert convert(src, "latex", "latex").outputs == ["$%s$" % tex]


@pytest.mark.parametrize("definition", ["\\newcommand{\\note}[1]{\\mbox{#1}}", "\\newcommand\\note[1]{\\text{#1}}",
                                        "\\def\\note#1{\\textrm{#1}}", "\\newcommand{\\inner}[1]{\\mbox{#1}}"
                                        "\\newcommand{\\note}[1]{\\inner{#1}}"])
def test_text_macros_defined_in_the_input_keep_a_bare_equation_whole(definition):
    # a macro that wraps \mbox / \text: its argument is text inside the formula, as for \mbox itself
    # (pandoc's prose reader used to get it, expand it and return only the nested "n")
    src = definition + "\na_n \\le b_n \\quad \\note{for all $n$}"
    assert not pe._has_math_delimiters(src)
    assert convert(src, "latex", "latex", wrapper="bare").outputs == [src]
    if not definition.startswith("\\def"):  # texmath does not know \def
        assert convert(src, "latex", "mathml").text.count("<mi>n</mi>") == 3  # a_n, b_n and the text's n


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


@pytest.mark.parametrize("label", ["eq/loss", "eq:loss+reg", "eq(1)", "eq,1", "eq a", 'say "hi"'])
def test_typst_label_function_is_removed(label):
    # labels that are not valid Typst label syntax come out as $ x = 1 $#label("eq/loss")
    assert convert("\\begin{equation}x = 1\\label{%s}\\end{equation}" % label, "latex", "typst").text == "x = 1"


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


def test_cdata_and_comments_are_left_alone():
    # entity and declaration clean-up used to rewrite literal text: \text{\&\#x2264;}
    assert convert("<math><mtext><![CDATA[&le;]]></mtext></math>", "mathml", "latex").text == "$\\text{\\&le;}$"
    assert convert('<math><mtext><![CDATA[<?xml version="1.0"?>]]></mtext></math>', "mathml",
                   "latex").text == '$\\text{<?xml version="1.0"?>}$'
    assert convert("<math><!-- &foo; --><mi>&alpha;</mi></math>", "mathml", "latex").text == "$\\alpha$"
    # a DOCTYPE is still removed whole, also with comments or brackets in its internal subset
    for doctype in ('<!DOCTYPE math [<!-- note -->]>', '<!DOCTYPE math [<!ENTITY x "a]b"> <!-- ]> -->]>',
                    '<!DOCTYPE math PUBLIC "-//W3C//DTD MathML 2.0//EN" "mathml2.dtd">'):
        assert convert(doctype + "\n<math><mi>x</mi></math>", "mathml", "latex").text == "$x$", doctype


OMML_X = "<m:oMath><m:r><m:t>x</m:t></m:r></m:oMath>"


@pytest.mark.parametrize("src, fmt", [
    ("<m:oMath></m:oMath>", "omml"),
    ("<m:oMath><m:foo><m:r><m:t>y</m:t></m:r></m:foo></m:oMath>", "omml"),  # texmath skips unknown elements
    (OMML_X + "<m:oMath/>", "omml"),
    ("<math></math>", "mathml"),
])
def test_empty_equation_is_an_error(src, fmt):
    # used to give an empty result: "$$" in LaTeX, <mrow /> in MathML
    with pytest.raises(ConversionError) as e:
        convert(src, fmt, "latex")
    assert "texmath could not interpret" in str(e.value)
    if src.startswith(OMML_X):
        assert "equation 2 of the 2" in str(e.value)


def test_omml_alternate_content_with_several_choices_is_read_once():
    ns = ('xmlns:mc="http://schemas.openxmlformats.org/markup-compatibility/2006" '
          'xmlns:m="http://schemas.openxmlformats.org/officeDocument/2006/math"')
    alt = ('<mc:AlternateContent %s><mc:Choice Requires="a">%s</mc:Choice><mc:Choice Requires="b">%s</mc:Choice>'
           '<mc:Fallback>%s</mc:Fallback></mc:AlternateContent>' % (ns, OMML_X, OMML_X, OMML_X))
    assert convert(alt, "omml", "latex").outputs == ["$x$"]
    fallback_only = '<mc:AlternateContent %s><mc:Fallback>%s</mc:Fallback></mc:AlternateContent>' % (ns, OMML_X)
    assert convert(fallback_only, "omml", "latex").outputs == ["$x$"]


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
        assert back == "\\[\n\\begin{aligned}\na & = b \\\\\nc & = d\n\\end{aligned}\n\\]"
    else:
        assert back == convert(plain, "mathml", "latex").text
    assert 'columnspacing' not in convert(tex, "latex", "mathml").text  # the MathML output stays texmath's


TABLE = "\\[\n\\begin{aligned}\n%s\n\\end{aligned}\n\\]"
OFFICE_SHAPES = [  # LaTeX, cells per row in the MathML for Office (None: unchanged), LaTeX back from it
    # a trailing \\ or a line without & gives texmath rows with fewer cells: padded, a trailing empty row dropped
    ("\\begin{align} a &= b \\\\ c &= d \\\\ \\end{align}", [2, 2], TABLE % "a & = b \\\\\nc & = d"),
    ("\\begin{aligned} a &= b \\\\ c &= d \\\\ \\end{aligned}", [2, 2], TABLE % "a & = b \\\\\nc & = d"),
    ("\\begin{align} a &= b \\\\ c \\end{align}", [2, 2], TABLE % "a & = b \\\\\nc & "),
    ("\\begin{eqnarray} a &=& b \\\\ c \\end{eqnarray}", [2, 2], TABLE % "a & = b \\\\\nc & "),
    ("\\begin{align} a &= b & c &= d \\\\ e &= f \\end{align}", [4, 4], TABLE % "a & = b & c & = d \\\\\ne & = f & & "),
    # an array is an equation array only when it reads like one; a table of values stays a matrix
    ("\\[\\left\\{\\begin{array}{rl} x+y &= 1 \\\\ x-y &= 0 \\end{array}\\right.\\]", [2, 2],
     "\\[\n\\left\\{ \\begin{aligned}\nx + y & = 1 \\\\\nx - y & = 0\n\\end{aligned} \\right.\n\\]"),
    ("\\[\\begin{array}{rcl} x+y &=& 1 \\\\ x &=& 2 \\end{array}\\]", [2, 2], TABLE % "x + y & = 1 \\\\\nx & = 2"),
    ("\\[\\begin{array}{rcl}1&2&3\\\\4&5&6\\end{array}\\]", None, None),  # used to lose its middle column
    ("\\[\\begin{array}{rlr}1&2&3\\\\4&5&6\\end{array}\\]", None, None),
    ("\\[f=\\left\\{\\begin{array}{rl}1, & x>0\\\\0, & \\text{else}\\end{array}\\right.\\]", None, None),
    ("\\[\\begin{array}{p{\\linewidth}} x \\end{array}\\]", None, None),
    ("\\[\\begin{array}% column specification\n{rcl} 1 & 2 & 3 \\\\ 4 & 5 & 6 \\end{array}\\]", None, None),
    ("\\[\\begin{array}{rl} x & \\quad = 1 \\\\ y & \\quad = 2 \\end{array}\\]", [2, 2], None),  # spacing, then =
    # a last row without text is not always empty
    ("\\begin{aligned} a &= b \\\\ & \\sqrt{} \\end{aligned}", [2, 2], None),
    ("\\begin{aligned} a &= b \\\\ & \\frac{}{} \\end{aligned}", [2, 2], None),
]


@pytest.mark.parametrize("tex, cells, back", OFFICE_SHAPES)
def test_mathml_for_office_shapes_ragged_tables_and_arrays(tex, cells, back):
    import xml.etree.ElementTree as ET
    from mathshuttle.convert import mathml_for_office
    ns = "{http://www.w3.org/1998/Math/MathML}"
    office = mathml_for_office(tex, "latex")[0]  # what Copy for Word/PowerPoint puts on the clipboard
    if cells is None:
        assert office == write_equation(read_equations(tex, "latex")[0], "mathml")
    else:
        table = next(ET.fromstring(office).iter(ns + "mtable"))
        assert table.get("columnspacing") == "0em"
        assert [len(r.findall(ns + "mtd")) for r in table] == cells
        if back is not None:
            assert convert(office, "mathml", "latex").text == back


def test_mathml_for_office_shapes_only_the_alignment_around_an_array():
    import xml.etree.ElementTree as ET
    from mathshuttle.convert import mathml_for_office
    ns = "{http://www.w3.org/1998/Math/MathML}"
    office = mathml_for_office("\\begin{align} a &= \\begin{array}{rl} 1 & 2 \\end{array} \\\\ c &= d \\end{align}", "latex")[0]
    assert [t.get("columnspacing") for t in ET.fromstring(office).iter(ns + "mtable")] == ["0em", None]


def test_numbering_commands_moved_into_aligned_are_flagged():
    src = "\\begin{align}\na &= b \\tag{1} \\\\\nc &= d \\label{eq:c}\n\\end{align}"
    assert convert(src, "latex", "latex", wrapper="equation").notes
    assert convert(src, "latex", "latex", wrapper="bracket").notes
    assert not convert(src, "latex", "latex", wrapper="align*").notes
    assert not convert(src, "latex", "latex").notes  # auto keeps the align environment
    one_label = "\\begin{align}\na &= b \\label{eq:a} \\\\\nc &= d\n\\end{align}"
    assert not convert(one_label, "latex", "latex", wrapper="equation").notes
    # a \tag is an error in inline maths, also when it comes from a single equation
    tagged = "\\begin{equation}x\\tag{A}\\end{equation}"
    assert convert(tagged, "latex", "latex", wrapper="dollar").notes
    assert convert(tagged, "latex", "latex", wrapper="paren").notes
    assert convert("x \\tag{A}", "latex", "latex", mode="inline").notes  # Auto -> $...$
    assert not convert(tagged, "latex", "latex", wrapper="bracket").notes  # \[ \tag{A} \] is fine
    assert not convert(tagged, "latex", "latex", mode="inline").notes  # Auto keeps the equation


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
    # Auto keeps a display environment even when Mode = Inline: the status must say display
    r = convert("\\begin{equation}x\\end{equation}", "latex", "latex", mode="inline")
    assert r.text.startswith("\\begin{equation}") and r.equations[0].display
    assert not convert("x", "latex", "latex", mode="inline").equations[0].display


def test_relations_of_office_equation_arrays():
    # Word exports an equation array without alignment attributes; a relation in the right-hand cells
    # identifies it.  Arrows, negated and other relations, and decorated ones (\overset{!}{=}) count.
    row = '<mtr><mtd><mi>a</mi></mtd><mtd>%s<mi>b</mi></mtd></mtr>'
    word = '<math xmlns="http://www.w3.org/1998/Math/MathML" display="block"><mtable>%s%s</mtable></math>'
    for rel in ["≲", "≢", "⊊", "↪", "≦", "≰", "∶", "⟹", "⩽"]:
        mo = "<mo>%s</mo>" % rel
        assert convert(word % (row % mo, row % mo), "mathml", "latex", wrapper="bare").text.startswith("\\begin{aligned}"), rel
    over = "<mover><mo>=</mo><mi>!</mi></mover>"
    assert convert(word % (row % over, row % "<mo>=</mo>"), "mathml", "latex", wrapper="bare").text.startswith("\\begin{aligned}")
    spaced = "<mo> </mo><mo>=</mo>"  # Word's export of &\quad = b: the relation after the space counts
    assert convert(word % (row % spaced, row % spaced), "mathml", "latex", wrapper="bare").text.startswith("\\begin{aligned}")
    for op in ["∪", "⊕", "⊤", "∸", "∺"]:  # operators and ordinary symbols are not relations: a matrix
        mo = "<mo>%s</mo>" % op
        assert convert(word % (row % mo, row % mo), "mathml", "latex", wrapper="bare").text.startswith("\\begin{matrix}"), op


def test_office_equation_arrays_with_several_alignment_pairs():
    # Word 16.0.20527's export of a \begin{aligned} a &= b & c &= d ... typed into it, without the
    # columnalign attributes (older Word, other programs): right / left pairs
    cell = "<mtd>%s</mtd>"
    math = '<math xmlns="http://www.w3.org/1998/Math/MathML" display="block"><mtable columnspacing="0em">%s</mtable></math>'

    def table(*rows):
        return math % "".join("<mtr>%s</mtr>" % "".join(cell % c for c in r) for r in rows)

    pairs = table(["<mi>a</mi>", "<mo>=</mo><mi>b</mi>", "<mi>c</mi>", "<mo>=</mo><mi>d</mi>"],
                  ["<mi>e</mi>", "<mo>=</mo><mi>f</mi>", "<mo>&#x2003;</mo><mi>g</mi>", "<mo>=</mo><mi>h</mi>"])
    assert convert(pairs, "mathml", "latex", wrapper="bare").text == \
        "\\begin{aligned}\na & = b & c & = d \\\\\ne & = f & \\quad g & = h\n\\end{aligned}"
    # a second pair without a relation in its left-aligned cells, an odd width, ragged rows: matrices
    values = table(["<mi>a</mi>", "<mo>=</mo><mi>b</mi>", "<mn>1</mn>", "<mn>2</mn>"],
                   ["<mi>e</mi>", "<mo>=</mo><mi>f</mi>", "<mn>3</mn>", "<mn>4</mn>"])
    odd = table(["<mi>a</mi>", "<mo>=</mo><mi>b</mi>", "<mi>c</mi>"], ["<mi>e</mi>", "<mo>=</mo><mi>f</mi>", "<mi>g</mi>"])
    ragged = table(["<mi>a</mi>", "<mo>=</mo><mi>b</mi>", "<mi>c</mi>", "<mo>=</mo><mi>d</mi>"],
                   ["<mi>e</mi>", "<mo>=</mo><mi>f</mi>"])
    for m in (values, odd, ragged):
        assert convert(m, "mathml", "latex", wrapper="bare").text.startswith("\\begin{matrix}")


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


# ---- which equation "Copy for Word/PowerPoint" copies ---------------------------------------
def test_office_copy_picks_an_equation():
    from mathshuttle.convert import input_equations, office_copy
    text = "$a$ and $\\frac{b}{2}$ and \\[c^2\\]"
    assert [e.tex for e in input_equations(text, "latex")] == ["a", "\\frac{b}{2}", "c^2"]
    first, second = office_copy(text, "latex"), office_copy(text, "latex", index=1)
    assert "<mi>a</mi>" in first.mathml and "<mfrac>" in second.mathml
    assert "Equation 2 of the 3 in the input was copied." in second.notes
    assert not office_copy("$a$", "latex").notes  # one equation: nothing to say
    with pytest.raises(ConversionError, match="no equation 4: the input has 3"):
        office_copy(text, "latex", index=3)


def test_cli_copy_index(monkeypatch, capsys, tmp_path):
    from mathshuttle import clipboard
    from mathshuttle.__main__ import main
    copied = []
    monkeypatch.setattr(clipboard, "copy_mathml_for_office", lambda mathml, *a, **k: copied.append(mathml))
    source = tmp_path / "in.tex"
    source.write_text("$a$ and $\\frac{b}{2}$", encoding="utf-8")
    assert main(["copy", "--index", "2", str(source)]) == 0
    assert "<mfrac>" in copied[-1] and "Equation 2 of the 2" in capsys.readouterr().err
    assert main(["copy", str(source)]) == 0 and "<mi>a</mi>" in copied[-1]  # the first by default
    assert main(["copy", "--index", "3", str(source)]) == 1
    assert "no equation 3: the input has 2" in capsys.readouterr().err
    assert main(["copy", "--index", "0", str(source)]) == 1


# ---- a TeX annotation that does not match its MathML ------------------------------------------
ANNOTATED = ('<math xmlns="http://www.w3.org/1998/Math/MathML" display="block"><semantics>%s'
             '<annotation encoding="application/x-tex">%s</annotation></semantics></math>')


@pytest.mark.parametrize("shown, tex, stale", [
    ("<msup><mi>y</mi><mn>2</mn></msup>", "x^2", True),                       # edited after the TeX
    ("<mi>a</mi><mo>=</mo><mi>b</mi>", "a \\neq b", True),
    ("<munderover><mo>&#x2211;</mo><mrow><mi>i</mi><mo>=</mo><mn>0</mn></mrow><mi>n</mi></munderover>",
     "\\sum_{i=1}^{n}", True),
    ("<msup><mi>x</mi><mn>2</mn></msup>", "x^2", False),
    # other renderers, same equation: math italic, \cdot, an accent character, spacing, \sin, \mathbb
    ("<mi>&#x1D465;</mi><mo>&#x22C5;</mo><mi>y</mi>", "x \\cdot y", False),
    ("<mover><mi>x</mi><mo>&#x02C6;</mo></mover>", "\\hat{x}", False),
    ("<mfrac><mi>a</mi><mi>b</mi></mfrac><mspace width='0.167em'/><mi>d</mi><mi>x</mi>",
     "\\frac{a}{b}\\,\\mathrm{d}x", False),
    ("<mi>sin</mi><mo>&#x2061;</mo><mi>x</mi>", "\\sin x", False),
    ("<mi mathvariant='double-struck'>R</mi>", "\\mathbb{R}", False),
    ("<mi>x</mi>", "\\notdefinedhere{x}", False),                             # unreadable: not compared
])
def test_stale_tex_annotation(shown, tex, stale):
    from mathshuttle.convert import annotation_notes
    math = ANNOTATED % ("<mrow>%s</mrow>" % shown, tex)
    assert bool(annotation_notes(math)) == stale
    result = convert(math, "mathml", "latex")
    assert any("may be out of date" in n for n in result.notes) == stale


def test_stale_annotation_conversion_follows_the_mathml():
    result = convert(ANNOTATED % ("<mrow><msup><mi>y</mi><mn>2</mn></msup></mrow>", "x^2"), "mathml", "latex")
    assert result.text == "\\[ y^{2} \\]"


def test_annotation_written_by_mathshuttle_matches():
    from mathshuttle.convert import annotation_notes
    for tex in ("\\frac{\\partial f}{\\partial x} + \\sum_{i=1}^{n} \\mathbf{x}_i^2",
                "\\hat{\\theta} = \\arg\\min_x f(x)", "\\vec{v} \\cdot \\bar{w} \\neq 0"):
        assert annotation_notes(convert(tex, "latex", "mathml", annotate=True).text) == []
