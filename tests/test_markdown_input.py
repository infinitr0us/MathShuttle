"""LaTeX input that is Markdown text: a chat answer or notes pasted as they are."""
import pytest

from mathshuttle.convert import convert, input_equations
from mathshuttle.pandoc_engine import PandocNotFound, find_pandoc, looks_like_markdown, read_latex_text

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
    md.write_text("# Notes\n\nInline $a$ and:\n\n\\begin{align}\nx &= 1\n\\end{align}\n\nDone $b$.\n",
                  encoding="utf-8")
    doc = read_document(str(md))
    assert [(f.equation.tex, f.equation.display) for f in doc.found] == [
        ("a", False), ("\\begin{align}\nx &= 1\n\\end{align}", True), ("b", False)]
    assert all(f.where == "Notes" for f in doc.found)


@pytest.mark.parametrize("body", [
    "See \\[note\\] and \\[1\\] for details. Also $x = 1$.\n",  # pandoc writes [1] so in Markdown
    "- \\[x\\] Done\n- \\[ \\] Pending\n- Formula: $x = 1$\n",
    "Choose \\(a\\) or \\(b\\). Formula: $x = 1$.\n",
])
def test_markdown_file_escaped_brackets_are_no_maths(tmp_path, body):
    from mathshuttle.documents import read_document
    md = tmp_path / "n.md"
    md.write_text("# Doc\n\n" + body, encoding="utf-8")
    assert [f.equation.tex for f in read_document(str(md)).found] == ["x = 1"]


@pytest.mark.parametrize("text, eqs", [
    # LaTeX with something that looks like Markdown: the LaTeX reading, which keeps $ x $ with spaces
    ("Kinetic energy is $0.5 mv^2$ and $ E = mc^2 $.", ["0.5 mv^2", "E = mc^2"]),
    ("The term $2 dx$ integrates to $ 2x $.", ["2 dx", "2x"]),
    ("Consider Poincar\\`e and Amp\\`ere with $ a + b $ and $c$.", ["a + b", "c"]),
    ("See \\url{https://example.com/a/20%20b} for $ x + y $ and $z$.", ["x + y", "z"]),
    ("Use \\verb|50% of| with $ x + y $ and $z$.", ["x + y", "z"]),
    ("Run:\n\\begin{verbatim}\n# compute loss\n\\end{verbatim}\nwhere $ x = 1 $ and $y = 2$.", ["x = 1", "y = 2"]),
])
def test_latex_that_looks_like_markdown_keeps_its_equations(text, eqs):
    found, as_markdown = read_latex_text(text)
    assert [" ".join(e.tex.split()) for e in found] == eqs and not as_markdown


@pytest.mark.parametrize("text, eqs", [
    ("Revenue grew from $5M to $10M, while $x = 2$.", ["x = 2"]),
    ("Plans cost $5/month or $50/year, so $r = 10$.", ["r = 10"]),
    ("It costs $5 a day, or $10 a week, giving $y = 5x$.", ["y = 5x"]),
    ("Save $5 (total $10). Let $x > 0$.", ["x > 0"]),
    ("It costs US$5 or US$10, so $p = 5$.", ["p = 5"]),
    ("Prices: $5, $10.\nHere $y = mx + b$.", ["y = mx + b"]),
    ("- Rate (in %): $r = 0.2$\n- Loss: $L = 1 - r$", ["r = 0.2", "L = 1 - r"]),
    ("The error rate (in %) is $e = 0.05$.", ["e = 0.05"]),
    ("Roughly 50 % of samples satisfy $x > 0$.", ["x > 0"]),
    ("At the p% confidence level, $z = 1.96$.", ["z = 1.96"]),
    ("**1. Cost:** Between $5M and $10M, with $x = 1$.", ["x = 1"]),
    ("**\u516c\u5f0f\uff1a** \u8bef\u5dee (in %) \u4e3a $e = 0.1$\u3002", ["e = 0.1"]),
    ("Run `echo '$HOME:$PATH'` and then $x = 1$.", ["x = 1"]),
    ("Use ``$a`` and ``$b`` in shell, while $x = 1$ is math.", ["x = 1"]),
    # an environment of maths written into the text without $$
    ("**Result:**\n\n\\begin{aligned}\na &= b\n\\end{aligned}\n\nAnd $x$.", ["\\begin{aligned} a &= b \\end{aligned}", "x"]),
])
def test_chat_money_percent_and_code(text, eqs):
    found, as_markdown = read_latex_text(text)
    assert [" ".join(e.tex.split()) for e in found] == eqs and as_markdown


