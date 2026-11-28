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


# ---- what the Gemini review added ---------------------------------------------------------------------
@pytest.mark.parametrize("definitions, body, wanted", [
    # a macro that takes arguments gets no braces of its own: they would cut it from its argument
    (r"\DeclarePairedDelimiter{\abs}{\lvert}{\rvert}", r"e^\abs{x}", r"\lvert"),
    (r"\newcommand{\myvec}[1]{\mathbf{#1}}", r"x^\myvec{a}", r"\mathbf"),
    (r"\newcommand{\nrm}[1]{\left\|#1\right\|}", r"\frac1\nrm{x}", r"\left"),
])
def test_a_macro_with_arguments_is_not_cut_from_them(definitions, body, wanted):
    out, notes = compat.expand_macros(body, compat.Macros(text=definitions))
    assert wanted in out and "kept as written" not in " ".join(notes)


def test_second_arguments_of_overset_and_cfrac_stay_whole():
    out, _ = compat.expand_macros(r"\overset\K\K + \cfrac1\K", compat.Macros(text=r"\newcommand{\K}{\mathcal{K}}"))
    assert out == r"\overset{\mathcal{K}}{\mathcal{K}} + \cfrac1{\mathcal{K}}"


def test_symbols_swapped_with_let_and_a_wrapped_hat_expand():
    swap = compat.Macros(text=r"\let\temp\phi\let\phi\varphi\let\varphi\temp")
    out, notes = compat.expand_macros(r"\phi + \varphi", swap)
    assert out.replace(" ", "") == r"\varphi+\phi" and "kept" not in " ".join(notes)
    wrapped = compat.Macros(text=r"\let\oldhat\hat\renewcommand{\hat}[1]{\oldhat{\mathbf{#1}}}")
    out, notes = compat.expand_macros(r"\hat{x}", wrapped)
    assert r"\mathbf{x}" in out and "kept" not in " ".join(notes)


def test_gdef_and_a_later_plain_definition_are_no_machinery():
    out, _ = compat.expand_macros(r"x \in \R", compat.Macros(text=r"\gdef\R{\mathbb{R}}"))
    assert out == r"x \in \mathbb{R}"
    later = compat.Macros(text="\\newcommand{\\R}{\\@foo}\n\\renewcommand{\\R}{\\mathbb{R}}")
    out, _ = compat.expand_macros(r"x \in \R", later)
    assert out == r"x \in \mathbb{R}"


def test_a_preamble_macro_with_an_at_sign_in_its_text_expands_in_a_document(tmp_path, capsys):
    paper = tmp_path / "paper.tex"
    paper.write_text("\\documentclass{article}\n\\newcommand{\\contact}{\\text{a@b}}\n"
                     "\\makeatletter\\newcommand{\\eqlabel}[1]{\\label{#1}\\global\\@namedef{eq@#1}{}}\\makeatother\n"
                     "\\begin{document}\n$\\contact$ and $x\\eqlabel{a}$\n\\end{document}\n", encoding="utf-8")
    assert main(["extract", str(paper), "--expand-macros"]) == 0
    captured = capsys.readouterr()
    lines = [line for line in captured.out.splitlines() if line.startswith("$")]
    assert lines == ["$\\text{a@b}$", "$x\\eqlabel{a}$"]
    assert "internal" in captured.err and "\\eqlabel" in captured.err


def test_a_control_symbol_paired_delimiter_before_a_letter():
    out, _ = compat.expand_macros(r"\1x", compat.Macros(text=r"\DeclarePairedDelimiter{\1}{\lvert}{\rvert}"))
    assert r"\lvert" in out and r"\1" not in out


def test_a_stale_override_message_offers_no_download(monkeypatch, tmp_path):
    from mathshuttle import pandoc_engine
    monkeypatch.setattr(pandoc_engine, "_pandoc_path", None)
    monkeypatch.setenv("MATHSHUTTLE_PANDOC", str(tmp_path / "missing" / "pandoc.exe"))
    with pytest.raises(pandoc_engine.PandocNotFound) as e:
        pandoc_engine.find_pandoc()
    assert "get-pandoc" not in str(e.value) and "unset" in str(e.value)
