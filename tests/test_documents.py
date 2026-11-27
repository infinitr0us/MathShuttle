"""Equations of whole documents (documents.py) and `python -m mathshuttle extract`.

fixtures/documents: sample.docx and sample.pptx were made with Word / PowerPoint 16.0.20527 (headings, inline
and display equations, an equation array; two slides with a title and an equation each); paper.tex \\inputs
macros.tex; notes.md has dollar amounts in its prose.
"""
import os
import subprocess
import sys

import pytest

ROOT = os.path.join(os.path.dirname(__file__), "..")
sys.path.insert(0, ROOT)

from mathshuttle.documents import MARK, read_document  # noqa: E402
from mathshuttle.pandoc_engine import ConversionError  # noqa: E402

DOCS = os.path.join(os.path.dirname(__file__), "fixtures", "documents")


def doc(name):
    return read_document(os.path.join(DOCS, name))


def test_word_document():
    d = doc("sample.docx")
    assert d.source == "office" and not d.notes
    assert [f.where for f in d.found] == ["Method", "Method", "Bounds"]  # headings with localised style ids
    inline, display, aligned = d.found
    assert inline.equation.tex == r"P_{\mathrm{OOD}}^{(t)}" and not inline.equation.display
    assert inline.context == "The loss uses %s for every step." % MARK
    # Word stores "ℒ=" as one script run: only the letter is calligraphic
    assert display.equation.tex.startswith(r"\mathcal{L} = \frac{1}{n}\sum")
    assert aligned.equation.tex.startswith("\\begin{aligned}") and "\\binom{n}{k}" in aligned.equation.tex


def test_powerpoint_presentation():
    d = doc("sample.pptx")
    assert [f.where for f in d.found] == ["slide 1: Objective", "slide 2: Update rule"]
    assert all(f.equation.display for f in d.found)  # alone in their paragraph
    assert d.found[1].equation.tex == r"\theta_{t + 1} = \theta_{t} - \eta\nabla_{\theta}\mathcal{L}"


def test_latex_document_with_input_and_labels():
    d = doc("paper.tex")
    assert d.source == "latex"
    wheres = [f.where for f in d.found]
    assert wheres == ["Method", "Method  [eq:loss]", "Method", "Bounds  [eq:first]"]
    assert "\\mathbb{R}" in d.found[0].equation.tex  # \R from \input{macros}
    assert "\\lVert" in d.found[1].equation.tex


def test_markdown_dollar_amounts_are_not_maths():
    d = doc("notes.md")
    assert [f.equation.tex for f in d.found] == ["y = Wx + b", r"\sigma(z) = \frac{1}{1 + e^{-z}}"]


def test_unreadable_files(tmp_path):
    bad = tmp_path / "broken.docx"
    bad.write_bytes(b"not a zip")
    with pytest.raises(ConversionError, match="not a readable"):
        read_document(str(bad))
    with pytest.raises(ConversionError, match="Cannot read .xyz"):
        read_document(str(tmp_path / "x.xyz"))


@pytest.mark.parametrize("name", ["sample.docx", "sample.pptx", "paper.tex", "notes.md"])
def test_export_to_word(tmp_path, name):
    from mathshuttle.documents import write_docx
    d = doc(name)
    out = tmp_path / (name + ".docx")
    assert write_docx(d, str(out)) == []
    back = read_document(str(out))  # native equations, in order, display or inline as they were
    assert len(back.found) == len(d.found)
    assert [f.equation.display for f in back.found] == [f.equation.display for f in d.found]
    assert all(f.where == "Equations of " + name for f in back.found)
    assert os.listdir(str(tmp_path)) == [out.name]  # no part file left


def test_export_to_word_notes_and_failures(tmp_path, monkeypatch):
    from mathshuttle import documents
    from mathshuttle.documents import DocumentEquations, Found, write_docx
    from mathshuttle.pandoc_engine import Equation
    d = DocumentEquations("x.tex", "latex", [Found(Equation("\\nosuchmacro{x}", True), "A"),
                                             Found(Equation("a \\tag{1}", True), "B")])
    out = tmp_path / "x.docx"
    notes = write_docx(d, str(out))
    assert any("1 equation could not be converted" in n for n in notes)
    assert any("\\tag" in n for n in notes)
    # a failed run leaves the file that was there as it was
    out.write_bytes(b"old")
    monkeypatch.setattr(documents, "_run", lambda *a, **k: (_ for _ in ()).throw(ConversionError("no")))
    with pytest.raises(ConversionError):
        write_docx(d, str(out))
    assert out.read_bytes() == b"old" and os.listdir(str(tmp_path)) == ["x.docx"]


