"""What the review of 0.8.0 found: each case as a user would meet it."""
from __future__ import annotations

import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from mathshuttle import compat  # noqa: E402
from mathshuttle.__main__ import main  # noqa: E402
from mathshuttle.convert import convert  # noqa: E402
from mathshuttle.documents import read_document  # noqa: E402


def _extract(path, capsys, *more):
    assert main(["extract", str(path), "--expand-macros", *more]) == 0
    out = capsys.readouterr().out
    return [line for line in out.splitlines() if line.startswith("$")]


def test_a_macro_that_makes_an_equation_in_the_prose_keeps_the_order(tmp_path, capsys):
    paper = tmp_path / "paper.tex"
    paper.write_text("\\documentclass{article}\n\\newcommand{\\model}{$x$}\n\\begin{document}\n"
                     "Use \\model{} with $a$ and $b$.\n\\end{document}\n", encoding="utf-8")
    assert _extract(paper, capsys) == ["$x$", "$a$", "$b$"]


def test_an_ensuremath_macro_in_pasted_text_keeps_its_equation():
    out = convert(r"\newcommand{\M}{\ensuremath{M}}Use \M{} and $y$.", "latex", "latex", expand_macros=True)
    assert out.outputs == ["$M$", "$y$"]


def test_a_macro_after_an_optional_argument_stays_one_argument():
    out, _ = compat.expand_macros(r"\sqrt[3]\rads", compat.Macros(text=r"\newcommand{\rads}{2\pi}"))
    assert out == r"\sqrt[3]{2\pi}"


def test_siunitx_options_keep_the_quantity_as_written():
    tex = r"\qty[round-mode=places,round-precision=1]{2.345}{kg}"
    out, _ = compat.expand_macros(tex, None)
    assert out == tex


def test_extract_match_and_word_export_take_the_same_equations(tmp_path, capsys):
    paper = tmp_path / "paper.tex"
    paper.write_text("\\documentclass{article}\n\\newcommand{\\field}{R}\n\\begin{document}\n$x\\in\\field$\n"
                     "\\end{document}\n", encoding="utf-8")
    out = tmp_path / "out.docx"
    assert _extract(paper, capsys, "--match", "\\field", "--docx", str(out)) == ["$x\\in R$"]
    assert len(read_document(str(out)).found) == 1


def test_markdown_paired_delimiters_apply(tmp_path, capsys):
    notes = tmp_path / "notes.md"
    notes.write_text("\\DeclarePairedDelimiter{\\inner}{\\langle}{\\rangle}\n\nMethod: $\\inner{x,y}$.\n",
                     encoding="utf-8")
    assert _extract(notes, capsys) == ["${\\langle {x,y}\\rangle}$"]


def test_get_pandoc_with_a_stale_override_says_so(monkeypatch, capsys, tmp_path):
    from mathshuttle import pandoc_fetch
    monkeypatch.setenv("MATHSHUTTLE_PANDOC", str(tmp_path / "gone" / "pandoc.exe"))
    monkeypatch.setattr(pandoc_fetch, "install", lambda **_: pytest.fail("must not download"))
    assert main(["get-pandoc"]) == 1
    assert "MATHSHUTTLE_PANDOC" in capsys.readouterr().err


def test_bug_report_details_leave_the_user_name_out(monkeypatch, tmp_path):
    from mathshuttle import app, pandoc_engine
    home = os.path.expanduser("~")
    missing = os.path.join(home, "gone", "pandoc.exe")
    monkeypatch.setattr(pandoc_engine, "_pandoc_path", None)
    monkeypatch.setenv("MATHSHUTTLE_PANDOC", missing)
    text = app.bug_report_details(None)
    assert "MATHSHUTTLE_PANDOC" in text
    assert home not in text and home.replace("\\", "\\\\") not in text
    assert os.path.basename(home) not in text or len(os.path.basename(home)) < 3


def test_stale_download_parts_are_removed(tmp_path):
    from mathshuttle import pandoc_fetch
    old, new = tmp_path / "tmpold", tmp_path / "tmpnew"
    old.write_bytes(b"part")
    new.write_bytes(b"part")
    os.utime(old, (0, 0))
    pandoc_fetch._remove_stale_parts(tmp_path)
    assert not old.exists() and new.exists()
