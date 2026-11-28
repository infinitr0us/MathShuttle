"""Word export after the Codex and Gemini reviews of batch 2: equation numbers as LaTeX gives them (checked
against a real LaTeX run: fixtures/numbering/latex_counts.tex and the .aux MiKTeX wrote for it), references
and citations as LaTeX prints them, the title and abstract, figures, Markdown passages and raw TeX."""
import os
import re
import shutil
import struct
import sys
import zipfile
import zlib

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from mathshuttle.wordexport import document_to_word, passage_to_word  # noqa: E402

NUMBERING = os.path.join(os.path.dirname(__file__), "fixtures", "numbering")
LATEX_NUMBERS = ["(%d)" % n for n in range(1, 4)] + ["(T)"] + ["(%d)" % n for n in range(4, 14)] + ["(S)"] + \
    ["(%d)" % n for n in range(14, 20)] + ["(X)", "(20)", "(21)"]


def numbers(xml: str):
    return re.findall(r"<m:t>#([^<]*)</m:t>", xml)


def words(xml: str) -> str:
    import html
    return html.unescape(" ".join(re.sub(r"<[^>]+>", " ", xml).split()))


class Docx(dict):
    """The parts of a written .docx, read at once (the file is not left open: it is written again)."""

    def read(self, name: str) -> bytes:
        return self[name]

    def namelist(self):
        return list(self)


def export(tmp_path, tex, aux=None, name="paper", files=None):
    (tmp_path / (name + ".tex")).write_text(tex, encoding="utf-8")
    aux_path = tmp_path / (name + ".aux")
    if aux is not None:
        aux_path.write_text(aux, encoding="utf-8")
    elif aux_path.exists():
        aux_path.unlink()
    for file, data in (files or {}).items():
        (tmp_path / file).write_bytes(data) if isinstance(data, bytes) else (tmp_path / file).write_text(data)
    out = tmp_path / (name + ".docx")
    notes = document_to_word(str(tmp_path / (name + ".tex")), str(out))
    with zipfile.ZipFile(str(out)) as z:
        return notes, Docx((n, z.read(n)) for n in z.namelist())


@pytest.mark.parametrize("with_aux", [True, False])
def test_numbers_are_those_latex_gives(tmp_path, with_aux):
    # equation, \nonumber, align rows with \nonumber and \tag, a trailing \\ (LaTeX numbers its empty row),
    # gather, multline, eqnarray, split, align* with \tag, flalign, alignat, cases, \tag in an equation
    shutil.copy(os.path.join(NUMBERING, "latex_counts.tex"), str(tmp_path / "p.tex"))
    if with_aux:
        shutil.copy(os.path.join(NUMBERING, "latex_counts.aux"), str(tmp_path / "p.aux"))
    notes = document_to_word(str(tmp_path / "p.tex"), str(tmp_path / "p.docx"))
    xml = zipfile.ZipFile(str(tmp_path / "p.docx")).read("word/document.xml").decode("utf-8")
    assert numbers(xml) == LATEX_NUMBERS and "QQMSN" not in xml
    assert any("in order" in n for n in notes) != with_aux  # counted unchecked: said so


def test_references_to_counted_equations_without_an_aux(tmp_path):
    _, z = export(tmp_path, "\\documentclass{article}\\begin{document}\nSee \\eqref{b}.\n"
                            "\\begin{equation} a \\end{equation}\n\\begin{equation} b \\label{b} \\end{equation}\n"
                            "\\end{document}\n")
    xml = z.read("word/document.xml").decode("utf-8")
    assert "See (2)." in words(xml) and numbers(xml) == ["(1)", "(2)"]


def test_an_aux_that_disagrees_leaves_only_labelled_numbers(tmp_path):
    notes, z = export(tmp_path, "\\documentclass{article}\\begin{document}\n"
                                "\\begin{equation} a \\end{equation}\n\\begin{equation} b \\label{b} \\end{equation}\n"
                                "\\end{document}\n", aux="\\newlabel{b}{{5}{1}}\n")
    assert numbers(z.read("word/document.xml").decode("utf-8")) == ["(5)"]
    assert any("Only labelled equations" in n for n in notes)


