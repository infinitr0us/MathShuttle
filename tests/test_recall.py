"""The LaTeX an equation was copied from, given back when it returns from Word unedited (recall).
fixtures/recall/word_16.0.20527*.json: what real Word 16.0.20527 copied back for equations MathShuttle
copied (captured with tests/office_smoke.py's helpers; _structures: accents, matrices, binomials, fonts,
limits, the smoke test's equations)."""
import json
import os
import re
import subprocess
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from mathshuttle import recall  # noqa: E402
from mathshuttle.convert import office_copy  # noqa: E402

_FOLDER = os.path.join(os.path.dirname(__file__), "fixtures", "recall")
FIXTURES = json.load(open(os.path.join(_FOLDER, "word_16.0.20527.json"), encoding="utf-8"))
STRUCTURES = json.load(open(os.path.join(_FOLDER, "word_16.0.20527_structures.json"), encoding="utf-8"))


@pytest.mark.parametrize("case", STRUCTURES, ids=[c["tex"][:30] for c in STRUCTURES])
def test_what_word_does_to_structures_is_no_edit(case):
    # accents as other characters, ℓ as 𝓁, sans-serif italic, 1.5 as 1 . 5, scripts on a bracketed group
    assert recall.fingerprint(case["ours"]) == recall.fingerprint(case["word"])


@pytest.mark.parametrize("a, b", [
    ("x", r"\hat{x}"), ("x", r"\bar{x}"), (r"\hat{x}", r"\tilde{x}"), (r"y = f(x)", r"\tilde{y} = f(x)"),
    ("v = u", r"\vec{v} = \vec{u}"), (r"A \cup B", r"\overline{A \cup B}"),
    (r"\begin{pmatrix} a & b \\ c & d \end{pmatrix}", r"\begin{pmatrix} a & b & c & d \end{pmatrix}"),
    (r"\begin{pmatrix} a & b \\ c & d \end{pmatrix}", "(abcd)"),
    (r"\begin{align} a &= b \\ c &= d \end{align}", "a = b c = d"),
    (r"\binom{n}{k}", r"\left(\frac{n}{k}\right)"), (r"\ell", "l"), (r"\mathbf{2} x", "2 x"),
    (r"\langle x, y \rangle", "< x, y >"), (r"a \parallel b", r"a \| b"), (r"\mathbf{x}", "x"), (r"\mathrm{d}x", "dx"),
    (r"\sum_{i} x", r"\sum^{i} x"), (r"x_i", "x^i"), (r"\sqrt{x}", r"\sqrt[3]{x}"), (r"\frac{a}{b}", r"\frac{b}{a}"),
    (r"\mathcal{L}", "L"), (r"\mathbb{R}", "R"), ("x'", "x"), (r"(a+b)^2", r"(a+b^2)"),
    (r"\mathbb{1}_{A}", "1_{A}"), (r"\phi", r"\varphi"), (r"\epsilon", r"\varepsilon")])
def test_an_edit_is_told_apart(a, b):
    assert recall.fingerprint(office_copy(a, "latex").mathml) != recall.fingerprint(office_copy(b, "latex").mathml)


@pytest.mark.parametrize("tex, word", [(r"\epsilon", "\U0001d716"), (r"\vartheta", "\U0001d717"),
                                       (r"\varpi", "\U0001d71b"), (r"\phi", "\U0001d719"), (r"\varphi", "\U0001d711")])
def test_word_s_italic_greek_variants_are_the_same_letters(tex, word):
    # Word writes texmath's ϵ ϑ ϖ ϕ φ in their mathematical italic forms (𝜙 seen in real Word)
    mathml = '<math xmlns="http://www.w3.org/1998/Math/MathML"><mi>%s</mi></math>' % word
    assert recall.fingerprint(office_copy(tex, "latex").mathml) == recall.fingerprint(mathml)


@pytest.fixture
def store(tmp_path, monkeypatch):
    path = tmp_path / "copied.json"
    monkeypatch.setenv("MATHSHUTTLE_RECALL_PATH", str(path))
    return path


@pytest.mark.parametrize("case", FIXTURES, ids=[c["tex"][:30] for c in FIXTURES])
def test_what_word_gives_back_has_the_same_fingerprint(case):
    # bold made bold italic, brackets made a stretchy pair, separators marked, spaces as characters
    assert recall.fingerprint(case["ours"]) == recall.fingerprint(case["word"])


def test_edits_change_the_fingerprint():
    word = next(c["word"] for c in FIXTURES if "softmax" in c["tex"])
    base = recall.fingerprint(word)
    edited = [word.replace("softmax", "softmin"),                      # a name
              re.sub(r"<mi>(?:𝑧|z)</mi>", "<mi>y</mi>", word, count=1),  # a variable
              word.replace("<mi>z</mi>", '<mi mathvariant="normal">z</mi>', 1)]  # a letter made upright
    for variant in edited:
        assert recall.fingerprint(variant) != base, variant
    script = next(c["word"] for c in FIXTURES if "sum" in c["tex"])
    assert recall.fingerprint(script.replace("<mi>n</mi>", "<mi>m</mi>", 1)) != recall.fingerprint(script)


