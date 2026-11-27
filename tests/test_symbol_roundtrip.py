"""Symbols which survive the Office clipboard format and the Office document format."""
import xml.etree.ElementTree as ET

import pytest

from mathshuttle import tidy
from mathshuttle.convert import convert, for_texmath, mathml_for_office
from mathshuttle.pandoc_engine import ConversionError, Equation, mathml_elements


# The 340 entries in Fable's symbol sweep (nexists occurs twice in the original).
SYMBOLS = """
alpha beta gamma delta epsilon varepsilon zeta eta theta vartheta iota kappa varkappa lambda mu nu xi pi varpi rho
varrho sigma varsigma tau upsilon phi varphi chi psi omega Gamma Delta Theta Lambda Xi Pi Sigma Upsilon Phi Psi Omega
digamma aleph beth gimel daleth hbar hslash ell wp Re Im partial nabla infty emptyset varnothing forall exists nexists
neg lnot top bot angle measuredangle sphericalangle triangle square blacksquare Box Diamond lozenge blacklozenge
bigstar star ast circ bullet cdot cdots ldots vdots ddots dots times div pm mp oplus ominus otimes oslash odot
bigoplus bigotimes bigodot biguplus bigsqcup bigcup bigcap bigvee bigwedge sum prod coprod int oint iint iiint
cup cap sqcup sqcap uplus vee wedge land lor setminus smallsetminus wr amalg dagger ddagger diamond
triangleleft triangleright bigtriangleup bigtriangledown vartriangle triangledown blacktriangle blacktriangledown
leq le geq ge neq ne equiv sim simeq approx cong propto ll gg lll ggg prec succ preceq succeq subset supset
subseteq supseteq nsubseteq nsupseteq subsetneq supsetneq sqsubset sqsupset sqsubseteq sqsupseteq in ni notin
vdash dashv models Vdash vDash perp parallel nparallel mid nmid asymp bowtie doteq triangleq coloneqq eqqcolon
lesssim gtrsim lessapprox gtrapprox leqslant geqslant nleq ngeq nless ngtr approxeq thicksim thickapprox
backsim eqsim because therefore between pitchfork smile frown
to gets rightarrow leftarrow leftrightarrow Rightarrow Leftarrow Leftrightarrow longrightarrow longleftarrow
longleftrightarrow Longrightarrow Longleftarrow Longleftrightarrow mapsto longmapsto hookrightarrow hookleftarrow
uparrow downarrow updownarrow Uparrow Downarrow Updownarrow nearrow searrow swarrow nwarrow rightharpoonup
rightharpoondown leftharpoonup leftharpoondown rightleftharpoons leftrightharpoons rightrightarrows leftleftarrows
twoheadrightarrow rightarrowtail leadsto rightsquigarrow leftrightsquigarrow nrightarrow nleftarrow nRightarrow
implies impliedby iff circlearrowleft circlearrowright curvearrowright upharpoonright restriction multimap
langle rangle lfloor rfloor lceil rceil lVert rVert lvert rvert vert Vert backslash lbrace rbrace lbrack rbrack
ulcorner urcorner llcorner lrcorner
prime backprime complement eth mho Finv Game Bbbk imath jmath checkmark maltese circledS circledast circledcirc
circleddash boxplus boxminus boxtimes boxdot dotplus ltimes rtimes leftthreetimes rightthreetimes barwedge veebar
doublebarwedge curlywedge curlyvee Cap Cup intercal centerdot divideontimes
flat natural sharp clubsuit diamondsuit heartsuit spadesuit S P dag ddag copyright pounds
lhd rhd unlhd unrhd Join varpropto varsubsetneq nexists varDelta varGamma varTheta varOmega
""".split()

# Unknown controls in texmath 0.13.3.5. If a newer engine accepts one, it must round-trip too.
UNSUPPORTED = {"thicksim", "thickapprox", "circledS", "S", "P", "copyright"}
# Tidy's documented set-difference convention for a literal backslash from Word.
GLYPH_EXCEPTIONS = {"backslash": ("a\\b", "a∖b")}


def characters(math):
    return "".join("".join(math.itertext()).split())