def test_chapter_numbering_uses_the_aux_only(tmp_path):
    tex = ("\\documentclass{report}\\begin{document}\\chapter{One}\n\\begin{equation} a \\label{a} \\end{equation}\n"
           "\\begin{equation} b \\end{equation}\n\\end{document}\n")
    notes, z = export(tmp_path, tex)
    assert numbers(z.read("word/document.xml").decode("utf-8")) == [] and any("chapter" in n for n in notes)
    notes, z = export(tmp_path, tex, aux="\\newlabel{a}{{1.1}{1}}\n")
    assert numbers(z.read("word/document.xml").decode("utf-8")) == ["(1.1)"]
    assert any("without a \\label have no number (1)" in n for n in notes)


def test_subequations_stay_separate_with_their_numbers(tmp_path):
    _, z = export(tmp_path, "\\documentclass{article}\\usepackage{amsmath}\\begin{document}\n"
                            "\\begin{subequations}\\label{g}\n\\begin{equation} x = 1 \\label{s1} \\end{equation}\n"
                            "\\begin{equation} y = 2 \\label{s2} \\end{equation}\n\\end{subequations}\n"
                            "Group \\eqref{g}.\\end{document}\n",
                  aux="\\newlabel{g}{{1}{1}}\n\\newlabel{s1}{{1a}{1}}\n\\newlabel{s2}{{1b}{1}}\n")
    xml = z.read("word/document.xml").decode("utf-8")
    assert numbers(xml) == ["(1a)", "(1b)"] and xml.count("<m:oMath>") == 2 and "Group (1)." in words(xml)


def test_a_commented_tag_or_label_is_no_number(tmp_path):
    _, z = export(tmp_path, "\\documentclass{article}\\begin{document}\n\\begin{equation} y = 1 % \\tag{comment}\n"
                            "+ 2 \\label{y} \\end{equation}\n\\begin{equation} z % \\nonumber\n\\end{equation}\n"
                            "See \\eqref{y}.\\end{document}\n")
    xml = z.read("word/document.xml").decode("utf-8")
    assert numbers(xml) == ["(1)", "(2)"] and "See (1)." in words(xml)


def test_tags_with_maths_or_text_print_as_latex_prints_them(tmp_path):
    _, z = export(tmp_path, "\\documentclass{article}\\usepackage{amsmath}\\begin{document}\n"
                            "\\begin{equation} a \\tag{$\\dagger$} \\end{equation}\n"
                            "\\begin{equation} b \\tag{\\text{Max}} \\end{equation}\n"
                            "\\begin{align*} c &= d \\tag{A} \\\\ e &= f \\tag{B} \\end{align*}\n\\end{document}\n")
    xml = z.read("word/document.xml").decode("utf-8")
    assert numbers(xml) == ["(\u2020)", "(Max)", "(A)", "(B)"] and "not converted" not in words(xml)


def test_gather_and_eqnarray_rows_get_their_numbers(tmp_path):
    _, z = export(tmp_path, "\\documentclass{article}\\usepackage{amsmath}\\begin{document}\n"
                            "\\begin{gather} p \\\\ q \\end{gather}\n"
                            "\\begin{eqnarray} r &=& s \\\\ t &=& u \\end{eqnarray}\n\\end{document}\n")
    xml = z.read("word/document.xml").decode("utf-8")
    assert numbers(xml) == ["(1)", "(2)", "(3)", "(4)"] and xml.count("<m:eqArr>") == 2


def test_equations_in_footnotes_are_numbered_too(tmp_path):
    _, z = export(tmp_path, "\\documentclass{article}\\begin{document}\nText\\footnote{Where "
                            "\\[ f = 1 \\tag{F} \\]}.\\end{document}\n")
    notes = z.read("word/footnotes.xml").decode("utf-8")
    assert "<m:t>#(F)</m:t>" in notes and "QQMSN" not in notes


def test_cref_and_autoref_print_their_names(tmp_path):
    aux = ("\\newlabel{eq:y}{{7}{1}{}{equation.7}{}}\n\\newlabel{sec:intro}{{1}{1}{Intro}{section.1}{}}\n"
           "\\newlabel{fig:a}{{2}{1}{}{figure.2}{}}\n")
    body = "\\begin{document}\nA \\cref{eq:y}, B \\Cref{sec:intro}, C \\cref{fig:a}.\n\\end{document}\n"
    _, z = export(tmp_path, "\\documentclass{article}\\usepackage{cleveref}" + body, aux=aux)
    assert "A eq. (7), B Section 1, C fig. 2." in words(z.read("word/document.xml").decode("utf-8"))
    _, z = export(tmp_path, "\\documentclass{article}\\usepackage{hyperref}" + body.replace("\\cref", "\\autoref")
                  .replace("\\Cref", "\\autoref"), aux=aux)
    assert "A Equation 7, B section 1, C Figure 2." in words(z.read("word/document.xml").decode("utf-8"))