def test_code_fences_money_and_raw_tex():
    # a LaTeX example in a chat answer's code block (with \documentclass and a % line) is not the input
    fence = ("Here is a document:\n\n```latex\n\\documentclass{article}\n% a comment\n\\begin{document}\n"
             "$example$\n\\end{document}\n```\n\nThen use $x$.\n")
    assert found(fence) == [("i", "x")]
    # pandoc's Markdown takes "$5.00 ($" for maths: read again with that $ escaped
    assert found("**Cost:** the plan is $5.00 ($p$ after tax) and $x$ is the quantity.") == [("i", "p"), ("i", "x")]
    # maths inside raw TeX of the text; definitions in raw TeX apply after them only, and once
    assert found("In Markdown, \\textbf{Use $x$} and $y$. **Bold**") == [("i", "x"), ("i", "y")]
    eqs = found("**A** \\begin{equation}\\vec{x}\\end{equation}\n\n\\renewcommand{\\vec}[1]{\\mathbf{#1}}\n\n"
                "\\begin{equation}\\vec{y}\\end{equation}")
    assert len(eqs) == 2 and "\\vec{x}" in eqs[0][1] and "\\mathbf{y}" in eqs[1][1]


@pytest.mark.parametrize("text", ["The plans cost $5 and $10 per month.", "Run `echo $HOME and $PATH` to see."])
def test_markdown_without_maths_has_no_equation(text):
    from mathshuttle.pandoc_engine import ConversionError
    with pytest.raises(ConversionError, match="No equation found"):
        input_equations(text, "latex")


def test_markdown_file_input_is_found_next_to_it(tmp_path):
    from mathshuttle.documents import read_document
    (tmp_path / "part.tex").write_text("$P$\n", encoding="utf-8")
    md = tmp_path / "n.md"
    md.write_text("# T\n\n\\begin{center}$A$ and \\input{part.tex}\\end{center}\n", encoding="utf-8")
    assert [f.equation.tex for f in read_document(str(md)).found] == ["A", "P"]


@pytest.mark.parametrize("text, eqs", [
    # numbers are maths, not money; each occurrence counts; a price pair is no equation
    ("**Answer:** $42$.", ["42"]),
    ("**Ratio:** $1/2$; compare $y$.", ["1/2", "y"]),
    ("Let $2 x$ be twice $x$. Thus $ x $ is positive.", ["2 x", "x", "x"]),
    ("Set $ 42 $ here. 1% comment", ["42"]),
    ("**Total:** $5 + $10 in fees. Let $t$ be the total.", ["t"]),
    # the money $ that is escaped is the right one, and as often as needed
    ("Compare \\$5.00 (old) to $5.00 ($p$).", ["p"]),
    ("$$5.00 (a+b)$$ and **cost** $5.00 ($p$ after tax).", ["5.00 (a+b)", "p"]),
    ("**Plans:** $5, $10, $15, $20, or $25 ($p$ after tax).", ["p"]),
    # a bare equation with maths in its \mbox stays one; code is no input; a TeX comment is no sign
    ("E = \\mbox{valid for $2 x$}", ["E = \\mbox{valid for $2 x$}"]),
    ("Use `$x$` as the source and $y$ as the result.", ["y"]),
    ("Example:\r\n\r\n```latex {.tex}\r\n$x$\r\n````\r\n\r\nThen $y$.\r\n", ["y"]),
    ("Set $x=1$ % `old` choice: $x=0$\nthen $y=x$.", ["x=1", "y=x"]),
    ("A percent (%) is the unit. $x$.", ["x"]),
    ("**Result:** \\begin{aligned}a&=b\\\\[4pt]c&=d\\end{aligned} and $y$.",
     ["\\begin{aligned}a&=b\\\\[4pt]c&=d\\end{aligned}", "y"]),
])
def test_review_round_three(text, eqs):
    assert [" ".join(e.tex.split()) for e in read_latex_text(text)[0]] == eqs


