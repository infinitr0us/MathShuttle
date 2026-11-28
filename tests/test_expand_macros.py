"""Writing an equation for a document that does not have the author's macros."""
from __future__ import annotations

import pytest

from mathshuttle import compat
from mathshuttle.__main__ import main
from mathshuttle.convert import convert, convert_equation, convert_equations
from mathshuttle.documents import read_document
from mathshuttle.pandoc_engine import Equation


@pytest.mark.parametrize("definitions, body, expected", [
    (r"\newcommand{\R}{\mathbb{R}}", r"x\in\R", r"x\in\mathbb{R}"),
    (r"\newcommand{\pair}[2]{(#1,#2)}", r"\pair{x}{y}", "(x,y)"),
    (r"\newcommand{\power}[2][2]{#2^{#1}}", r"\power{x}+\power[3]{y}", "x^{2}+y^{3}"),
    (r"\def\until#1.{[#1]}", r"\until a.", "[a]"),
    (r"\let\eps=\varepsilon", r"\eps", r"\varepsilon"),
    (r"\DeclareMathOperator{\op}{op}\DeclareMathOperator*{\best}{best}",
     r"\op(x)+\best_x", r"\operatorname{op}(x)+\operatorname*{best}_x"),
    (r"\newcommand{\field}{\mathbb{R}}\newcommand{\spaceof}[1]{\field^{#1}}",
     r"\spaceof{n}", r"\mathbb{R}^{n}"),
    (r"\DeclarePairedDelimiter{\abs}{\lvert}{\rvert}", r"\abs{x}+\abs*{x}+\abs[\big]{x}",
     r"{\lvert {x}\rvert}+{\left\lvert {x}\right\rvert}+{\bigl\lvert {x}\bigr\rvert}"),
    (r"\DeclarePairedDelimiterX{\given}[2]{[}{]}{#1\delimsize\vert#2}", r"\given*{a}{b}",
     r"{\left[ {{a}\middle \vert{b}}\right]}"),
])
def test_definitions(definitions, body, expected):
    out, notes = compat.expand_macros(body, compat.Macros(text=definitions))
    assert out == expected
    assert notes


def test_inline_definitions_win_and_are_removed():
    user = compat.Macros(text=r"\newcommand{\ket}[1]{A(#1)}")
    src = r"\newcommand{\ket}[1]{B(#1)} \ket{\psi}"
    assert convert(src, "latex", "latex", wrapper="bare", macros=user, expand_macros=True).text == r"B(\psi)"


def test_input_definitions_before_delimiters_and_merged_notes():
    src = (r"\newcommand{\sq}[1]{#1^2}\DeclareMathOperator*{\best}{best}" + "\n" +
           r"$\sq{x}$ and $\best_y \sq{y}$")
    result = convert(src, "latex", "latex", expand_macros=True)
    assert result.outputs == ["$x^2$", r"$\operatorname*{best}_y y^2$"]
    notes = [n for n in result.notes if r"\sq" in n or r"\best" in n]
    assert len(notes) == 1 and notes[0].count(r"\sq") == notes[0].count(r"\best") == 1


def test_many_macros_are_counted_once_across_equations():
    macros = compat.Macros(text="\n".join(r"\newcommand{\my%s}{%s}" % (n, n) for n in "abcdef"))
    src = r"$\mya+\myb+\myc$ and $\myc+\myd+\mye+\myf$"
    assert "6 distinct macros written out" in convert(src, "latex", "latex", macros=macros, expand_macros=True).notes


def test_a_macro_in_prose_can_supply_a_whole_equation():
    src = r"\newcommand{\first}{$x$}\newcommand{\nextvar}{y}\first and $\nextvar$"
    assert convert(src, "latex", "latex", expand_macros=True).outputs == ["$x$", "$y$"]


def test_operator_declared_between_equations():
    src = r"$x$ then \DeclareMathOperator*{\best}{best} and $\best_z$"
    assert convert(src, "latex", "latex", expand_macros=True).outputs == ["$x$", r"$\operatorname*{best}_z$"]


def test_input_preamble_uses_the_same_definitions_as_a_macro_file():
    defs = r"\newcommand{\contact}{\text{a@b}}"
    expected = convert(r"$\contact$", "latex", "latex", macros=compat.Macros(text=defs), expand_macros=True)
    assert convert(defs + r" $\contact$", "latex", "latex", expand_macros=True) == expected


