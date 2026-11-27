"""The macOS pasteboard backend (_clipboard_macos.py), through the clipboard API the app uses.

Overwrites the clipboard.  Every test reads back only what this process wrote itself: reading what
another app copied may make macOS ask the user first (paste privacy), which a test must not do.
"""
import os
import sys
import threading

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from mathshuttle import clipboard  # noqa: E402

pytestmark = pytest.mark.skipif(sys.platform != "darwin", reason="the macOS pasteboard backend")

if sys.platform == "darwin":
    from mathshuttle import _clipboard_macos as mac  # noqa: E402

MATHML = '<math xmlns="http://www.w3.org/1998/Math/MathML" display="block"><mfrac><mi>α</mi><mn>2</mn></mfrac></math>'


def types():
    return [name for _, name in clipboard.available_formats()]


@pytest.mark.parametrize("text", ["plain", "", "café 公式 \U0001d400 (astral)", "two\nlines\r\n", "a\0b"])
def test_text_round_trip(text):
    clipboard.set_text(text)
    assert clipboard.get_text() == text
    assert mac.TEXT_TYPE in types()


def test_sequence_number_moves_and_guards_a_write():
    clipboard.set_text("first")
    sequence = clipboard.sequence_number()
    assert clipboard.sequence_number() == sequence  # reading does not change it
    clipboard.set_text("copied meanwhile")
    assert clipboard.sequence_number() != sequence
    with pytest.raises(clipboard.ClipboardChanged):
        clipboard.set_text("result", expect_sequence=sequence)
    assert clipboard.get_text() == "copied meanwhile"  # left as it is
    clipboard.set_text("ok", expect_sequence=clipboard.sequence_number())
    assert clipboard.get_text() == "ok"


def test_set_data_writes_several_types_at_once():
    clipboard.set_data({"org.mathshuttle.test-bytes": b"a\0b\xff", mac.TEXT_TYPE: "text too"})
    assert clipboard.get_data("org.mathshuttle.test-bytes") == b"a\0b\xff"  # bytes exactly, NUL included
    assert clipboard.get_text() == "text too"
    assert {"org.mathshuttle.test-bytes", mac.TEXT_TYPE} <= set(types())
    assert clipboard.get_data("org.mathshuttle.not-there") is None
    clipboard.set_text("replaces everything")
    assert "org.mathshuttle.test-bytes" not in types()


def test_copy_mathml_for_office_writes_every_candidate_type():
    clipboard.copy_mathml_for_office(MATHML)
    on = types()
    for t in mac.MATHML_TYPES:
        assert t in on
        data = clipboard.get_data(t)
        assert data.startswith(b'<?xml version="1.0" encoding="UTF-8"?>\n<math') and not data.endswith(b"\0")
        assert data.decode("utf-8").endswith(MATHML)
    assert clipboard.get_text() == MATHML  # the plain text holds the MathML too, as for Word on Windows
    # the richest first, by the pasteboard's convention: the MathML types before the plain text
    assert on.index(mac.MATHML_TYPES[0]) < on.index(mac.TEXT_TYPE)
    assert clipboard.read_equation_from_office().endswith(MATHML)


def test_copy_mathml_for_office_with_other_plain_text():
    clipboard.copy_mathml_for_office(MATHML, plain_text=r"\frac{\alpha}{2}")
    assert clipboard.get_text() == r"\frac{\alpha}{2}"
    assert MATHML in clipboard.read_equation_from_office()


@pytest.mark.parametrize("fmt", ["org.example.MathML-Test", "com.example.mathml"])
def test_read_equation_takes_any_type_named_mathml(fmt):
    clipboard.set_data({fmt: ("\ufeff" + MATHML).encode("utf-16-le")})  # another program's UTF-16, BOM
    assert clipboard.read_equation_from_office() == MATHML


def test_read_equation_wants_mathml():
    clipboard.set_text(MATHML)  # MathML only as plain text: no equation copied from Office
    assert clipboard.read_equation_from_office() is None
    clipboard.set_data({"com.microsoft.MathML": b"<p>not maths</p>"})
    assert clipboard.read_equation_from_office() is None
    clipboard.set_data({"com.microsoft.MathML": b"<mml:math xmlns:mml='x'><mml:mi>x</mml:mi></mml:math>"})
    assert clipboard.read_equation_from_office().startswith("<mml:math")


def test_mathml_types_on_puts_candidates_first():
    on = ["public.utf8-plain-text", "org.example.mathml", "public.mathml.presentation", "public.mathml"]
    assert mac._mathml_types_on(on) == ["public.mathml", "public.mathml.presentation", "org.example.mathml"]


def test_read_office_equation_falls_back_to_the_html():
    # what Mac Word copies for lines aligned at "=": the MathML types without data, the OMML in the HTML
    html = ("<html><body><p class=MsoNormal><!--[if gte msEquation 12]><m:oMath><i><span lang=EN-CA>"
            "<m:r>x</m:r></span></i></m:oMath><![endif]--><![if !msEquation]><img src=x><![endif]></p></body></html>")
    clipboard.set_data({"public.mathml": b"", mac.HTML_TYPE: html.encode("utf-8"), mac.TEXT_TYPE: "x"})
    assert clipboard.read_equation_from_office() is None
    src, omml = clipboard.read_office_equation()
    assert src == "omml" and '<m:t xml:space="preserve">x</m:t>' in omml
    clipboard.copy_mathml_for_office(MATHML)  # MathML, when there is some, comes first
    assert clipboard.read_office_equation() == ("mathml", clipboard.read_equation_from_office())
    clipboard.set_data({mac.HTML_TYPE: b"<html><body><p>no equation</p></body></html>"})
    assert clipboard.read_office_equation() is None


def test_works_from_another_thread():
    # the hotkey converts in a worker thread: no run loop there, objects go through the pool of each call
    errors = []

    def work():
        try:
            for i in range(50):
                clipboard.set_text("thread %d" % i)
                assert clipboard.get_text() == "thread %d" % i
                clipboard.copy_mathml_for_office(MATHML)
                assert clipboard.read_equation_from_office().endswith(MATHML)
        except Exception as e:  # reported below
            errors.append(e)

    thread = threading.Thread(target=work)
    thread.start()
    thread.join(60)
    assert not thread.is_alive() and not errors


def test_this_backend_is_used(monkeypatch):
    monkeypatch.setattr(clipboard, "_backend_module", None)  # chosen again, on first use
    assert clipboard._backend() is mac
