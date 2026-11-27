"""The Codex review of batch 1 (f09198a..f305f4d): each finding, as it should be now."""
import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from mathshuttle import compat, documents  # noqa: E402
from mathshuttle.convert import convert, convert_equation  # noqa: E402
from mathshuttle.documents import read_document  # noqa: E402
from mathshuttle.pandoc_engine import ConversionError  # noqa: E402

PHYSICS = compat.load_macros(document="\\usepackage{physics}\n")
BRAKET = compat.load_macros(document="\\usepackage{braket}\n")


def shown(tex, macros=None):
    """What the MathML shows, as text."""
    import xml.etree.ElementTree as ET
    return "".join(ET.fromstring(convert(tex, "latex", "mathml", macros=macros).text).itertext()).replace(" ", "").replace("\n", "")


def paper(tmp_path, preamble, body, files=()):
    for name, text in files:
        (tmp_path / name).write_text(text, encoding="utf-8")
    path = tmp_path / "paper.tex"
    path.write_text("\\documentclass{article}\n%s\n\\begin{document}\n%s\n\\end{document}\n" % (preamble, body),
                    encoding="utf-8")
    return str(path)


def read(path):
    doc = read_document(path)
    return [shown_eq(f.equation, doc) for f in doc.found], doc


def shown_eq(eq, doc):
    import xml.etree.ElementTree as ET
    text, _ = convert_equation(eq, "mathml", "auto", doc.macros(None))
    return "".join(ET.fromstring(text).itertext()).replace(" ", "").replace("\n", "")


def test_a_redefinition_in_a_group_does_not_change_other_equations(tmp_path):
    out, _ = read(paper(tmp_path, "", "$a\\div b$ {\\renewcommand{\\div}{\\nabla\\cdot} $\\div F$}"))
    assert out == ["a÷b", "∇⋅F"]


def test_a_package_in_a_verbatim_example_is_not_loaded(tmp_path):
    body = "$a\\div b$\n\\begin{verbatim}\n\\usepackage{physics}\n\\end{verbatim}"
    assert read(paper(tmp_path, "", body))[0] == ["a÷b"]


def test_a_latin1_style_file_does_not_make_the_paper_unreadable(tmp_path):
    (tmp_path / "old.sty").write_bytes("% auteur: Andr\xe9\n\\newcommand{\\foo}{x}\n".encode("latin-1"))
    assert read(paper(tmp_path, "\\usepackage{old}", "$x^2$"))[0] == ["x2"]


def test_the_body_alone_ends_at_the_real_end_of_document(tmp_path, monkeypatch):
    real = documents._run

    def failing(args, stdin, *a, **kw):
        if b"\\documentclass" in stdin:
            raise ConversionError("pandoc failed (exit 64):\nunexpected \\@")
        return real(args, stdin, *a, **kw)
    monkeypatch.setattr(documents, "_run", failing)
    out, doc = read(paper(tmp_path, "", "$a$\n% \\end{document}\n$b$"))
    assert out == ["a", "b"]


def test_xparse_definitions_keep_their_meaning():
    omitted = compat.load_macros(text=r"\NewDocumentCommand{\f}{o m}{\IfNoValueTF{#1}{#2}{#1}}")
    assert shown(r"\f{x}", omitted) == "x" and shown(r"\f[y]{x}", omitted) == "y"
    renewed = compat.load_macros(text=r"\NewDocumentCommand{\g}{m}{x}" "\n" r"\RenewDocumentCommand{\g}{m}{y}")
    assert shown(r"\g{1}", renewed) == "y"


def test_a_definition_above_the_equation_in_the_input_applies():
    # what Open file's Use writes into the window
    assert shown("\\DeclarePairedDelimiter{\\inner}{\\langle}{\\rangle}\n$\\inner{x}$") == "⟨x⟩"