def test_natbib_citations_numeric_and_author_year(tmp_path):
    aux = ("\\bibcite{smith}{{7}{2020}{{Smith et~al.}}{{Smith, Jones, and Lee}}}\n"
           "\\bibcite{jones}{{8}{2019}{{Jones}}{{Jones}}}\n")
    body = ("\\begin{document}\nA \\citep[e.g.][p. 5]{smith,jones}. B \\citet{smith}. C \\cite{jones}.\n"
            "\\[ x \\text{ by \\cite{jones}} \\]\n\\end{document}\n")
    _, z = export(tmp_path, "\\documentclass{article}\\usepackage[numbers]{natbib}" + body, aux=aux)
    text = words(z.read("word/document.xml").decode("utf-8"))
    assert "A [e.g. 7, 8, p. 5]." in text and "C [8]." in text
    _, z = export(tmp_path, "\\documentclass{article}\\usepackage{natbib}" + body, aux=aux)
    text = words(z.read("word/document.xml").decode("utf-8"))
    assert "A (e.g. Smith et al., 2020; Jones, 2019, p. 5)." in text
    assert "B Smith et al. (2020)." in text and "C Jones (2019)." in text


def test_a_markdown_citation_nobody_numbers_stays_as_written(tmp_path):
    md = tmp_path / "notes.md"
    md.write_text("Ask @alice about $x = 1$ [see @book, p. 9].\n", encoding="utf-8")
    document_to_word(str(md), str(tmp_path / "notes.docx"))
    text = words(zipfile.ZipFile(str(tmp_path / "notes.docx")).read("word/document.xml").decode("utf-8"))
    assert "Ask @alice about" in text and "[see @book, p. 9]" in text


def test_title_authors_and_abstract_are_kept(tmp_path):
    _, z = export(tmp_path, "\\documentclass{article}\\title{A Paper}\\author{A. Author}\\date{2026}\n"
                            "\\begin{document}\\maketitle\n\\begin{abstract}We show $u = 1$.\\end{abstract}\n"
                            "\\section{Intro}Text.\\end{document}\n")
    xml = z.read("word/document.xml").decode("utf-8")
    text = words(xml)
    assert "A Paper" in text and "A. Author" in text and "We show" in text
    assert xml.index("A Paper") < xml.index("Intro") and xml.count("<m:oMath>") == 1


def _png() -> bytes:
    raw = b"\x00\xff\x00\x00"
    chunk = lambda kind, data: struct.pack(">I", len(data)) + kind + data + struct.pack(  # noqa: E731
        ">I", zlib.crc32(kind + data) & 0xFFFFFFFF)
    return (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", 1, 1, 8, 2, 0, 0, 0))
            + chunk(b"IDAT", zlib.compress(raw)) + chunk(b"IEND", b""))


def test_figures_next_to_the_paper_are_found_from_anywhere(tmp_path, monkeypatch):
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    monkeypatch.chdir(str(elsewhere))
    paper = tmp_path / "paper"
    paper.mkdir()
    _, z = export(paper, "\\documentclass{article}\\usepackage{graphicx}\\begin{document}\n"
                         "\\includegraphics{fig.png}\n\\end{document}\n", files={"fig.png": _png()})
    assert any(n.startswith("word/media/") for n in z.namelist())


def test_a_markdown_passage_keeps_its_formatting(tmp_path):
    out = tmp_path / "chat.docx"
    passage_to_word("# Derivation\n\n**Step 1.** We have $E = mc^2$.\n\n- First point\n- Second point\n\n"
                    "| a | b |\n|---|---|\n| 1 | 2 |\n", str(out))
    xml = zipfile.ZipFile(str(out)).read("word/document.xml").decode("utf-8")
    assert "Heading1" in xml and "<w:b" in xml and "<w:tbl>" in xml and "<w:numPr>" in xml
    assert "**" not in xml and "# Derivation" not in words(xml) and xml.count("<m:oMath>") == 1


@pytest.mark.parametrize("text", ["We observed two samples.", "# Results\n\nNo maths here."])
def test_a_passage_without_maths_is_text(tmp_path, text):
    out = tmp_path / "t.docx"
    passage_to_word(text, str(out))
    xml = zipfile.ZipFile(str(out)).read("word/document.xml").decode("utf-8")
    assert "<m:oMath" not in xml and ("samples" in words(xml) or "No maths here." in words(xml))


