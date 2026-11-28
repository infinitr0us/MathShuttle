"""Spaces for Office: Word's MathML import turns every <mspace> into one no-break space, but keeps Unicode
spaces (checked with Word / PowerPoint 16.0.20527), so Copy for Word/PowerPoint writes those, and reading
MathML turns them back into the widths texmath writes \\, \\; \\quad \\qquad for."""
import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from mathshuttle.convert import convert, office_copy  # noqa: E402


@pytest.mark.parametrize("tex, chars", [
    (r"a\,b", " "), (r"a\;b", " "), (r"a\quad b", " "), (r"a\qquad b", "  "),
    (r"a\hspace{3em}b", "   "),
])
def test_office_gets_unicode_spaces(tex, chars):
    mathml = office_copy(tex, "latex").mathml
    assert "<mo>%s</mo>" % chars in mathml and "mspace" not in mathml


def test_a_negative_space_is_left_out_for_office():
    mathml = office_copy(r"a\!b", "latex").mathml
    assert "mspace" not in mathml and "\xa0" not in mathml


@pytest.mark.parametrize("tex, back", [
    (r"e\!^{-x}", "e{}^{-x}"), (r"\!_0F_1", "{}_{0}F_{1}"), (r"\frac{a}{\!}", r"\frac{a}{}"),
    (r"\overset{\!}{x}", r"\overset{}{x}")])
def test_a_negative_space_that_is_an_argument_leaves_an_empty_one(tex, back):
    # (a script on \! is a script on nothing: removing the space would leave <msup> one child short)
    assert convert(office_copy(tex, "latex").mathml, "mathml", "latex").text == "\\[ %s \\]" % back


@pytest.mark.parametrize("tex", [r"a\quad\,b", r"\frac{\quad}{\quad}", r"x_{\quad}^{\quad}"])
def test_only_word_s_split_em_spaces_are_merged(tex):
    # Word splits \qquad into two em spaces; a \quad\, or the spaces of two arguments stay as they are
    assert convert(office_copy(tex, "latex").mathml, "mathml", "latex").text == "\\[ %s \\]" % tex.replace(
        r"\quad\,b", r"\quad\, b")


@pytest.mark.parametrize("tex, back", [
    (r"a\,b", r"a\, b"), (r"a\;b", r"a\; b"), (r"a\quad b", r"a\quad b"), (r"a\qquad b", r"a\qquad b"),
])
def test_the_spaces_come_back(tex, back):
    assert convert(office_copy(tex, "latex").mathml, "mathml", "latex", wrapper="bare").text == back


def test_two_em_spaces_as_word_writes_them_are_a_qquad():
    word = ('<math xmlns="http://www.w3.org/1998/Math/MathML"><mi>d</mi><mo> </mo><mo> </mo>'
            '<mi>e</mi></math>')
    assert convert(word, "mathml", "latex", wrapper="bare").text == r"d\qquad e"


def test_commas_between_brackets_are_no_separators_for_office():
    # Word made max(cos(a,b),0) max(cos,(a,b)0) and q(x;mu,sigma) q(x;mu;sigma) from separators
    mathml = office_copy(r"\max(\cos(a, b), 0) + q(x;\mu,\sigma)", "latex").mathml
    assert mathml.count('separator="false"') == 4 and "<mo>,</mo>" not in mathml
    back = convert(mathml, "mathml", "latex", wrapper="bare").text
    assert back == convert(r"\max(\cos(a, b), 0) + q(x;\mu,\sigma)", "latex", "latex", wrapper="bare").text or "," in back


def test_other_mathml_keeps_its_spaces():
    mathml = '<math xmlns="http://www.w3.org/1998/Math/MathML"><mi>a</mi><mspace width="0.167em"/><mi>b</mi></math>'
    assert convert(mathml, "mathml", "latex", wrapper="bare").text == r"a\, b"
    assert "mspace" in convert(r"a\,b", "latex", "mathml").text  # only the Office copy uses characters