def test_definitions_after_an_input_win(tmp_path):
    files = [("defs.tex", r"\def\myL{(}\def\myR{)}\DeclarePairedDelimiter{\fence}{\myL}{\myR}")]
    out, _ = read(paper(tmp_path, r"\usepackage{mathtools}\input{defs}\def\myL{[}\def\myR{]}", r"$\fence{x}$", files))
    assert out == ["[x]"]


def test_subequations_with_two_equations():
    src = "\\begin{subequations}\\label{grp}\n\\begin{equation}a=b\\end{equation}\n\\begin{equation}c=d\\end{equation}\n\\end{subequations}"
    out = convert(src, "latex", "latex").text
    assert out.startswith("\\begin{subequations}\\label{grp}") and "\\[" not in out
    assert out.count("\\begin{equation}") == 2


def test_page_and_title_references(tmp_path):
    files = [("paper.aux", r"\newlabel{eq:x}{{2.1}{37}{Conservation}{equation.2.1}{}}")]
    out, _ = read(paper(tmp_path, "\\usepackage{hyperref}",
                        "$a=\\text{see page \\pageref{eq:x}, \\nameref{eq:x}}$", files))
    assert out == ["a=seepage37,Conservation"]


def test_sideset_keeps_the_limits_of_the_sum():
    mathml = convert(r"\sideset{}{'}\sum_{i=1}^n x_i", "latex", "mathml").text
    assert "<munder>" in mathml and "<mover>" in mathml and mathml.index("<mi>n</mi>") < mathml.index("′")


def test_a_line_break_inside_a_negative_length():
    assert "<math" in convert("x \\hspace{-\n1mm} y", "latex", "mathml").text


@pytest.mark.parametrize("tex, macros, expected", [
    (r"\bra{a}\ket{b}", PHYSICS, "⟨a|b⟩"),
    (r"\ip{\psi}", PHYSICS, "⟨ψ|ψ⟩"),
    (r"\ev**{A}{\psi}", PHYSICS, "⟨ψ∣A∣ψ⟩"),
    (r"\braket{a|b}{c+d}", BRAKET, "⟨a|b⟩c+d"),
    (r"\ket{0}a+\ket{1}b", None, "|0⟩a+|1⟩b"),
    (r"\qty[per-mode=symbol]{10}{m/s}", None, "10m/s"),
    (r"\qty[per-mode=symbol]{10}{\metre\per\second}", None, "10m/s"),
])
def test_bra_ket_and_quantities(tex, macros, expected):
    assert shown(tex, macros) == expected


def test_va_is_an_arrow_over_bold():
    assert "𝐫" in convert(r"\va{r}", "latex", "mathml", macros=PHYSICS).text


def test_stretchy_bars_around_a_fraction_stay_a_pair():
    mathml = ('<math><mi>a</mi><mo stretchy="true">|</mo><mfrac><mi>y</mi><mi>x</mi></mfrac>'
              '<mo stretchy="true">|</mo><mi>b</mi></math>')
    out = convert(mathml, "mathml", "latex").text
    assert "\\middle|" in out or "\\left|" in out


def test_a_comment_does_not_displace_the_diagnosis():
    with pytest.raises(ConversionError) as e:
        convert("x+\\notknown % \\left(", "latex", "mathml")
    assert str(e.value).startswith("texmath does not know \\notknown")


# ---- the Gemini review of batch 1 ----------------------------------------------------------------------
@pytest.mark.parametrize("text, expected", [
    (r"\newcommand{\foo}{1}" "\n" r"\newcommand{\foo}{2}", "2"),
    (r"\newcommand{\foo}{1}" "\n" r"\DeclareMathOperator{\foo}{bar}", "bar"),
])
def test_a_later_definition_of_one_name_wins(text, expected):
    assert shown(r"\foo", compat.load_macros(text=text)) == expected


def test_a_let_alias_of_a_paired_delimiter():
    macros = compat.load_macros(text=r"\DeclarePairedDelimiter{\abs}{\lvert}{\rvert}" "\n" r"\let\myabs\abs")
    assert shown(r"\myabs{x}", macros) == "|x|"


