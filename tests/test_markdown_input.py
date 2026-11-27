"""LaTeX input that is Markdown text: a chat answer or notes pasted as they are."""
import pytest

from mathshuttle.convert import convert, input_equations
from mathshuttle.pandoc_engine import find_pandoc, looks_like_markdown, PandocNotFound

try:
    find_pandoc()
except PandocNotFound:  # pragma: no cover
    pytest.skip("pandoc not installed", allow_module_level=True)

CHAT = r"""The loss drops by 50% when $\lambda = 0.1$, and the model costs $5 and runs fast.

**Key formula:**

$$
L(\theta) = \sum_{i=1}^{n} \ell(f_\theta(x_i), y_i)
$$

where `x_i` is the input and $y_i$ the label. In code: `cost = $price * 2`.

1. First, \( a^2 + b^2 = c^2 \)
2. Then \[ E = mc^2 \]
"""


def found(text):
    return [("D" if e.display else "i", e.tex) for e in input_equations(text, "latex")]


def test_chat_answer_gives_its_equations_only():
    assert found(CHAT) == [
        ("i", "\\lambda = 0.1"),
        ("D", "L(\\theta) = \\sum_{i=1}^{n} \\ell(f_\\theta(x_i), y_i)"),
        ("i", "y_i"),
        ("i", "a^2 + b^2 = c^2"),
        ("D", "E = mc^2"),
    ]
    result = convert(CHAT, "latex", "mathml")
    assert any("Read as Markdown" in n for n in result.notes)


@pytest.mark.parametrize("text, eqs", [
    ("It costs $5 and $10 in total, while $x+1$ is math.", [("i", "x+1")]),  # money is no maths
    ("The loss drops by 50% when $x$ grows.", [("i", "x")]),                # % is a percent sign
    ("Run `echo $HOME` and then $y$ holds.", [("i", "y")]),                 # no maths in code
    ("## Result\n\nSo $z$ it is.", [("i", "z")]),
])
def test_markdown_signs(text, eqs):
    assert looks_like_markdown(text)
    assert found(text) == eqs


def test_raw_tex_environments_and_definitions_in_markdown():
    text = ("**Steps:**\n\n\\newcommand{\\vv}{\\mathbf{v}}\n\n\\begin{align}\n\\vv &= b \\\\\nc &= d\n\\end{align}\n\n"
            "and 20% of $\\vv$.")
    eqs = found(text)
    assert [kind for kind, _ in eqs] == ["D", "i"]
    assert "\\mathbf{v}" in eqs[0][1] and "c &= d" in eqs[0][1]
    assert eqs[1] == ("i", "\\mathbf{v}")


def test_text_that_latex_cannot_read_is_read_as_markdown():
    text = "Price: 5$ per item, and $x^2$ grows."  # no sign, but unbalanced dollars for LaTeX
    assert not looks_like_markdown(text)
    assert found(text) == [("i", "x^2")]
    assert any("Read as Markdown" in n for n in convert(text, "latex", "mathml").notes)


@pytest.mark.parametrize("text", [
    "We have $ a + b $ % a comment\nand $c$.",                                # spaced dollars, a comment
    "% 50% of the cases, see `table`\n$x$ and $y$",                           # signs in a comment line
    "\\documentclass{article}\n\\begin{document}\n**x** costs $5 and $y$\n\\end{document}",
    "Let $x^{**}$ and $y^{**}$ be duals, $\\$5$ a price.",
])
def test_latex_stays_latex(text):
    assert not looks_like_markdown(text)


def test_latex_reading_unchanged():
    assert found("We have $ a + b $ % a comment\nand $c$.") == [("i", "a + b"), ("i", "c")]
    assert not any("Markdown" in n for n in convert("$x$ and $y$", "latex", "mathml").notes)


def test_markdown_file_with_raw_environments(tmp_path):
    from mathshuttle.documents import read_document
    md = tmp_path / "notes.md"
    md.write_text("# Notes\n\nInline \\(a\\) and:\n\n\\begin{align}\nx &= 1\n\\end{align}\n\nDone $b$.\n",
                  encoding="utf-8")
    doc = read_document(str(md))
    assert [(f.equation.tex, f.equation.display) for f in doc.found] == [
        ("a", False), ("\\begin{align}\nx &= 1\n\\end{align}", True), ("b", False)]
    assert all(f.where == "Notes" for f in doc.found)
