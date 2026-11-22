"""The LaTeX preview (preview.py).  The rendering tests need a LaTeX installation (latex, dvipng)."""
import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from mathshuttle import compat, preview  # noqa: E402
from mathshuttle.pandoc_engine import Equation  # noqa: E402

needs_latex = pytest.mark.skipif(preview.tools() is None, reason="no LaTeX installation on PATH")


@pytest.mark.parametrize("tex, display, expected", [
    ("x^2", False, "$x^2$"),
    (r"\frac{a}{b}", True, r"$\displaystyle \frac{a}{b}$"),
    ("\\begin{align} a &= b \\label{eq:a} \\\\ c &= d \\notag \\end{align}", True,
     "$\\displaystyle \\begin{aligned}\na &= b  \\\\ c &= d \n\\end{aligned}$"),
    ("\\begin{equation} x \\tag{3} \\end{equation}", True, "$\\displaystyle x$"),
])
def test_math_line(tex, display, expected):
    assert " ".join(preview.math_line(tex, display).split()) == " ".join(expected.split())


@pytest.mark.skipif(sys.platform != "darwin" or not os.path.isfile("/Library/TeX/texbin/latex"), reason="MacTeX")
def test_mactex_is_found_without_it_on_path(monkeypatch):
    # an app started from the Finder has launchd's PATH; latex's directory is added for the fonts kpathsea
    # makes with mktexpk (bbm), which it finds through PATH
    monkeypatch.setenv("PATH", "/usr/bin:/bin:/usr/sbin:/sbin")
    assert preview._which("latex") == "/Library/TeX/texbin/latex"
    env = preview._tex_env("/Library/TeX/texbin/latex")
    assert env["PATH"] == "/Library/TeX/texbin:/usr/bin:/bin:/usr/sbin:/sbin"
    monkeypatch.setenv("PATH", "/Library/TeX/texbin:/usr/bin")
    assert preview._tex_env("/Library/TeX/texbin/latex") is None  # already there: this process's own


@needs_latex
def test_render_and_cache(tmp_path):
    eqs = [Equation(r"\left\lvert \frac{x}{2} \right\rvert", True), Equation("a^2", False)]
    first = preview.render(eqs, work=str(tmp_path))
    assert first.endswith(".png") and os.path.getsize(first) > 100
    assert preview.render(eqs, work=str(tmp_path)) == first  # cached
    assert sorted(os.listdir(tmp_path)) == [os.path.basename(first)]  # no .tex / .log / .dvi left


@needs_latex
def test_render_with_the_users_macros(tmp_path):
    macros = compat.Macros(text=r"\DeclarePairedDelimiter{\abs}{\lvert}{\rvert}\newcommand{\R}{\mathbb{R}}")
    tex = r"\abs*{x} \in \R"
    assert preview.render([Equation(tex, True)], preview.definitions_for(tex, macros), work=str(tmp_path))


@needs_latex
def test_render_error_is_latexs_message(tmp_path):
    with pytest.raises(preview.PreviewError, match="LaTeX: .*(Missing|Undefined)"):
        preview.render([Equation(r"\frac{a}", True)], work=str(tmp_path))