def test_ignore_macros_in_markdown_code():
    src = "A code example:\n" + r"`\newcommand{\foo}{wrong}`" + "\n\nUse $\\foo$."
    result = convert(src, "latex", "latex", macros=compat.Macros(text=r"\newcommand{\foo}{right}"), expand_macros=True)
    assert result.outputs == ["$right$"]


def test_only_latex_input_uses_the_option():
    source = '<math xmlns="http://www.w3.org/1998/Math/MathML"><mi>x</mi></math>'
    macros = compat.Macros(text=r"\newcommand{\foo}{wrong}")
    assert convert(source, "mathml", "latex", macros=macros, expand_macros=True) == convert(source, "mathml", "latex")


def test_file_and_document_definitions(tmp_path):
    path = tmp_path / "macros.sty"
    path.write_text(r"\newcommand{\base}{R}\newcommand{\fromfile}[1]{\base^{#1}}", encoding="utf-8")
    user = compat.load_macros([str(path)])
    paper = tmp_path / "paper.tex"
    paper.write_text(r"""\documentclass{article}
\newcommand{\base}{Q}
\DeclarePairedDelimiter{\abs}{|}{|}
\newcommand{\paper}[1]{p(#1)}
\DeclareMathOperator*{\opt}{opt}
\begin{document}
$\fromfile{2}+\abs{x}+\paper{t}+\opt_z$
\end{document}""", encoding="utf-8")
    doc = read_document(str(paper), expand_macros=True)
    equations = [f.equation for f in doc.found]
    results = convert_equations(equations, "latex", "auto", doc.macros(user), expand_macros=True)
    assert len(results) == 1
    assert results[0][0] == r"$Q^{2}+{| {x}|}+p(t)+\operatorname*{opt}_z$"
    assert results[0] == convert_equation(equations[0], "latex", macros=doc.macros(user), expand_macros=True)


def test_document_heading_scope_and_body_operator(tmp_path):
    paper = tmp_path / "scope.tex"
    paper.write_text(r"""\documentclass{article}
\newcommand{\heading}{Methods}
{\newcommand{\local}{bad}}
\begin{document}
\section{\heading}
$x$
\DeclareMathOperator*{\best}{best}
$\best_z+\local$
\end{document}""", encoding="utf-8")
    doc = read_document(str(paper), expand_macros=True)
    assert doc.found[1].where == "Methods"
    assert convert_equation(doc.found[1].equation, "latex", macros=doc.macros(None),
                            expand_macros=True)[0] == r"$\operatorname*{best}_z+\local$"


def test_packs_and_user_override():
    body = r"\ket{\psi}+\pdv{f}{x}"
    out, notes = compat.expand_macros(body)
    assert out == r"\left| {\psi} \right\rangle+\frac{\partial {f}}{\partial {x}}"
    assert any(r"\ket" in n and r"\pdv" in n for n in notes)
    user = compat.Macros(text=r"\newcommand{\ket}[1]{K(#1)}")
    assert compat.expand_macros(body, user)[0] == r"K(\psi)+\frac{\partial {f}}{\partial {x}}"


def test_texmath_rewrites_stay_out_of_latex():
    src = r"""\begin{align}
  \dup{x} &= {\rm x} + \eqref{eq:one} \tag{T}\label{eq:one}\\
  \left\lVert y\right\rVert &= \smash{a} \hspace{10mm} \notag
\end{align}"""
    macros = compat.Macros(text=r"\def\dup#1{#1+#1}", labels={"eq:one": "9"})
    for wrapper in ("auto", "bracket"):
        expected = convert(src.replace(r"\dup{x}", "x+x"), "latex", "latex", wrapper=wrapper)
        got = convert(src, "latex", "latex", wrapper=wrapper, macros=macros, expand_macros=True)
        assert got.text == expected.text
        assert got.equations == expected.equations


def test_without_a_macro_the_source_is_unchanged():
    tex = "  \n" + r"x \% y % \foo and \ket{p} are in a comment" + "\n + {\\rm z}  \n"
    assert compat.expand_macros(tex, compat.Macros(text=r"\newcommand{\foo}[1]{(#1)}")) == (tex, [])