def test_simple_macros_expand_without_a_pandoc_run(monkeypatch):
    macros = compat.load_macros(text=r"\newcommand{\calR}{\mathcal{R}}" "\n" r"\newcommand{\bb}{\mathbb}")
    monkeypatch.setattr(compat, "_expand", lambda *a: pytest.fail("a pandoc run for simple macros"))
    assert compat.for_texmath(r"\calR + \bb R", macros)[0] == r"\mathcal{R}+ \mathbb R"  # (TeX skips the space)
    monkeypatch.undo()  # right after a command that takes an argument, pandoc decides (as TeX would)
    tight = compat.load_macros(text=r"\newcommand{\RR}{R}" "\n" r"\newcommand{\mass}{m}")
    assert shown(r"\mathbb\RR + \frac\mass{V}", tight) == "ℝ+mV"


def test_the_fast_path_leaves_what_needs_tex_to_pandoc():
    for text, tex, expected in (
            (r"\def\energy{E}\edef\initial{\energy}\def\energy{F}", r"\initial+\energy", "E+F"),
            (r"\newcommand{\pM}{p\xspace}", r"\pM", "p")):
        assert shown(tex, compat.load_macros(text=text)).startswith(expected)


def test_document_hooks_and_requirepackage(tmp_path):
    out, _ = read(paper(tmp_path, r"\usepackage{mathtools}\AtBeginDocument{\renewcommand{\div}{\nabla\cdot}}",
                        r"$\div F$"))
    assert out == ["∇⋅F"]
    (tmp_path / "paper.tex").unlink()
    out, _ = read(paper(tmp_path, r"\RequirePackage{physics}", r"$\div F$"))
    assert out == ["∇⋅F"]


def test_code_in_a_chat_answer_loads_no_package():
    assert shown("Use `\\usepackage{physics}` for that. Then $6\\div 2=3$.") == "6÷2=3"


def test_a_file_input_twice_is_read_twice(tmp_path):
    files = [("sq.tex", r"\DeclarePairedDelimiter{\fence}{[}{]}"), ("pa.tex", r"\DeclarePairedDelimiter{\fence}{(}{)}")]
    out, _ = read(paper(tmp_path, r"\usepackage{mathtools}\input{sq}\input{pa}\input{sq}", r"$\fence{x}$", files))
    assert out == ["[x]"]


def test_preview_packages_follow_the_paper():
    from mathshuttle import packs
    assert packs.packages_for(r"\braket{\psi}", set(), {"braket"}) == ["braket"]
    assert packs.packages_for(r"\newcommand{\abs}[1]{(#1)}\abs{x}", set(), set()) == []


@pytest.mark.parametrize("tex", [r"\text{for \(\textbf{x} = 1\)}", r"x_\text{a \textbf{b}}",
                                 r"a \mkern-3mu b", r"a \mkern 1.5mu b", r"\begin{aligned} a &= b \\global = 1 \end{aligned}"])
def test_text_and_spacing_cases(tex):
    assert "<math" in convert(tex, "latex", "mathml").text


def test_a_subscripted_text_stays_in_the_subscript():
    assert compat.for_texmath(r"x_\text{a \textbf{b}}")[0] == r"x_{\text{a }\textbf{b}}"


def test_a_root_index_in_brackets_keeps_them():
    mathml = '<math><mroot><mi>x</mi><mrow><mo>[</mo><mi>n</mi><mo>]</mo></mrow></mroot></math>'
    out = convert(mathml, "mathml", "latex", wrapper="bare").text
    assert "[[" not in out and "]]" not in out


def test_subequations_numbering_and_display():
    from mathshuttle import latex_wrap
    sub = "\\label{eq:g}\n\\begin{align}\na &= b \\label{eq:1} \\\\\nc &= d \\label{eq:2}\n\\end{align}"
    assert latex_wrap.numbering_conflict(sub, "equation", True)
    assert latex_wrap.output_is_display(sub, "auto", False)