def test_raw_tex_in_markdown(tmp_path):
    (tmp_path / "defs.tex").write_text("\\newcommand{\\R}{\\mathbb{R}}\n", encoding="utf-8")
    md = tmp_path / "notes.md"
    md.write_text("\\input{defs}\n\nWe have\n\n\\begin{aligned}\na &= b \\\\\nc &= \\R\n\\end{aligned}\n\n"
                  "```{=latex}\n\\newcommand{\\V}{\\mathbf{v}}\n\\begin{equation} \\V \\end{equation}\n```\n\n"
                  "```{=latex}\n\\begin{equation} y \\in \\R \\end{equation}\n```\n\n"
                  "> \\begin{equation} q = \\V \\end{equation}\n", encoding="utf-8")
    notes = document_to_word(str(md), str(tmp_path / "notes.docx"))
    xml = zipfile.ZipFile(str(tmp_path / "notes.docx")).read("word/document.xml").decode("utf-8")
    assert xml.count("<m:oMath>") == 4, notes  # the aligned block, \V, y, and the one in the quote
    assert "not converted" not in words(xml) and 'm:val="double-struck"' in xml  # ℝ from the \input file's \R


def test_plain_words_for_failures_after_the_fiftieth_equation(tmp_path):
    body = "".join("\\[ x_{%d} \\]\n" % i for i in range(60)) + "\\[ \\bogus{x} \\]\n"
    _, z = export(tmp_path, "\\documentclass{article}\\begin{document}\n" + body + "\\end{document}\n")
    assert "texmath does not know \\bogus" in words(z.read("word/document.xml").decode("utf-8"))


def test_a_latin_1_paper(tmp_path):
    (tmp_path / "old.tex").write_bytes("\\documentclass{article}\\begin{document}Caf\u00e9 $x$.\\end{document}\n"
                                       .encode("latin-1"))
    document_to_word(str(tmp_path / "old.tex"), str(tmp_path / "old.docx"))
    assert "Caf\u00e9" in words(zipfile.ZipFile(str(tmp_path / "old.docx")).read("word/document.xml").decode("utf-8"))


# ---- the realistic re-check -----------------------------------------------------------------------------
ARTICLE = "\\documentclass{article}\\usepackage{amsmath}%s\\begin{document}\n%s\n\\end{document}\n"


def test_a_matrix_in_a_numbered_gather_keeps_both_rows(tmp_path):
    _, z = export(tmp_path, ARTICLE % ("", "\\begin{gather} A = \\begin{pmatrix} 1 & 0 \\\\ 0 & 1 \\end{pmatrix} \\\\ "
                                           "B = I \\end{gather}\n\\begin{gather} C = \\begin{pmatrix} a \\\\ b "
                                           "\\end{pmatrix} \\\\ D \\notag \\end{gather}"))
    xml = z.read("word/document.xml").decode("utf-8")
    assert numbers(xml) == ["(1)", "(2)", "(3)"]
    second = xml[xml.index("#(3)") - 3000:xml.index("#(3)")]
    assert second.count("<m:mr>") == 2  # (3) on the row with the 2x1 matrix, not around both rows


def test_a_tag_with_maths_and_an_aux_keeps_the_count(tmp_path):
    _, z = export(tmp_path, ARTICLE % ("", "\\begin{equation} a \\label{a} \\end{equation}\n"
                                           "\\begin{equation} b \\tag{$\\dagger$} \\label{star} \\end{equation}\n"
                                           "\\begin{equation} c \\end{equation}\nSee \\eqref{star}."),
                  aux="\\newlabel{a}{{1}{1}}\n\\newlabel{star}{{{$\\dagger $}}{1}}\n")
    xml = z.read("word/document.xml").decode("utf-8")
    assert numbers(xml) == ["(1)", "(\u2020)", "(2)"] and "See (\u2020)." in words(xml)


def test_a_reference_to_the_only_tag(tmp_path):
    _, z = export(tmp_path, ARTICLE % ("", "\\[ a \\tag{A} \\label{a} \\]\nSee \\eqref{a}."))
    assert "See (A)." in words(z.read("word/document.xml").decode("utf-8"))