def test_macro_next_to_a_control_word():
    macros = compat.Macros(text=r"\newcommand{\res}{R_{\mathrm{eff}}}")
    assert compat.expand_macros(r"x\le\res", macros)[0] == r"x\le R_{\mathrm{eff}}"


def test_a_macro_is_one_fraction_argument():
    macros = compat.Macros(text=r"\newcommand{\eb}{\bar\epsilon}")
    assert compat.expand_macros(r"\frac1\eb", macros)[0] == r"\frac1{\bar\epsilon}"


def test_pack_expansion_does_not_introduce_another_package_command():
    tex = r"\slashed\partial+\SI{12}{kHz}+\si{\kilo\hertz}+\numlist{1;2;3}"
    out, notes = compat.expand_macros(tex)
    assert r"\not" in out and r"12\,\mathrm{kHz}" in out and r"\text{ and }" in out
    assert all(cmd not in out for cmd in (r"\slashed", r"\cancel", r"\SI", r"\si", r"\kilo", r"\hertz", r"\num"))
    assert notes


def test_pandoc_expansion_preserves_the_environment_layout():
    macros = compat.Macros(text=r"\newcommand{\double}[1]{#1+#1}")
    tex = r"\begin{align}\double{x}&=y\end{align}"
    assert compat.expand_macros(tex, macros)[0] == tex.replace(r"\double{x}", "x+x")


def test_failure_keeps_the_whole_equation(monkeypatch):
    monkeypatch.setattr(compat, "_expand", lambda *args: None)
    macros = compat.Macros(text=r"\def\dup#1{#1+#1}\DeclarePairedDelimiter\abs{|}{|}")
    eq = Equation(r"\abs{x}+\dup{y}+\ket{z}", True)
    result, notes = convert_equation(eq, "latex", "bare", macros, expand_macros=True)
    assert result == eq.tex
    assert any("could not be expanded" in n for n in notes)


def test_pandoc_refuses_a_recursive_definition():
    src = r"\newcommand{\myloop}{\myloop} $\myloop+x$ and $y$"
    result = convert(src, "latex", "latex", expand_macros=True)
    assert result.outputs == [r"$\myloop+x$", "$y$"]
    assert any("could not be expanded" in n for n in result.notes)


def test_cli_convert_extract_and_ignored_targets(tmp_path, capsys):
    paper = tmp_path / "paper.tex"
    macros = tmp_path / "macros.tex"
    paper.write_text(r"$\myvalue$ and $\ket{x}$", encoding="utf-8")
    macros.write_text(r"\newcommand{\myvalue}{42}", encoding="utf-8")
    for cmd in ("convert", "extract"):
        args = [cmd, str(paper), "--macros", str(macros)]
        assert main(args + ["-t", "latex"]) == 0
        assert r"\myvalue" in capsys.readouterr().out
        assert main(args + ["-t", "latex", "--expand-macros"]) == 0
        out = capsys.readouterr().out
        assert "$42$" in out and r"\right\rangle" in out
        assert main(args + ["-t", "mathml"]) == 0
        expected = capsys.readouterr()
        assert main(args + ["-t", "mathml", "--expand-macros"]) == 0
        assert capsys.readouterr() == expected


@pytest.mark.parametrize("tex, defs, expected, notes", [
    (r"{\rm x} + \tbinom{n}{k} + \smash{y}", "", r"{\mathrm{x}} + \binom{n}{k} + {y}",
     [r"\tbinom: written as the plain-size version", r"\smash: layout-only commands were left out"]),
    (r" \R + \ket{x} ", r"\newcommand{\R}{\mathbb{R}}", r"\mathbb{R}+ \left| {x} \right\rangle",
     [r"written as their packages define them (built in): \ket (physics)"]),
    (r"\abs*{u}+\bigl\lVert x \bigr\rVert", r"\DeclarePairedDelimiter\abs{\lvert}{\rvert}",
     r"{\left\lvert {u}\right\rvert}+\bigl\Vert x \bigr\Vert", []),
    (r"\dup{a} + \eqref{k}", r"\def\dup#1{#1+#1}", r"a+a + \text{(4)}", []),
])
def test_existing_texmath_output(tex, defs, expected, notes):
    assert compat.for_texmath(tex, compat.Macros(text=defs, labels={"k": "4"})) == (expected, notes)