@pytest.mark.parametrize("text, eqs", [
    # LaTeX as v0.4.0 read it: TeX's quotes are no code, a comment is no sign, nor its maths an equation
    ("The `inner' loop updates $x_i$ and the `outer' loop updates $y_j$.", ["x_i", "y_j"]),
    ("Consider `A' ($a=1$), `B' ($b=2$), and `C' ($c=3$).", ["a=1", "b=2", "c=3"]),
    ("In ``fast'' $f(x)$ and ``slow'' $g(x)$ we use $2 x$ steps.", ["f(x)", "g(x)", "2 x"]),
    ("x = 1 % initial value", ["x = 1 % initial value"]),
    ("f(x) = (x + 1)% old term\n+ 2", ["f(x) = (x + 1)% old term + 2"]),
    ("We set $x = 1$% old: $x_0 = 0$ with 50% drop\nand compute $y = x^2$.", ["x = 1", "y = x^2"]),
    ("We have $x = 1$ \\\\ % 50% old: $x_0$\nand $y = 2$.", ["x = 1", "y = 2"]),
    # chat: numbers, tables with prices, callouts, prices per month and ranges
    ("**Values:** $0, 1$ and $10 - 3$ and $1 - 0.5$.", ["0, 1", "10 - 3", "1 - 0.5"]),
    ("| Plan | Price | Formula |\n| --- | --- | --- |\n| Basic | $10 | $y = 10x$ |\n| Pro | $20 | $y = 20x$ |\n",
     ["y = 10x", "y = 20x"]),
    ("# Notes\n\n> [!note] Key Identity\n> $$\n> E = mc^2\n> $$\n", ["E = mc^2"]),
    ("**Cost:** $5/mo ($p$/yr).", ["p"]),
    ("**Range:** $5-$10 ($p$ after tax).", ["p"]),
])
def test_final_check_cases(text, eqs):
    assert [" ".join(e.tex.split()) for e in read_latex_text(text)[0]] == eqs


def test_markdown_file_money_before_maths(tmp_path):
    from mathshuttle.documents import read_document
    md = tmp_path / "price.md"
    md.write_text("# Pricing\n\nThe plan costs $5.00 ($p$ after tax) and $x$ is the quantity.\n", encoding="utf-8")
    assert [f.equation.tex for f in read_document(str(md)).found] == ["p", "x"]


def test_code_only_answer_and_picker_note(tmp_path):
    from mathshuttle.convert import office_copy
    from mathshuttle.documents import read_document
    from mathshuttle.pandoc_engine import ConversionError
    with pytest.raises(ConversionError, match="No equation found"):
        read_latex_text("Here is how:\n\n```\n$x$\n```\n")
    eqs = input_equations("20% of $x$.", "latex")  # what the menu took: the note comes along
    assert any("Markdown" in n for n in office_copy("20% of $x$.", "latex", eqs=eqs).notes)
    # definitions from an \input and from a block with \$ in it carry over to the raw TeX after them
    (tmp_path / "defs.tex").write_text("\\newcommand{\\answer}{99}\n", encoding="utf-8")
    md = tmp_path / "n.md"
    md.write_text("# T\n\n\\input{defs.tex}\n\n\\begin{align}\\answer&=1\\end{align}\n\n"
                  "\\newcommand{\\myunit}{\\text{\\$}}\n\n\\begin{equation}\\myunit 5\\end{equation}\n", encoding="utf-8")
    found = [f.equation.tex for f in read_document(str(md)).found]
    assert len(found) == 2 and "99" in found[0] and "\\text{\\$}" in found[1]


def test_same_reading_both_ways_gives_no_note():
    # $2 x$ looks like money to the detector, but both readers find the same: no Markdown note
    result = convert("Let $2 x$ and $y$ hold.", "latex", "mathml")
    assert len(result.equations) == 2 and not any("Markdown" in n for n in result.notes)
