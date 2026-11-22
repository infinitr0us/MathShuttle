"""LaTeX that texmath does not take, rewritten for it (compat.py), and the user's macro files."""
import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from mathshuttle import compat  # noqa: E402
from mathshuttle.convert import convert, office_copy  # noqa: E402


def rewrite(tex, macros=None):
    return compat.for_texmath(tex, macros)[0]


@pytest.mark.parametrize("tex, expected", [
    # plain-TeX font switches: the scope is the rest of the group (thesis: P_{\rm{OOD}}, {\rm d}x)
    (r"P_{\rm{OOD}}^{(t)}", r"P_{\mathrm{OOD}}^{(t)}"),
    (r"P_{\rm OOD}", r"P_{\mathrm{OOD}}"),
    (r"{\rm d}x", r"{\mathrm{d}}x"),
    (r"\rm a \bf b", "\\mathrm{a {𝐛}}"),  # \bf b -> \mathbf{b} -> the bold character
    (r"{\cal L} + \cal M", r"{\mathcal{L}} + \mathcal{M}"),
    (r"\left( \rm x \right) y", r"\left( \mathrm{x} \right) y"),
    (r"\begin{aligned} \rm a &= \rm b \\ c &= d \end{aligned}",
     r"\begin{aligned} \mathrm{a} &= \mathrm{b} \\ c &= d \end{aligned}"),
    (r"\rmfamily x", r"\rmfamily x"),  # another control word
    (r"\text{for {\rm all}} x", r"\text{for {all}} x"),  # a text font change: dropped
    (r"\rm", ""),
    # renamed commands and environments
    (r"\begin{align} a &= b \notag \\ c &= d \end{align}", r"\begin{align} a &= b \nonumber \\ c &= d \end{align}"),
    (r"\mathbbm{1}[y=k]", r"\mathbb{1}[y=k]"),
    (r"\Bbb R", r"\mathbb R"),
    (r"\begin{dcases} a & x \\ b & y \end{dcases}", r"\begin{cases} a & x \\ b & y \end{cases}"),
    (r"\begin{rcases} a & x \end{rcases}", r"\left.\begin{array}{ll} a & x \end{array}\right\}"),
    (r"\begin{pmatrix*}[r] 1 & -2 \end{pmatrix*}", r"\begin{pmatrix} 1 & -2 \end{pmatrix}"),
    # layout-only commands
    (r"\smash[b]{x} + y", r"{x} + y"),
    (r"\vphantom{\int} y", r"{} y"),  # an empty atom stays (\sin\vphantom{x}h is no \sinh)
    (r"\left( a \middle| b \right)", r"\left( a \middle| b \right)"),  # texmath reads \middle there
    (r"\textcolor{red}{x} + y", r"{\color{red} {x}} + y"),
    # definitions: expanded by pandoc's LaTeX reader (texmath does not know \def and \let)
    (r"\def\R{\mathbb{R}} x \in \R", r"x \in \mathbb{R}"),
    (r"\def\pair#1#2{(#1, #2)} \pair{a}{b}", r"(a, b)"),
    (r"\let\eps\varepsilon \eps", r"\varepsilon"),
    (r"\def\weird#1.{[#1]} \weird a.", r"[a]"),  # delimited parameters too
    # untouched: comments, text arguments, labels
    (r"x % \rm here" + "\n" + r"+ y", r"x % \rm here" + "\n" + r"+ y"),
    (r"x \label{eq:\notag}", r"x \label{eq:\notag}"),
    # symbols texmath writes as the wrong character
    (r"\|\mathbf{c}\|_2", "\\Vert{𝐜}\\Vert_2"),
    (r"a \\ b \| c", r"a \\ b \Vert c"),  # a line break is not a norm bar
    (r"\max_{j}\|g(x)\|_2", r"\max_{j}\Vert g(x)\Vert_2"),  # thesis: \|g must not become \Vertg
    (r"A \setminus B = \varnothing", r"A \smallsetminus B = \emptyset"),
    # bold upright letters as bold characters (texmath writes \mathbf{x} bold italic)
    (r"\mathbf{x}^\top \mathbf{W}_{i} + \mathbf{0}", "{𝐱}^\\top {𝐖}_{i} + {𝟎}"),
    (r"\bf x", "\\mathbf{x}".replace("\\mathbf{x}", "{𝐱}")),
    (r"\mathbf{\hat{x}} + \mathbf{x_i}", r"\mathbf{\hat{x}} + \mathbf{x_i}"),  # not plain letters: as written
    (r"\text{\mathbf{x}}", r"\text{\mathbf{x}}"),
])
def test_rewrites(tex, expected):
    assert rewrite(tex) == expected


def test_bold_characters_only_for_mathml_and_omml():
    assert compat.for_texmath(r"\mathbf{x}", target="typst")[0] == r"\mathbf{x}"
    assert compat.for_texmath(r"\mathbf{x}", target="omml")[0] == "{𝐱}"


def test_bold_letters_are_upright_in_office():
    mathml = convert(r"\mathbf{x} + \boldsymbol{\theta} + \bm{w}", "latex", "mathml").text
    assert "<mi>𝐱</mi>" in mathml  # what Word itself exports for \mathbf{x}
    assert mathml.count('mathvariant="bold-italic"') == 2
    assert convert(mathml, "mathml", "latex", wrapper="bare").text == r"\mathbf{x} + \boldsymbol{\theta} + \boldsymbol{w}"