def test_export_to_word_passes_on_what_was_approximated(tmp_path):
    from mathshuttle.documents import DocumentEquations, Found, write_docx
    from mathshuttle.pandoc_engine import Equation
    latex = DocumentEquations("x.tex", "latex", [Found(Equation("\\textcolor{red}{y} + \\tbinom{n}{k}", True), "A")])
    notes = write_docx(latex, str(tmp_path / "a.docx"))
    assert any("colour" in n for n in notes) and len(notes) == len(set(notes))
    office = DocumentEquations("x.docx", "office", [Found(Equation("a \\tag{1}", True), "A")])
    assert write_docx(office, str(tmp_path / "b.docx")) == []  # LaTeX from Office: no LaTeX-input notes


def test_export_to_word_keeps_going_past_an_equation_compat_rejects(tmp_path):
    from mathshuttle.documents import DocumentEquations, Found, write_docx
    from mathshuttle.pandoc_engine import Equation
    d = DocumentEquations("x.tex", "latex", [Found(Equation("x", True), "A"),
                                             Found(Equation("{\\newcommand{\\vct}{V}\\vct}", True), "B"),
                                             Found(Equation("y", True), "C")])
    out = tmp_path / "x.docx"
    notes = write_docx(d, str(out))
    assert len(read_document(str(out)).found) == 2 and any("could not be converted" in n for n in notes)


def test_extract_command_checks_macro_files(tmp_path):
    env = dict(os.environ, PYTHONIOENCODING="utf-8")
    p = subprocess.run([sys.executable, "-m", "mathshuttle", "extract", os.path.join(DOCS, "paper.tex"),
                        "--macros", str(tmp_path / "missing.tex"), "--docx", str(tmp_path / "x.docx")],
                       cwd=ROOT, capture_output=True, env=env)
    assert p.returncode == 1 and "does not exist" in p.stderr.decode("utf-8")
    assert not (tmp_path / "x.docx").exists()


def test_sized_norm_bars_are_norms():
    from mathshuttle.compat import for_texmath
    from mathshuttle.pandoc_engine import Equation, write_mathml
    for tex in ("\\left\\lVert x \\right\\rVert", "\\bigl\\lVert x \\bigr\\rVert", "\\Bigl\\| x \\Bigr\\|",
                "\\left( P \\middle\\lVert Q \\right)", "\\left% a comment\n\\lVert x \\right\\rVert"):
        mathml = write_mathml(Equation(for_texmath(tex)[0], True))
        assert "‖" in mathml and "∥" not in mathml, tex
    assert "∥" in write_mathml(Equation(for_texmath("a \\parallel b")[0], True))  # parallel stays


def test_extract_command_writes_word(tmp_path):
    env = dict(os.environ, PYTHONIOENCODING="utf-8")
    out = tmp_path / "eqs.docx"
    p = subprocess.run([sys.executable, "-m", "mathshuttle", "extract", os.path.join(DOCS, "sample.pptx"),
                        "--docx", str(out)], cwd=ROOT, capture_output=True, env=env)
    assert p.returncode == 0 and "written to" in p.stderr.decode("utf-8")
    assert len(read_document(str(out)).found) == 2


def test_extract_command():
    env = dict(os.environ, PYTHONIOENCODING="utf-8")
    p = subprocess.run([sys.executable, "-m", "mathshuttle", "extract", os.path.join(DOCS, "sample.pptx")],
                       cwd=ROOT, capture_output=True, env=env)
    out = p.stdout.decode("utf-8").replace("\r\n", "\n")  # text-mode stdout on Windows
    assert p.returncode == 0
    assert out.startswith("% [1] slide 1: Objective\n\\[ \\min_{\\theta}")
    assert "2 equation(s) in sample.pptx" in p.stderr.decode("utf-8")