def office_xml(tex, fmt, mode):
    if fmt == "mathml":
        return mathml_for_office(tex, "latex", mode)[0]
    return convert(tex, "latex", "omml", mode).text


@pytest.fixture(scope="module")
def originals():
    equations = {}
    for name in SYMBOLS:
        tex = "a \\" + name + " b"
        try:
            equations[name] = office_xml(tex, "mathml", "inline")
        except ConversionError as error:
            assert name in UNSUPPORTED and "texmath could not parse" in str(error), (name, error)
    return equations


@pytest.mark.parametrize("fmt", ["mathml", "omml"])
def test_common_symbols(originals, fmt):
    # Read the independent equations in one pandoc call. Redraw them in a second batch and
    # compare exact code points, without the synonym folding used for annotation comparisons.
    inputs = originals.values() if fmt == "mathml" else (
        office_xml("a \\" + name + " b", fmt, "inline") for name in originals)
    latex = convert("".join(inputs), fmt, "latex", "inline", "bare").outputs
    assert len(latex) == len(originals)
    drawn = mathml_elements([for_texmath(Equation(tex, False), "latex", None)[0] for tex in latex])
    for (name, source), tex, math in zip(originals.items(), latex, drawn):
        assert tex.isascii() and r"\left." not in tex and r"\right." not in tex, (name, tex)
        assert math is not None, (name, tex)
        before, after = characters(ET.fromstring(source)), characters(math)
        assert before == after or (before, after) == GLYPH_EXCEPTIONS.get(name), (name, tex, before, after)


@pytest.mark.parametrize("fmt", ["mathml", "omml"])
def test_preferred_commands_and_distinct_glyphs(fmt):
    # Independent Office input distinguishes glyphs that the same LaTeX font may draw alike.
    symbols = {
        "ϝ": "digamma", "▫": "square", "▴": "blacktriangle", "▾": "blacktriangledown", "⊏": "sqsubset",
        "⊐": "sqsupset", "≔": "coloneqq", "≕": "eqqcolon", "⤳": "leadsto", "⌜": "ulcorner",
        "⌝": "urcorner", "⌞": "llcorner", "⌟": "lrcorner", "℧": "mho", "✓": "checkmark", "✠": "maltese",
        "ℏ": "hbar", "≼": "preceq", "≽": "succeq", "△": "triangle", "∧": "wedge", "∨": "vee",
        "⩽": "leqslant", "⩾": "geqslant", "≤": "leq", "≥": "geq", "⨿": "amalg", "∐": "coprod",
        "→": "rightarrow", "⟶": "longrightarrow", "□": "Box",
    }
    pattern = ("<math><mi>a</mi><mo>%s</mo><mi>b</mi></math>" if fmt == "mathml" else
               "<m:oMath><m:r><m:t>a%sb</m:t></m:r></m:oMath>")
    result = convert("".join(pattern % ch for ch in symbols), fmt, "latex", "inline", "bare")
    assert [tex.replace(" ", "") for tex in result.outputs] == ["a\\" + name + "b" for name in symbols.values()]


@pytest.mark.parametrize("attrs", ["", 'stretchy="false"', 'stretchy="true"'])
@pytest.mark.parametrize("symbol,expected", [("|", "a|b"), ("→", r"a \rightarrow b")])
def test_infix_mo_does_not_make_a_fence(attrs, symbol, expected):
    xml = "<math><mi>a</mi><mo %s>%s</mo><mi>b</mi></math>" % (attrs, symbol)
    assert convert(xml, "mathml", "latex", wrapper="bare").text == expected


@pytest.mark.parametrize("fmt", ["mathml", "omml"])
def test_office_writer_keeps_distinct_symbols(fmt):
    for name, ch in {"amalg": "⨿", "coprod": "∐", "leqslant": "⩽", "geqslant": "⩾",
                     "longrightarrow": "⟶", "Longleftarrow": "⟸", "longmapsto": "⟼",
                     "lVert": "‖", "rVert": "‖", "parallel": "∥"}.items():
        assert characters(ET.fromstring(office_xml("a \\" + name + " b", fmt, "inline"))) == "a" + ch + "b"