def test_a_theorem_counter_shared_with_equations_is_no_plain_count(tmp_path):
    notes, z = export(tmp_path, ARTICLE % ("\\usepackage{amsthm}\\newtheorem{theorem}[equation]{Theorem}",
                                           "\\begin{equation} a \\label{a} \\end{equation}\n\\begin{theorem}T"
                                           "\\end{theorem}\n\\begin{equation} b \\end{equation}"),
                      aux="\\newlabel{a}{{1}{1}}\n")
    assert numbers(z.read("word/document.xml").decode("utf-8")) == ["(1)"]  # b is (3) in LaTeX: not guessed


def test_a_macro_that_steps_the_counter_counts_only_when_used(tmp_path):
    helper = "\\newcommand\\numberthis{\\addtocounter{equation}{1}\\tag{\\theequation}}"
    _, z = export(tmp_path, ARTICLE % (helper, "\\begin{equation} a \\end{equation}\n\\begin{equation} b \\end{equation}"))
    assert numbers(z.read("word/document.xml").decode("utf-8")) == ["(1)", "(2)"]
    notes, z = export(tmp_path, ARTICLE % (helper, "\\begin{equation} a \\end{equation}\n"
                                                   "\\begin{align*} b \\numberthis \\end{align*}"))
    assert any("No equation numbers" in n for n in notes)


def test_figures_in_a_graphicspath_folder_without_extension(tmp_path):
    (tmp_path / "figs").mkdir()
    (tmp_path / "figs" / "sample.png").write_bytes(_png())
    (tmp_path / "plot.png").write_bytes(_png())
    _, z = export(tmp_path, ARTICLE % ("\\usepackage{graphicx}\\graphicspath{{figs/}}",
                                       "\\includegraphics{sample}\n\n\\includegraphics{plot.pdf}"))
    assert len([n for n in z.namelist() if n.startswith("word/media/")]) == 2  # (a .png beside the .pdf)


def test_citations_and_references_in_the_abstract(tmp_path):
    _, z = export(tmp_path, ARTICLE % ("", "\\begin{abstract}We extend \\cite{smith}. See \\eqref{e}.\\end{abstract}\n"
                                           "\\begin{equation} x \\label{e} \\end{equation}"),
                  aux="\\bibcite{smith}{7}\n\\newlabel{e}{{1}{1}}\n")
    assert "We extend [7]. See (1)." in words(z.read("word/document.xml").decode("utf-8"))


def test_numeric_citet_and_an_accented_author(tmp_path):
    aux = ("\\bibcite{smith}{{7}{2020}{{Smith et~al.}}{{Smith}}}\n"
           "\\bibcite{groetschel}{{8}{1988}{{Gr{\\\"o}tschel et~al.}}{{Gr{\\\"o}tschel, Lov{\\'a}sz}}}\n")
    _, z = export(tmp_path, ARTICLE % ("\\usepackage[numbers]{natbib}",
                                       "Following \\citet{smith}, see \\citep{groetschel}."), aux=aux)
    assert "Following Smith et al. [7], see [8]." in words(z.read("word/document.xml").decode("utf-8"))
    _, z = export(tmp_path, ARTICLE % ("\\usepackage{natbib}", "See \\citep{groetschel}."), aux=aux)
    assert "See (Gr\u00f6tschel et al., 1988)." in words(z.read("word/document.xml").decode("utf-8"))


def test_markdown_maths_uses_an_input_file_s_macros(tmp_path):
    (tmp_path / "defs.tex").write_text("\\newcommand{\\Domain}{\\mathcal{D}}\n", encoding="utf-8")
    md = tmp_path / "notes.md"
    md.write_text("\\input{defs}\n\nInline $x \\in \\Domain$ and\n\n$$\\Domain^2$$\n", encoding="utf-8")
    document_to_word(str(md), str(tmp_path / "notes.docx"))
    xml = zipfile.ZipFile(str(tmp_path / "notes.docx")).read("word/document.xml").decode("utf-8")
    assert xml.count("<m:oMath>") == 2 and "not converted" not in words(xml)


@pytest.mark.parametrize("text, maths", [("# Usage\n\nCall `train_model` to fit.", 0),
                                         ("\\section{Results}\nWe observed two samples.", 0),
                                         ("AB + CD", 1), ("sin x cos y", 1)])
def test_passages_are_read_as_what_they_are(tmp_path, text, maths):
    out = tmp_path / "p.docx"
    passage_to_word(text, str(out))
    xml = zipfile.ZipFile(str(out)).read("word/document.xml").decode("utf-8")
    assert xml.count("<m:oMath>") == maths and "not converted" not in words(xml)