def test_lossy_rewrites_are_noted():
    _, notes = compat.for_texmath(r"\textcolor{red}{x} + \tbinom{n}{k} + \smash{y}")
    assert any("colour" in n for n in notes)
    assert any("\\tbinom" in n for n in notes)
    assert any("\\smash" in n for n in notes)
    assert compat.for_texmath(r"{\rm OOD} \notag")[1] == []  # exact equivalents: no note


@pytest.mark.parametrize("tex", [
    r"P_{\rm{OOD}}^{(t)} \in [0, 0.5)",
    r"\mathcal{L}_{{x}_{\rm neg}} = - \mathcal{D}_{\rm KL}",
    r"\begin{align} a &= b \notag \\ c &= d \end{align}",
    r"\def\R{\mathbb{R}} x \in \R^n",
    r"\let\eps\varepsilon \eps > 0",
    r"\mathbbm{1}[y = k] + \tbinom{n}{k}",
    r"\begin{dcases} 1 & x > 0 \\ 0 & \text{otherwise} \end{dcases}",
    r"\left( \frac{a}{b} \middle| c \right) + \smash{x} + \vphantom{y} z",
    r"\textcolor{red}{x} + \text{for {\rm all} } n",
])
def test_converts_after_rewriting(tex):
    """Each of these is rejected by texmath as written."""
    for dst in ("mathml", "omml", "typst"):
        assert convert(tex, "latex", dst).text


def test_rm_is_upright_in_mathml():
    out = convert(r"P_{\rm OOD}", "latex", "mathml").text
    assert out.count('mathvariant="normal"') == 3


def test_latex_output_keeps_the_source():
    tex = "\\[ P_{\\rm{OOD}} \\notag \\]"
    assert convert(tex, "latex", "latex").text == tex


MACRO_FILE = r"""
%% math_commands.tex, ICLR style
\def\vx{{\bm{x}}}
\def\R{\mathbb{R}}
\newcommand{\norm}[1]{\left\lVert #1 \right\rVert}
\newcommand{\pnorm}[2][2]{\left\lVert #2 \right\rVert_{#1}}
\DeclareMathOperator*{\argmax}{arg\,max}
\DeclareMathOperator{\sg}{sg}
\let\eps\varepsilon
\DeclarePairedDelimiter{\abs}{\lvert}{\rvert}
\newcommand{\loss}{\mathcal{L}_{\rm CE}}
\def\weird#1.{#1}
\def\broken{x
% \def\commented{x}
\usepackage{amsmath}
"""


@pytest.fixture
def macros(tmp_path):
    path = tmp_path / "math_commands.tex"
    path.write_text(MACRO_FILE, encoding="utf-8")
    return compat.load_macros([str(path)])


def test_definitions_are_read(macros):
    assert sorted(macros.definitions) == ["\\R", "\\abs", "\\argmax", "\\eps", "\\loss", "\\norm", "\\pnorm",
                                          "\\sg", "\\vx", "\\weird"]
    assert macros.skipped == ["\\broken"]  # never closed


def test_only_needed_definitions_are_prepended(macros):
    names = [d.name for d in macros.needed(r"\argmax_{\vx} \norm{\vx}")]
    assert sorted(names) == ["\\argmax", "\\norm", "\\vx"]
    assert macros.needed("x + y") == []


def test_own_definitions_win(macros):
    assert macros.needed(r"\newcommand{\R}{\mathbb{C}} \R") == []


@pytest.mark.parametrize("tex", [
    r"\argmax_{\vx \in \R^n} \norm{\vx} + \eps",
    r"\abs{x} + \loss + \sg(x) + \pnorm[1]{y}",
    r"\[ \loss = 1 \]",
    r"\begin{align} \loss &= \norm{\vx} \notag \\ &\leq \eps \end{align}",
])
def test_macros_are_applied(macros, tex):
    out = convert(tex, "latex", "mathml", macros=macros)
    assert "\\" not in out.text.split("annotation")[0]  # no macro left over as text
    assert any("\\broken" in n for n in out.notes)


def test_macros_in_office_copy(macros):
    copy = office_copy(r"\argmax_{\vx} \norm{\vx}", "latex", macros=macros)
    assert "<math" in copy.mathml and "arg" in copy.mathml
    assert copy.latex == r"\[ \argmax_{\vx} \norm{\vx} \]"  # the plain-text flavour keeps the source


def test_annotation_holds_the_source(macros):
    out = convert(r"\norm{\vx}", "latex", "mathml", annotate=True, macros=macros).text
    assert ">\\norm{\\vx}</annotation>" in out


def test_macro_files_are_reloaded_when_changed(tmp_path):
    path = tmp_path / "m.tex"
    path.write_text(r"\newcommand{\a}{x}", encoding="utf-8")
    first = compat.load_macros([str(path)])
    path.write_text(r"\newcommand{\a}{x}\newcommand{\b}{y}", encoding="utf-8")
    os.utime(path, ns=(1, 10 ** 18))  # a different mtime even on a coarse file system clock
    assert len(compat.load_macros([str(path)])) == 2 and len(first) == 1


def test_missing_macro_file_is_reported(tmp_path):
    macros = compat.load_macros([str(tmp_path / "missing.tex")])
    assert macros.errors and "cannot read" in macros.errors[0]
