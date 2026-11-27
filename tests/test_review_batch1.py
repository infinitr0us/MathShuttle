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