@pytest.mark.parametrize("fmt", ["mathml", "omml"])
@pytest.mark.parametrize("tex,expected", [
    (r"a\to b", r"a \rightarrow b"),
    (r"\left|x\right|", r"|x|"),  # texmath uses ordinary bars when the content needs no stretching
    (r"\left| \frac{a}{b} \right|", r"\left| \frac{a}{b} \right|"),
    (r"\left( \frac{a}{b} \right)", r"\left( \frac{a}{b} \right)"),
    (r"\{a\mid b\}", r"\{ a \mid b\}"),
    (r"\left\{a\middle|b\right\}", r"\left\{ a \middle| b \right\}"),
    (r"\xrightarrow{abc}", r"\xrightarrow{abc}"),
    (r"\xrightarrow[a]{b}", r"\xrightarrow[a]{b}"),
    (r"\xleftarrow[{[i]}]{x_j}", r"\xleftarrow[{[i]}]{x_{j}}"),
    (r"\underbrace{x+y}_{n_i}", r"\underbrace{x + y}_{n_{i}}"),
    (r"\overbrace{x+y}^{n}", r"\overbrace{x + y}^{n}"),
    (r"x \in [0,1]", r"x \in [0,1]"),
    (r"\arg\limits_{x}", r"\arg\limits_{x}"),  # plain \arg takes side scripts even in display
    (r"\frac{\max\limits_x}{n}", r"\frac{\max\limits_{x}}{n}"),
])
def test_structures(tex, expected, fmt):
    assert convert(office_xml(tex, fmt, "display"), fmt, "latex", wrapper="bare").text == expected


@pytest.mark.parametrize("fmt", ["mathml", "omml"])
def test_named_limits_follow_the_output_style(fmt):
    ops = [r"\max", r"\min", r"\lim", r"\sup", r"\inf", r"\liminf", r"\limsup",
           r"\operatorname*{argmax}", r"\operatorname*{arg\,min}"]
    xml = "".join(office_xml(op + r"\limits_{x}", fmt, "display") for op in ops)
    for mode, limits in (("inline", r"\limits"), ("display", "")):
        assert convert(xml, fmt, "latex", mode, "bare").outputs == [op + limits + "_{x}" for op in ops]


def test_literal_brackets_and_options():
    for start in (r"\\", r"\\*", r"\begin{aligned}", r"\sqrt", r"\xrightarrow"):
        assert tidy.tidy_tex(start + r" \lbrack 2\rbrack", True) == start + r" \lbrack 2]"
    for start in ("", r"\left", r"\in", r"\begin{matrix}"):
        assert tidy.tidy_tex(start + r"\lbrack 0,1\rbrack", True) == start + "[0,1]"


def test_limits_inside_labels_and_the_root_index():
    for tex in (r"\xrightarrow[\max\limits_{a}]{\min\limits_{b}}",
                r"\sqrt[\max\limits_{x}]{n}", r"{\textstyle \min\limits_{x}}"):
        assert tidy.tidy_tex(tex, True) == tex


def test_stretchy_middle_with_inferred_form():
    xml = '<math><mo>{</mo><mi>x</mi><mo stretchy="true">|</mo><mi>y</mi><mo>}</mo></math>'
    assert convert(xml, "mathml", "latex", wrapper="bare").text == r"\left\{ x \middle| y \right\}"
    xml = office_xml(r"\left.\frac{x}{y}\middle|z\right.", "mathml", "display")
    assert r"\middle|" in convert(xml, "mathml", "latex", wrapper="bare").text


def test_fixed_label_base_and_unknown_unicode():
    fixed = '<math><mover><mo stretchy="false">→</mo><mi>n</mi></mover></math>'
    assert r"\overset{n}" in convert(fixed, "mathml", "latex").text
    for fmt in ("mathml", "omml"):
        result = convert(office_xml("x⚓y", fmt, "inline"), fmt, "latex", wrapper="bare")
        assert "⚓" in result.text and any("pdfLaTeX may reject" in n for n in result.notes)
    assert tidy.tidy_tex(r"\text{✓}", True) == r"\text{✓}"