def test_no_self_suggestion_and_an_end_without_begin():
    from mathshuttle import diagnose
    short = diagnose.explain(r"\tensor[^a_b]{T}{^c_d}", r"\tensor[^a_b]{T}{^c_d}",
                             r"unexpected control sequence \tensor", short=True)
    assert short == [r"texmath does not know \tensor"]
    assert "do not pair up" in diagnose._unclosed(r"x \end{align}")


def test_no_equation_hint_for_a_mistyped_windows_path(capsys):
    from mathshuttle.__main__ import main
    assert main(["convert", r"C:\nonexistent\paper.tex"]) == 1
    assert "-e" not in capsys.readouterr().err


def test_pack_notes_are_merged_across_equations():
    prefix = "written as their packages define them (built in): "
    notes = [prefix + r"\ket (physics)", "other", prefix + r"\bra, \ket (physics); \Set (braket)"]
    assert compat.merge_notes(notes) == [prefix + r"\ket, \bra (physics); \Set (braket)", "other"]


def test_a_letter_range_across_cases_is_no_alphabet():
    assert compat._letters("A,...,z") == []


# ---- the final Codex re-check (874d5cb) ----------------------------------------------------------------
def test_a_macro_of_limits_keeps_them_on_the_sum():
    macros = compat.load_macros(text=r"\newcommand{\subk}{_{k=1}^N}" "\n" r"\newcommand{\barsep}{|}")
    assert "<munderover>" in convert(r"\sum\subk E_k", "latex", "mathml", macros=macros).text
    assert "<m:nary>" in convert(r"\sum\subk E_k", "latex", "omml", macros=macros).text
    assert "<math" in convert(r"\left\barsep x \right\barsep", "latex", "mathml", macros=macros).text


def test_a_conditional_hook_is_not_applied(tmp_path):
    files = [("compat.sty", r"\AtBeginDocument{\@ifpackageloaded{physics}{\renewcommand{\div}{\nabla\cdot}}{}}")]
    assert read(paper(tmp_path, r"\usepackage{compat}", r"$6\div 2=3$", files))[0] == ["6÷2=3"]


def test_a_labelled_hook_is_applied(tmp_path):
    out, _ = read(paper(tmp_path, r"\AddToHook{begindocument}[notation]{\renewcommand{\div}{\nabla\cdot}}",
                        r"$\div F$"))
    assert out == ["∇⋅F"]


def test_a_dollar_in_a_comment_does_not_hide_the_preamble():
    text = "% An unescaped $ starts math mode.\n\\DeclarePairedDelimiter{\\fence}{\\langle}{\\rangle}\n$\\fence{x}$ and $\\fence{y}$"
    assert [shown(t) for t in ("x",)] and "<math" in convert(text, "latex", "mathml").text
    assert convert(text, "latex", "mathml").text.count("⟨") == 2


def test_an_indented_code_block_in_a_chat_answer_loads_no_package():
    text = "To get it, about 50% of people write:\n\n    \\usepackage{physics}\n\nThen $6\\div 2=3$ holds."
    assert shown(text) == "6÷2=3"


def test_many_inputs_of_one_header_do_not_use_up_the_files(tmp_path):
    files = [("head.tex", "% header"), ("foot.tex", "% footer")]
    files += [("t%d.tex" % i, "\\input{head}\nrow %d\n\\input{foot}" % i) for i in range(100)]
    files += [("app.tex", r"\DeclarePairedDelimiter{\floorof}{\lfloor}{\rfloor}")]
    body = "".join("\\input{t%d}\n" % i for i in range(100)) + "\\input{app}\n$m=\\floorof{x}$"
    assert read(paper(tmp_path, "\\usepackage{mathtools}", body, files))[0] == ["m=⌊x⌋"]


def test_preview_keeps_the_package_a_renewcommand_needs():
    from mathshuttle import packs
    assert packs.packages_for(r"\renewcommand{\abs}[1]{\lvert #1\rvert}\abs{x}", set(), {"physics"}) == ["physics"]
