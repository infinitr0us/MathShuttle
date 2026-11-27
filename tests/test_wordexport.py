"""A passage or a whole .tex / .md document as a Word file (wordexport): prose kept, native equations,
numbers at the right margin as Word numbers them, references and citations with their numbers, failures
marked.  (Rendering checked with Word 16.0.20527: # in an equation array with m:maxDist.)"""
import os
import re
import sys
import zipfile

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from mathshuttle.wordexport import document_to_word, passage_to_word  # noqa: E402

PAPER = r"""\documentclass{article}
\usepackage{amsmath,mathtools,physics}
\DeclarePairedDelimiter\absv{\lvert}{\rvert}
\begin{document}
\section{Method}
We minimise \eqref{eq:loss}, see \cite{smith} and \cite{doe}.
\begin{equation}
  \label{eq:loss} L = \sum_i \absv{x_i}^2
\end{equation}
\begin{align}
  a &= b \label{eq:a} \\
  c &= d \label{eq:b}
\end{align}
\begin{equation}
  \ket{\psi} = 0 \tag{Q}
\end{equation}
\begin{equation*}
  u = v \tag*{S}
\end{equation*}
Bad: \begin{equation} \bogus{x} = 1 \end{equation}
Unknown in the text: \glsfoo{x}.
\end{document}
"""
AUX = r"""\relax
\bibcite{smith}{7}
\newlabel{eq:loss}{{1}{1}}
\newlabel{eq:a}{{2}{1}}
\newlabel{eq:b}{{3}{1}}
"""


def words(xml: str) -> str:
    import html
    return html.unescape(" ".join(re.sub(r"<[^>]+>", " ", xml).split()))


@pytest.fixture(scope="module")
def paper(tmp_path_factory):
    folder = tmp_path_factory.mktemp("paper")
    (folder / "paper.tex").write_text(PAPER, encoding="utf-8")
    (folder / "paper.aux").write_text(AUX, encoding="utf-8")
    out = folder / "paper.docx"
    notes = document_to_word(str(folder / "paper.tex"), str(out))
    return notes, zipfile.ZipFile(str(out)).read("word/document.xml").decode("utf-8")


def test_numbers_go_where_word_puts_its_own(paper):
    notes, xml = paper
    for shown in ("#(1)", "#(2)", "#(3)", "#(Q)"):
        assert "<m:t>%s</m:t>" % shown in xml
    assert xml.count('<m:maxDist m:val="1"/>') == 3  # eq:loss, the align (one array), the tag
    assert "QQMSN" not in xml
    assert any("4 equation numbers" in n for n in notes)


def test_a_label_at_the_start_puts_the_number_at_the_end(paper):
    _, xml = paper
    loss = re.search(r"<m:oMath>(?:(?!</m:oMath>).)*#\(1\)(?:(?!</m:oMath>).)*</m:oMath>", xml, re.S).group()
    assert loss.rindex("#(1)") > loss.rindex("<m:nary>")  # after the sum, at the end of the row


def test_align_rows_are_numbered_each(paper):
    _, xml = paper
    array = re.search(r"<m:eqArr>(?:(?!</m:eqArr>).)*#\(2\)(?:(?!</m:eqArr>).)*</m:eqArr>", xml, re.S).group()
    assert "#(3)" in array


def test_starred_environments_get_no_number(paper):
    _, xml = paper
    assert "#S" not in xml


def test_references_and_citations_show_their_numbers(paper):
    _, xml = paper
    text = words(xml)
    assert "We minimise (1)" in text and "[7]" in text and "[doe]" in text


def test_paired_delimiters_and_packs_become_native_equations(paper):
    notes, xml = paper
    assert xml.count("<m:oMath>") == 4  # (the failed one is LaTeX text)
    assert any("physics" in n for n in notes)


def test_a_failure_is_marked(paper):
    notes, xml = paper
    text = words(xml)
    assert "[not converted: texmath does not know \\bogus]" in text
    assert any("1 equation could not be converted" in n for n in notes)


def test_a_chat_answer_keeps_its_prose_table_and_code(tmp_path):
    chat = ("Here is the derivation, about 50% of it:\n\n**Step 1.** The energy is $E = mc^2$, so\n\n"
            "$$\\frac{dE}{dm} = c^2.$$\n\n| item | cost |\n|---|---|\n| book | $5 |\n\n"
            "```python\nprint('$x$')\n```\n")
    out = tmp_path / "chat.docx"
    passage_to_word(chat, str(out))
    xml = zipfile.ZipFile(str(out)).read("word/document.xml").decode("utf-8")
    text = words(xml)
    assert xml.count("<m:oMath>") == 2
    assert "Step 1." in text and "$5" in text and "'$x$'" in text and "<w:tbl>" in xml


def test_raw_tex_in_a_markdown_file_becomes_equations(tmp_path):
    md = tmp_path / "notes.md"
    md.write_text("# Notes\n\nWe have\n\n\\begin{align}\na &= b \\\\\nc &= d\n\\end{align}\n\nand $x$.\n",
                  encoding="utf-8")
    out = tmp_path / "notes.docx"
    document_to_word(str(md), str(out))
    xml = zipfile.ZipFile(str(out)).read("word/document.xml").decode("utf-8")
    assert "<m:eqArr>" in xml and xml.count("<m:oMath>") == 2


def test_a_bare_equation_is_one_display_equation(tmp_path):
    out = tmp_path / "bare.docx"
    passage_to_word(r"\frac{a}{b} \tag{7}", str(out))
    xml = zipfile.ZipFile(str(out)).read("word/document.xml").decode("utf-8")
    assert "<m:oMathPara>" in xml and "<m:t>#(7)</m:t>" in xml


def test_the_word_command(tmp_path, capsys):
    from mathshuttle.__main__ import main
    out = tmp_path / "w.docx"
    assert main(["word", "-e", "The area is $\\pi r^2$.", "-o", str(out)]) == 0
    assert out.exists() and "written to" in capsys.readouterr().err


def test_docx_and_pptx_are_not_written_as_whole_documents(tmp_path):
    from mathshuttle.pandoc_engine import ConversionError
    with pytest.raises(ConversionError):
        document_to_word(str(tmp_path / "x.docx"), str(tmp_path / "y.docx"))
