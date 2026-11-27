"""Built-in packs (physics, braket, slashed, tensor, siunitx literal units, AASTeX) and standard LaTeX
that texmath 0.13 rejects (compat._standard): each must convert, and mean what LaTeX shows."""
import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from mathshuttle import compat  # noqa: E402
from mathshuttle.convert import convert  # noqa: E402


def prepared(tex, macros=None):
    return compat.for_texmath(tex, macros, "mathml")


def mathml(tex, macros=None):
    return convert(tex, "latex", "mathml", macros=macros).text


@pytest.mark.parametrize("tex, expected", [
    (r"\ket{\psi}", r"\left| {\psi} \right\rangle"),
    (r"\ket\psi", r"\left| {\psi} \right\rangle"),
    (r"\bra*{\psi}", r"\langle {\psi} |"),
    (r"\braket{\phi}{\psi}", r"\left\langle {\phi} \middle| {\psi} \right\rangle"),
    (r"\braket{\phi|\psi}", r"\left\langle {\phi} \middle| {\psi} \right\rangle"),
    (r"\mel{n}{A}{m}", r"\left\langle {n} \middle| {A} \middle| {m} \right\rangle"),
    (r"\expval{A}{\psi}", r"\left\langle {\psi} \middle| {A} \middle| {\psi} \right\rangle"),
    (r"\dv{f}{x}", r"\frac{\mathrm{d} {f}}{\mathrm{d} {x}}"),
    (r"\dv[2]{f}{x}", r"\frac{\mathrm{d}^{2} {f}}{\mathrm{d} {x}^{2}}"),
    (r"\dv{x}", r"\frac{\mathrm{d}}{\mathrm{d} {x}}"),
    (r"\pdv{f}{x}{y}", r"\frac{\partial^{2} {f}}{\partial {x} \, \partial {y}}"),
    (r"\comm{A}{B}", r"\left[ {{A}, {B}} \right]"),
    (r"\qty(\frac{a}{b})", r"\left( {\frac{a}{b}} \right)"),
    (r"\qty|x|", r"\left| {x} \right|"),
    (r"\abs{x}", r"\left| {x} \right|"),
    (r"\order{h^2}", r"\mathcal{O}\left( {h^2} \right)"),
    (r"\eval{f}_{0}", r"\left. {f} \right|_{0}"),
    (r"\mqty(a & b \\ c & d)", r"\begin{pmatrix} a & b \\ c & d \end{pmatrix}"),
    (r"\Tr\rho", r"\operatorname{Tr}\rho"),
    (r"\Set{x | x > 0}", r"\left\{ {x} \middle| {x > 0} \right\}"),
    (r"\slashed{p}", r"{\not p}"),
    (r"\slashed{\partial}", r"\cancel{\partial}"),
    (r"\tensor{T}{^a_b}", r"{T}{}^{a}{}_{b}"),
    (r"6\farcs15", r"6{.}^{\prime\prime}15"),
])
def test_pack_rewrites_convert(tex, expected):
    out, notes = prepared(tex)
    assert out == expected
    assert any("built in" in n for n in notes)
    assert "<math" in mathml(tex)


def test_siunitx_literal_units_are_upright_and_macro_units_left_to_texmath():
    out, _ = prepared(r"\qty{12}{kHz}")
    assert out == r"\num{12}\,\mathrm{kHz}"
    assert 'mathvariant="normal">k<' in mathml(r"\qty{12}{kHz}")
    assert prepared(r"\qty{3.2}{\per\second}")[0] == r"\qty{3.2}{\per\second}"  # texmath's own siunitx
    assert prepared(r"\si{m/s^2}")[0] == r"\mathrm{m/s^2}"
    assert prepared(r"\numlist{1;2;3}")[0] == r"\num{1}, \num{2}\text{ and }\num{3}"


def test_a_definition_of_the_users_wins_over_a_pack():
    macros = compat.load_macros(text=r"\newcommand{\abs}[1]{\lvert #1\rvert}")
    out, notes = prepared(r"\abs{x}", macros)
    assert "\\left|" not in out and not any("built in" in n for n in notes)


def test_div_is_physics_only_when_the_document_loads_physics():
    assert prepared(r"\div x")[0] == r"\div x"  # the division sign
    macros = compat.load_macros(document="\\usepackage{physics}\n")
    assert prepared(r"\div \vb{E}", macros)[0].startswith(r"\nabla\cdot")


def test_physics_braket_with_one_argument_follows_the_loaded_package():
    physics = compat.load_macros(document="\\usepackage{physics}\n")
    assert prepared(r"\braket{\psi}", physics)[0] == r"\left\langle {\psi} \middle| {\psi} \right\rangle"
    assert prepared(r"\braket{\psi}")[0] == r"\left\langle {\psi} \right\rangle"  # braket's (no physics)