def test_numberthis_takes_its_number_from_the_aux(tmp_path):
    helper = "\\newcommand\\numberthis{\\addtocounter{equation}{1}\\tag{\\theequation}}"
    _, z = export(tmp_path, ARTICLE % (helper, "\\begin{align*} a &= b \\numberthis \\label{nt} \\\\ c &= d "
                                               "\\numberthis \\end{align*}\nSee \\eqref{nt}."),
                  aux="\\newlabel{nt}{{4}{1}}\n")
    xml = z.read("word/document.xml").decode("utf-8")
    assert numbers(xml) == ["(4)"] and "theequation" not in words(xml) and "See (4)." in words(xml)


def test_natbib_numbers_passed_as_an_option(tmp_path):
    _, z = export(tmp_path, ARTICLE % ("\\PassOptionsToPackage{numbers}{natbib}\\usepackage{natbib}",
                                       "See \\citep{smith}."), aux="\\bibcite{smith}{{7}{2020}{{Smith}}{{Smith}}}\n")
    assert "See [7]." in words(z.read("word/document.xml").decode("utf-8"))


def test_an_align_with_a_line_starting_with_minus_is_latex(tmp_path):
    out = tmp_path / "p.docx"
    passage_to_word("In Theorem~1:\n\\begin{align}\n  f(x) &= a + b \\\\\n       - c + d\n\\end{align}\n"
                    "Here $ a + b $ > 0.", str(out))
    xml = zipfile.ZipFile(str(out)).read("word/document.xml").decode("utf-8")
    assert xml.count("<m:oMath>") == 2 and "$" not in words(xml)


def test_cref_names_from_titles_with_maths_and_without_an_aux(tmp_path):
    _, z = export(tmp_path, ARTICLE % ("\\usepackage{hyperref}\\usepackage{cleveref}",
                                       "\\section{On $L^{2}$}\\label{sec:l2}\\subsection{B}\\label{sub}\n"
                                       "See \\cref{sec:l2}, \\cref{sec:l2,sub}."),
                  aux="\\newlabel{sec:l2}{{1}{1}{On \\ensuremath {L^{2}}}{section.1}{}}\n"
                      "\\newlabel{sub}{{1.1}{1}{B}{subsection.1.1}{}}\n")
    assert "See section 1, sections 1 and 1.1." in words(z.read("word/document.xml").decode("utf-8"))
    _, z = export(tmp_path, ARTICLE % ("\\usepackage{cleveref}", "\\begin{equation} a \\label{a} \\end{equation}\n"
                                                                 "See \\cref{a}."))
    assert "See eq. (1)." in words(z.read("word/document.xml").decode("utf-8"))


def test_comments_in_subequations(tmp_path):
    _, z = export(tmp_path, ARTICLE % ("", "\\begin{subequations}\\label{g}\n% first\n"
                                           "\\begin{equation} x = 1 \\label{s1} \\end{equation}\n% second\n"
                                           "\\begin{equation} y = 2 \\label{s2} \\end{equation}\n\\end{subequations}"),
                  aux="\\newlabel{g}{{1}{1}}\n\\newlabel{s1}{{1a}{1}}\n\\newlabel{s2}{{1b}{1}}\n")
    xml = z.read("word/document.xml").decode("utf-8")
    assert numbers(xml) == ["(1a)", "(1b)"] and xml.count("<m:oMath>") == 2


def test_a_latin_1_paper_opens_too(tmp_path):
    from mathshuttle.documents import read_document
    (tmp_path / "old.tex").write_bytes("\\documentclass{article}\\begin{document}Caf\u00e9 $x$.\\end{document}\n"
                                       .encode("latin-1"))
    assert [f.equation.tex for f in read_document(str(tmp_path / "old.tex")).found] == ["x"]


def test_autoref_and_cref_in_one_document_and_mixed_lists(tmp_path):
    aux = "\\newlabel{e}{{1}{1}{}{equation.1}{}}\n\\newlabel{fig:a}{{2}{1}{}{figure.2}{}}\n"
    _, z = export(tmp_path, ARTICLE % ("\\usepackage{hyperref}\\usepackage{cleveref}",
                                       "A \\autoref{fig:a}. B \\cref{e}. C \\cref{fig:a,e}. D \\Cref{fig:a}."), aux=aux)
    assert "A Figure 2. B eq. (1). C fig. 2 and eq. (1). D Fig. 2." in words(z.read("word/document.xml").decode("utf-8"))
