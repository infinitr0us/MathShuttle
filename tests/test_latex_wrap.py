import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from mathshuttle.latex_wrap import classify, wrap  # noqa: E402

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


def test_sibling_environments_are_not_one_environment():
    two = "\\begin{aligned} a &= b \\end{aligned} \\quad \\begin{aligned} c &= d \\end{aligned}"
    assert classify(two) == ("plain", two)
    assert wrap(two, "align") == "\\begin{align}\n%s\n\\end{align}" % two
    eq = "\\begin{equation}\n\\begin{aligned} a \\end{aligned} = \\begin{aligned} b \\end{aligned}\n\\end{equation}"
    assert classify(eq) == ("plain", "\\begin{aligned} a \\end{aligned} = \\begin{aligned} b \\end{aligned}")
    # properly nested environments of the same name are still recognised
    nested = "\\begin{aligned} \\begin{aligned} a \\end{aligned} &= b \\end{aligned}"
    assert classify(nested)[0] == "align"


def test_eqnarray_columns_become_align_pairs():
    eqn = "\\begin{eqnarray}\na &=& b + c \\\\\n\\{x\\} & \\le & \\frac{1}{2} \\\\\n& & + d\n\\end{eqnarray}"
    assert wrap(eqn, "align*") == "\\begin{align*}\na &= b + c \\\\\n\\{x\\} & \\le  \\frac{1}{2} \\\\\n&  + d\n\\end{align*}"
    assert wrap(eqn, "auto") == eqn  # kept verbatim when the environment is kept
    # a & inside braces is not a column separator
    assert classify("\\begin{eqnarray*} a &=& \\text{x {&} y} \\end{eqnarray*}") == ("align", "a &= \\text{x {&} y}")
    # nor are the & and \\ of a nested matrix (its second row used to lose a separator: "5  6")
    nested = "\\begin{eqnarray} A &=& \\begin{pmatrix} 1 & 2 & 3 \\\\ 4 & 5 & 6 \\end{pmatrix} \\\\ x &=& y \\end{eqnarray}"
    assert classify(nested) == ("align", "A &= \\begin{pmatrix} 1 & 2 & 3 \\\\ 4 & 5 & 6 \\end{pmatrix} \\\\ x &= y")
    # nor one in a comment
    assert classify("\\begin{eqnarray} a &=& b % c & d & e\n\\end{eqnarray}") == ("align", "a &= b % c & d & e")
    # a comment between \begin and the environment name does not hide the nested matrix
    commented = "\\begin{eqnarray} A &=& \\begin% matrix\n{pmatrix} 1 & 2 & 3 \\\\ 4 & 5 & 6 \\end{pmatrix} \\end{eqnarray}"
    assert classify(commented)[1] == "A &= \\begin% matrix\n{pmatrix} 1 & 2 & 3 \\\\ 4 & 5 & 6 \\end{pmatrix}"


def test_position_argument_is_dropped_when_unwrapping():
    assert wrap("\\begin{aligned}[t]x&=y\\end{aligned}", "align") == "\\begin{align}\nx&=y\n\\end{align}"
    assert classify("\\begin{gathered}[b] x \\\\ y \\end{gathered}") == ("gather", "x \\\\ y")
    assert classify("\\begin{multlined}[t][6cm] a \\\\ b \\end{multlined}") == ("multline", "a \\\\ b")
    # kept where the environment itself is kept
    assert wrap("\\begin{aligned}[t]x&=y\\end{aligned}", "bracket") == "\\[ \\begin{aligned}[t]x&=y\\end{aligned} \\]"
    # LaTeX also finds the argument after a comment; the comment stays
    assert wrap("\\begin{aligned}% position\n[t]x&=y\\end{aligned}", "align") == "\\begin{align}\n% position\nx&=y\n\\end{align}"
    assert classify("\\begin{alignat}% columns\n{2} a &= b \\end{alignat}") == ("align", "% columns\n a &= b")


def test_environment_commands_in_comments_are_ignored():
    two = "\\begin{aligned}\na &= b % \\begin{aligned}\n\\end{aligned} \\quad \\begin{aligned}\nc &= d % \\end{aligned}\n\\end{aligned}"
    assert classify(two) == ("plain", two)  # two siblings, not one environment
    assert wrap(two, "align") == "\\begin{align}\n%s\n\\end{align}" % two
    # ... and comments between \begin / \end and the name do not hide the inner boundaries
    split = "\\begin{aligned}a&=b\\end% first\n{aligned}\\quad\\begin% second\n{aligned}c&=d\\end{aligned}"
    assert classify(split) == ("plain", split)


def test_trailing_comment_does_not_swallow_the_closing_delimiter():
    assert wrap("x % comment\n", "dollar") == "$x % comment\n$"
    assert wrap("x % comment", "paren") == "\\(x % comment\n\\)"
    assert wrap("x % comment", "bracket") == "\\[\nx % comment\n\\]"
    assert wrap("x % comment", "ddollar") == "$$\nx % comment\n$$"
    assert wrap("x = 5\\%", "dollar") == "$x = 5\\%$"  # an escaped percent sign is not a comment


def test_split_becomes_aligned_inline():
    assert wrap(EQ_SPLIT, "dollar") == "$\\begin{aligned}\na &= b \\\\\n&= c\n\\end{aligned}$"
    assert wrap("\\begin{split} a &= b \\end{split}", "paren") == "\\(\\begin{aligned}\na &= b\n\\end{aligned}\\)"
    assert wrap(EQ_SPLIT, "bracket").startswith("\\[\n\\begin{split}")  # fine in display maths
    # also next to other content, e.g. the usual \label before it
    labelled = "\\begin{equation}\\label{eq:s}\n\\begin{split}\na &= b \\\\\n&= c\n\\end{split}\n\\end{equation}"
    assert wrap(labelled, "dollar") == "$\\label{eq:s}\n\\begin{aligned}\na &= b \\\\\n&= c\n\\end{aligned}$"
    assert wrap(labelled, "bracket") == "\\[\n\\label{eq:s}\n\\begin{split}\na &= b \\\\\n&= c\n\\end{split}\n\\]"
    assert wrap("x = 1 % \\begin{split}", "dollar") == "$x = 1 % \\begin{split}\n$"  # a comment is left alone
    # a comment between \begin and the name: both ends are renamed, the comment stays
    assert wrap("\\label{s}\\begin% block\n{split}a &= b\\end{split}", "dollar") == \
        "$\\label{s}\\begin% block\n{aligned}a &= b\\end{aligned}$"


def test_gather_conversions():
    assert wrap(GATHER, "gather") == "\\begin{gather}\na = b \\\\\nc = d\n\\end{gather}"
    assert wrap(GATHER, "align") == "\\begin{align}\na = b \\\\\nc = d\n\\end{align}"
    assert wrap(GATHER, "bracket") == "\\[\n\\begin{gathered}\na = b \\\\\nc = d\n\\end{gathered}\n\\]"
    assert wrap(GATHER, "multline") == "\\begin{multline}\na = b \\\\\nc = d\n\\end{multline}"
