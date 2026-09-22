import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from equation_tool.latex_wrap import classify, wrap  # noqa: E402

ALIGN = "\\begin{align}\na &= b \\\\\nc &= d\n\\end{align}"
ALIGNAT = "\\begin{alignat}\n{2}\na &= b & c &= d\n\\end{alignat}"
EQ_SPLIT = "\\begin{equation}\n\\begin{split}\na &= b \\\\\n&= c\n\\end{split}\n\\end{equation}"
ALIGNED = "\\begin{aligned} a &= b \\\\ c &= d \\end{aligned}"
GATHER = "\\begin{gather*}\na = b \\\\\nc = d\n\\end{gather*}"


def test_classify():
    assert classify("x^2") == ("plain", "x^2")
    assert classify("\\begin{equation}\nx^2\n\\end{equation}") == ("plain", "x^2")
    assert classify(ALIGN) == ("align", "a &= b \\\\\nc &= d")
    assert classify(ALIGNAT) == ("align", "a &= b & c &= d")
    assert classify(EQ_SPLIT) == ("align", "a &= b \\\\\n&= c")
    assert classify(ALIGNED) == ("align", "a &= b \\\\ c &= d")
    assert classify(GATHER) == ("gather", "a = b \\\\\nc = d")
    # a leading brace group must not be mistaken for an environment argument
    assert classify("\\begin{equation}{a}\\end{equation}") == ("plain", "{a}")


def test_plain_wrappers():
    assert wrap("x^2", "bare") == "x^2"
    assert wrap("x^2", "dollar") == "$x^2$"
    assert wrap("x^2", "paren") == "\\(x^2\\)"
    assert wrap("x^2", "ddollar") == "$$ x^2 $$"
    assert wrap("x^2", "bracket") == "\\[ x^2 \\]"
    assert wrap("x^2", "equation") == "\\begin{equation}\nx^2\n\\end{equation}"
    assert wrap("x^2", "equation*") == "\\begin{equation*}\nx^2\n\\end{equation*}"
    assert wrap("x^2", "align*") == "\\begin{align*}\nx^2\n\\end{align*}"


def test_auto_follows_mode_or_keeps_environment():
    assert wrap("x^2", "auto", display=True) == "\\[ x^2 \\]"
    assert wrap("x^2", "auto", display=False) == "$x^2$"
    assert wrap(ALIGN, "auto") == ALIGN
    assert wrap(ALIGNED, "auto", display=True) == "\\[ " + ALIGNED + " \\]"
    assert wrap(EQ_SPLIT, "bracket") == "\\[\n\\begin{split}\na &= b \\\\\n&= c\n\\end{split}\n\\]"


def test_align_to_other_wrappers():
    assert wrap(ALIGN, "bare") == "\\begin{aligned}\na &= b \\\\\nc &= d\n\\end{aligned}"
    assert wrap(ALIGN, "dollar") == "$\\begin{aligned}\na &= b \\\\\nc &= d\n\\end{aligned}$"
    assert wrap(ALIGN, "bracket") == "\\[\n\\begin{aligned}\na &= b \\\\\nc &= d\n\\end{aligned}\n\\]"
    assert wrap(ALIGN, "equation") == "\\begin{equation}\n\\begin{aligned}\na &= b \\\\\nc &= d\n\\end{aligned}\n\\end{equation}"
    assert wrap(ALIGN, "align*") == "\\begin{align*}\na &= b \\\\\nc &= d\n\\end{align*}"
    assert wrap(ALIGN, "gather") == "\\begin{gather}\n\\begin{aligned}\na &= b \\\\\nc &= d\n\\end{aligned}\n\\end{gather}"


def test_inner_environments_unwrap_into_align():
    assert wrap(EQ_SPLIT, "align") == "\\begin{align}\na &= b \\\\\n&= c\n\\end{align}"
    assert wrap(ALIGNED, "align") == "\\begin{align}\na &= b \\\\ c &= d\n\\end{align}"
    assert wrap("\\[ " + ALIGNED + " \\]".strip(), "align").startswith("\\begin{align}")


def test_gather_conversions():
    assert wrap(GATHER, "gather") == "\\begin{gather}\na = b \\\\\nc = d\n\\end{gather}"
    assert wrap(GATHER, "align") == "\\begin{align}\na = b \\\\\nc = d\n\\end{align}"
    assert wrap(GATHER, "bracket") == "\\[\n\\begin{gathered}\na = b \\\\\nc = d\n\\end{gathered}\n\\]"
    assert wrap(GATHER, "multline") == "\\begin{multline}\na = b \\\\\nc = d\n\\end{multline}"
