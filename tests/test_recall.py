"""The LaTeX an equation was copied from, given back when it returns from Word unedited (recall).
fixtures/recall/word_16.0.20527.json: what real Word 16.0.20527 copied back for equations MathShuttle
copied (captured with tests/office_smoke.py's helpers)."""
import json
import os
import re
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from mathshuttle import recall  # noqa: E402
from mathshuttle.convert import office_copy  # noqa: E402

FIXTURES = json.load(open(os.path.join(os.path.dirname(__file__), "fixtures", "recall", "word_16.0.20527.json"),
                          encoding="utf-8"))


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