@pytest.mark.parametrize("tex, expected", [
    (r"\hspace{10mm} x", r"\hspace{1cm} x"),
    (r"\hspace*{1ex} x", r"\hspace{0.43em} x"),
    (r"a \kern3pt b \mkern3mu c", r"a \hspace{3pt} b \mspace{3mu} c"),
    (r"\text{a \textbf{b} c}", r"{\text{a }\textbf{b}\text{ c}}"),  # one group, as \text was one argument
    (r"\text{\small a}", r"\text{a}"),
    (r"\textnormal{x}", r"\textrm{x}"),
    (r"\varlimsup_{n} a_n", r"\overline{\lim}_{n} a_n"),
    (r"\operatornamewithlimits{argmin}_x", r"\operatorname*{argmin}_x"),
    (r"\bigm| x", r"\big| x"),
    (r"\cfrac[l]{1}{1+\cfrac{1}{x}}", r"\dfrac{1}{1+\dfrac{1}{x}}"),
    (r"\sideset{}{'}\sum_{n} a_n", r"{}{\underset{n}{\sum}}{}' a_n"),  # the limit stays under the sum
    (r"\widetilde\mathcal K", r"\widetilde{\mathcal{K}}"),
    (r"\begin{aligned}[t] a &= b \end{aligned}", r"\begin{aligned} a &= b \end{aligned}"),
    (r"\addtocounter{equation}{1} x = 1", r"{} x = 1"),
    (r"x = 1 \global\@namedef{lbl@eq:x}{equality}", r"x = 1 {}"),
    (r"\begingroup x \endgroup", r"{ x }"),
    (r"\begin{array}{c} \toprule a \\ \midrule b \end{array}", r"\begin{array}{c} \hline a \\ \hline b \end{array}"),
])
def test_standard_latex_that_texmath_rejects(tex, expected):
    assert prepared(tex)[0] == expected
    assert "<math" in mathml(tex)


def test_aligned_position_option_is_not_shown():
    assert ">t<" not in mathml(r"\begin{aligned}[t] a &= b \\ c &= d \end{aligned}")


def test_intertext_becomes_a_row_of_text_with_a_note():
    out, notes = prepared(r"\begin{align} a &= b \\ \intertext{and so} c &= d \end{align}")
    assert r"\text{and so}\\" in out
    assert any("intertext" in n for n in notes)


def test_references_take_the_numbers_of_the_aux_file():
    macros = compat.load_macros(labels={"eq:x": "2.1"})
    assert prepared(r"a \overset{\eqref{eq:x}}{=} b", macros)[0] == r"a \overset{\text{(2.1)}}{=} b"
    assert prepared(r"\text{by \eqref{eq:x}} a", macros)[0] == r"\text{by (2.1)} a"
    assert prepared(r"a \overset{\eqref{eq:y}}{=} b")[0] == r"a \overset{\text{(eq:y)}}{=} b"  # no number known


def test_conflicting_definitions_take_the_last_one():
    # a \DeclarePairedDelimiter and a \newcommand of one name (two macro files): the later one holds
    for text in (r"\DeclarePairedDelimiter{\abs}{\lvert}{\rvert}" "\n" r"\newcommand{\abs}[1]{\left|#1\right|}",
                 r"\newcommand{\abs}[1]{\left|#1\right|}" "\n" r"\DeclarePairedDelimiter{\abs}{\lvert}{\rvert}"):
        assert "<math" in mathml(r"\abs{y}", compat.load_macros(text=text))


def test_paired_delimiter_x_and_xparse_and_the_alphabet_loop():
    macros = compat.load_macros(text="\n".join([
        r"\DeclarePairedDelimiterX{\inp}[2]{\langle}{\rangle}{#1, #2}",
        r"\NewDocumentCommand{\KL}{O{p} m}{D_{\mathrm{KL}}(#1 \| #2)}",
        r"\foreach \x in {A,B,...,Z}{",
        r"  \expandafter\xdef\csname cal\x\endcsname{\noexpand\mathcal{\x}}",
        r"}"]))
    assert prepared(r"\inp{a}{b}", macros)[0] == r"{\langle {{a}, {b}}\rangle}"
    assert prepared(r"\inp*{a}{b}", macros)[0] == r"{\left\langle {{a}, {b}}\right\rangle}"
    assert prepared(r"\KL[r]{q}", macros)[0] == r"D_{\mathrm{KL}}(r \Vert q)"
    assert prepared(r"\KL{q}", macros)[0] == r"D_{\mathrm{KL}}(p \Vert q)"
    assert prepared(r"\calR + \calZ", macros)[0] == r"\mathcal{R} + \mathcal{Z}"
    assert not macros.skipped
