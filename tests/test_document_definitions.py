"""A .tex file's own definitions (also from files it \\input s), its .aux label numbers, the body-only
reading when pandoc cannot read the preamble, subequations, the Word export's marks for equations that
stay LaTeX text, and errors in plain words."""
import os
import re
import sys
import zipfile

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from mathshuttle import documents  # noqa: E402
from mathshuttle.convert import convert, convert_equation  # noqa: E402
from mathshuttle.documents import read_document, write_docx  # noqa: E402
from mathshuttle.pandoc_engine import ConversionError  # noqa: E402

PAPER = r"""\documentclass{article}
\usepackage{mathtools}
\DeclarePairedDelimiter\absv{\lvert}{\rvert}
\input{defs}
\begin{document}
\section{Intro}
\begin{equation}
  \absv{x} \le \absv*{\frac{a}{b}} \label{eq:a}
\end{equation}
Then $\nrm{v} = \calR$ by \eqref{eq:a}, and
\begin{equation}
  a \overset{\eqref{eq:a}}{\le} b
\end{equation}
\end{document}
"""
DEFS = r"""\DeclarePairedDelimiter\nrm{\lVert}{\rVert}
\foreach \x in {A,B,...,Z}{\expandafter\xdef\csname cal\x\endcsname{\noexpand\mathcal{\x}}}
"""
AUX = r"""\relax
\newlabel{eq:a}{{1.1}{1}{Intro}{equation.1.1}{}}
\newlabel{eq:a@cref}{{[equation][1][1]1.1}{[1][1][1]1}}
"""


@pytest.fixture
def paper(tmp_path):
    (tmp_path / "defs.tex").write_text(DEFS, encoding="utf-8")
    (tmp_path / "paper.aux").write_text(AUX, encoding="utf-8")
    path = tmp_path / "paper.tex"
    path.write_text(PAPER, encoding="utf-8")
    return str(path)


def test_the_documents_own_definitions_and_labels_apply(paper):
    doc = read_document(paper)
    assert doc.labels == {"eq:a": "1.1"}
    macros = doc.macros(None)
    for found in doc.found:
        text, _ = convert_equation(found.equation, "mathml", "auto", macros)
        assert "<math" in text
    last, _ = convert_equation(doc.found[-1].equation, "mathml", "auto", macros)
    assert "(1.1)" in last  # \eqref with the number LaTeX gave it


def test_without_them_the_paired_delimiter_fails(paper):
    doc = read_document(paper)
    with pytest.raises(ConversionError):
        convert_equation(doc.found[0].equation, "mathml", "auto", None)


def test_the_cli_extract_uses_them(paper, capsys):
    from mathshuttle.__main__ import main
    assert main(["extract", paper, "-t", "mathml"]) == 0
    assert capsys.readouterr().out.count("<math") == 3


def test_office_documents_get_no_document_macros():
    doc = documents.DocumentEquations("x.docx", "office", definitions=r"\newcommand{\a}{b}")
    assert doc.macros(None) is None


def test_the_body_alone_when_pandoc_cannot_read_the_preamble(paper, monkeypatch):
    real = documents._run

    def failing(args, stdin, *a, **kw):
        if b"\\documentclass" in stdin:
            raise ConversionError("pandoc failed (exit 64):\nError at (line 59, column 1): unexpected \\@")
        return real(args, stdin, *a, **kw)
    monkeypatch.setattr(documents, "_run", failing)
    doc = read_document(paper)
    assert len(doc.found) == 3
    assert any("body was read alone" in n for n in doc.notes)
    text, _ = convert_equation(doc.found[0].equation, "mathml", "auto", doc.macros(None))
    assert "<math" in text  # the preamble's \DeclarePairedDelimiter still applies


def test_subequations_come_back_as_latex_that_latex_takes():
    src = "\\begin{subequations}\\label{eq:g}\n\\begin{align}\na &= b \\\\\nc &= d\n\\end{align}\n\\end{subequations}"
    out = convert(src, "latex", "latex").text
    assert out.startswith("\\begin{subequations}\\label{eq:g}") and "\\[" not in out
    bracket = convert(src, "latex", "latex", wrapper="bracket").text
    assert "\\label{eq:g}" not in bracket and "\\begin{align}" not in bracket


def test_the_word_export_marks_what_stays_latex_text(tmp_path):
    path = tmp_path / "t.tex"
    path.write_text("\\documentclass{article}\n\\begin{document}\n\\section{A}\n\\begin{align}\n a &= \\bogus{b} \\\\\n"
                    " c &= d\n\\end{align}\nand $x^2$.\n\\end{document}\n", encoding="utf-8")
    out = tmp_path / "t.docx"
    notes = write_docx(read_document(str(path)), str(out))
    assert any("could not be converted" in n and "reason" in n for n in notes)
    xml = zipfile.ZipFile(str(out)).read("word/document.xml").decode("utf-8")
    words = " ".join(re.sub(r"<[^>]+>", " ", xml).split())
    assert "not converted: texmath does not know \\bogus; it is here as LaTeX text" in words
    assert xml.count("<m:oMath>") + xml.count("<m:oMath ") == 1  # x^2


@pytest.mark.parametrize("tex, words", [
    (r"\alhpa + 1", "Did you mean \\alpha?"),
    (r"\frac{a}{b", "a { is never closed"),
    (r"\sqrt{x}}", "a } closes a group that was never opened"),
    (r"\begin{aligned} \foo &= 1 \\ b &= 2 \end{aligned}", "texmath does not know \\foo."),
    (r"\ce{H2O}", "mhchem"),
    (r"\begin{tikzcd} A \arrow[r] & B \end{tikzcd}", "tikzcd is a diagram"),
])
def test_errors_say_what_is_wrong_first(tex, words):
    with pytest.raises(ConversionError) as e:
        convert(tex, "latex", "mathml")
    first, _, rest = str(e.value).partition("\n\n")
    assert words in first
    assert rest.startswith("texmath could not parse this LaTeX")  # texmath's own message stays


def test_an_error_names_the_equation_of_several():
    with pytest.raises(ConversionError) as e:
        convert("$a$ and $\\bogus b$", "latex", "mathml")
    assert str(e.value).startswith("Equation 2 of 2:")
