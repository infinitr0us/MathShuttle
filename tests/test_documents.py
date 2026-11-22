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


def test_extract_command():
    env = dict(os.environ, PYTHONIOENCODING="utf-8")
    p = subprocess.run([sys.executable, "-m", "mathshuttle", "extract", os.path.join(DOCS, "sample.pptx")],
                       cwd=ROOT, capture_output=True, env=env)
    out = p.stdout.decode("utf-8").replace("\r\n", "\n")  # text-mode stdout on Windows
    assert p.returncode == 0
    assert out.startswith("% [1] slide 1: Objective\n\\[ \\min_{\\theta}")
    assert "2 equation(s) in sample.pptx" in p.stderr.decode("utf-8")