def test_remember_and_recall(store):
    tex = r"\mathcal{L}(\theta) = \E\left[\norm{f_\theta(x) - y}^2\right] \label{eq:loss}"
    copy = office_copy(r"\[ \mathcal{L}(\theta) = \mathbb{E}\left[\left\| f_\theta(x) - y \right\|^2\right] \]", "latex")
    recall.remember(copy.mathml, tex, True)
    assert recall.recall(copy.mathml) == (tex, True)
    from mathshuttle import latex_wrap
    assert recall.latex_for(copy.mathml) == latex_wrap.wrap(tex, "auto", True)
    assert recall.latex_for(copy.mathml, "dollar", "inline") == latex_wrap.wrap(tex, "dollar", False)
    other = office_copy(r"a + b", "latex").mathml
    assert recall.recall(other) is None


def test_office_copy_remembers_only_after_copying(store):
    copy = office_copy(r"\begin{align} a &= b \label{eq:x} \end{align}", "latex")
    assert recall.recall(copy.mathml) is None  # office_copy alone does not remember
    copy.remember()
    tex, display = recall.recall(copy.mathml)
    assert "\\label{eq:x}" in tex and display
    assert recall.latex_for(copy.mathml).startswith("\\begin{align}")


def test_the_newest_copies_are_kept(store, monkeypatch):
    monkeypatch.setattr(recall, "_LIMIT", 3)
    copies = [office_copy("x_%d" % i, "latex") for i in range(5)]
    for i, c in enumerate(copies):
        recall.remember(c.mathml, "x_{%d}" % i, False)
    assert recall.recall(copies[0].mathml) is None and recall.recall(copies[4].mathml) == ("x_{4}", False)


def test_off_with_an_empty_path(monkeypatch):
    monkeypatch.setenv("MATHSHUTTLE_RECALL_PATH", "")
    copy = office_copy("a+b", "latex")
    copy.remember()
    assert recall.recall(copy.mathml) is None


def test_cli_paste_gives_the_latex_as_written(store, monkeypatch, capsys):
    from mathshuttle import clipboard
    from mathshuttle.__main__ import main
    sent = {}
    monkeypatch.setattr(clipboard, "copy_mathml_for_office", lambda mathml, **kw: sent.setdefault("mathml", mathml))
    assert main(["copy", "-e", r"\[ x^2 + \operatorname{softmax}(z) \label{eq:q} \]"]) == 0  # remembers it
    capsys.readouterr()
    word = next(c["word"] for c in FIXTURES if "softmax" in c["tex"])
    monkeypatch.setattr(clipboard, "read_office_equation", lambda: ("mathml", sent["mathml"]))
    assert main(["paste"]) == 0
    out = capsys.readouterr()
    assert "\\label{eq:q}" in out.out and "unchanged" in out.err
    monkeypatch.setattr(clipboard, "read_office_equation", lambda: ("mathml", word))
    assert main(["paste"]) == 0
    assert "\\label" not in capsys.readouterr().out  # another equation: Word's LaTeX
    assert main(["paste", "--no-tidy"]) == 0  # texmath's own LaTeX asked for: no recall
    monkeypatch.setattr(clipboard, "read_office_equation", lambda: ("mathml", sent["mathml"]))
    assert main(["paste", "--no-tidy"]) == 0
    assert "\\label" not in capsys.readouterr().out


def test_the_same_equation_with_two_labels_is_converted_afresh(store):
    first = office_copy(r"x = 0 \label{eq:initial}", "latex")
    first.remember()
    assert recall.recall(first.mathml) == (r"x = 0 \label{eq:initial}", True)
    office_copy(r"x = 0 \label{eq:initial}", "latex").remember()  # the same again: still known
    assert recall.recall(first.mathml) is not None
    office_copy(r"x = 0 \label{eq:terminal}", "latex").remember()  # the same symbols, another label
    assert recall.recall(first.mathml) is None
    office_copy(r"x = 0 \label{eq:initial}", "latex").remember()
    assert recall.recall(first.mathml) is None  # which one it is stays unknown
    other = office_copy(r"y = 1 \label{eq:y}", "latex")  # another equation: unaffected
    other.remember()
    assert recall.recall(other.mathml) == (r"y = 1 \label{eq:y}", True)


def test_a_relative_path_is_kept_next_to_the_working_folder(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("MATHSHUTTLE_RECALL_PATH", "copied.json")
    copy = office_copy("a+b", "latex")
    copy.remember()
    assert (tmp_path / "copied.json").is_file() and recall.recall(copy.mathml) == ("a+b", True)
    assert not [p for p in os.listdir(str(tmp_path)) if p.endswith((".part", ".lock"))]


def test_two_processes_lose_nothing(store):
    script = ("import sys; sys.path.insert(0, %r)\n"
              "from mathshuttle.convert import office_copy\n"
              "for i in range(15):\n"
              "    office_copy('x_{%%s%%d}' %% (sys.argv[1], i), 'latex').remember()\n"
              % os.path.join(os.path.dirname(__file__), ".."))
    env = dict(os.environ, MATHSHUTTLE_RECALL_PATH=str(store))
    runs = [subprocess.Popen([sys.executable, "-c", script, name], env=env) for name in "abcd"]
    assert all(run.wait(120) == 0 for run in runs)
    entries = json.load(open(str(store), encoding="utf-8"))
    assert len(entries) == 60
    assert recall.recall(office_copy("x_{c7}", "latex").mathml) == ("x_{c7}", True)
