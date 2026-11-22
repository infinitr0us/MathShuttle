"""Drives the tkinter window without showing it: convert, copy for Office, paste from Office, swap.

Needs a desktop session (tkinter) and pandoc; overwrites the clipboard.
"""
import os
import re
import sys
import time

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from mathshuttle import clipboard  # noqa: E402
from mathshuttle.app import App  # noqa: E402


SETTINGS_TMP = os.path.join(os.path.dirname(__file__), "_settings_tmp.json")


@pytest.fixture(scope="module")
def root():
    # One Tk root for the module: Anaconda's Tk occasionally fails to initialise when a
    # process creates and destroys several roots in a row.
    import mathshuttle.app as app_module
    app_module.SETTINGS_PATH = SETTINGS_TMP
    a = App()
    a.withdraw()
    yield a
    a.destroy()
    try:
        os.remove(SETTINGS_TMP)
    except OSError:
        pass


@pytest.fixture
def app(root):
    root.auto_var.set(False)
    root.wrapper_var.set("auto")
    root.mode_var.set("auto")
    root.clear()
    return root


def pump(app, seconds=5.0, until=lambda: False):
    end = time.time() + seconds
    while time.time() < end:
        app.update()
        if until():
            return True
        time.sleep(0.02)
    return until()


def set_input(app, text):
    app.input.delete("1.0", "end")
    app.input.insert("1.0", text)
    app.input.edit_modified(False)


def test_convert_and_error_display(app):
    app.src_var.set("latex")
    app.dst_var.set("mathml")
    set_input(app, "$x^2$")
    app.convert_now()
    assert pump(app, until=lambda: "<math" in app.output.get("1.0", "end"))
    assert 'display="inline"' in app.output.get("1.0", "end")
    assert "inline" in app.status_var.get()

    assert str(app.status_label.cget("foreground")) == ""

    set_input(app, "\\frac{a}")
    app.convert_now()
    assert pump(app, until=lambda: "unexpected eof" in app.output.get("1.0", "end"))
    assert app.output.tag_ranges("error")
    assert app.status_var.get() == "Conversion failed"
    assert str(app.status_label.cget("foreground")) == "#b00020"  # errors in red, not like a success


def test_copy_for_office_and_paste_back(app):
    app.src_var.set("latex")
    app.dst_var.set("omml")
    set_input(app, "\\begin{align} a &= b \\\\ c &= d \\end{align}")
    app.copy_for_office()
    assert "Ctrl+V" in app.status_var.get()
    mathml = clipboard.read_equation_from_office()
    assert mathml and "<mtable" in mathml

    set_input(app, "")
    app.paste_from_office()
    assert app.src_var.get() == "mathml"
    assert app.dst_var.get() == "omml"
    assert "<math" in app.input.get("1.0", "end")
    assert pump(app, until=lambda: "<m:eqArr>" in app.output.get("1.0", "end"))

    app.dst_var.set("latex")
    app.wrapper_var.set("align*")
    app.convert_now()
    assert pump(app, until=lambda: app.output.get("1.0", "end").startswith("\\begin{align*}"))


def test_swap_moves_output_to_input(app):
    app.src_var.set("latex")
    app.dst_var.set("mathml")
    set_input(app, "x^2")
    app.convert_now()
    assert pump(app, until=lambda: "<math" in app.output.get("1.0", "end"))
    app.swap()
    assert app.src_var.get() == "mathml" and app.dst_var.get() == "latex"
    assert "<math" in app.input.get("1.0", "end")
    assert pump(app, until=lambda: app.output.get("1.0", "end").strip() == "\\[ x^{2} \\]")
    # eqn cannot be an input format, so swapping is refused
    app.dst_var.set("eqn")
    app.swap()
    assert "Cannot swap" in app.status_var.get()


@pytest.fixture
def slow_convert(monkeypatch):
    """Make conversions take 0.4 s (so that they can be overtaken) and record the calls."""
    import mathshuttle.app as app_module
    real, calls = app_module.convert, []

    def convert(*args, **kwargs):
        calls.append(args)
        time.sleep(0.4)
        return real(*args, **kwargs)

    monkeypatch.setattr(app_module, "convert", convert)
    return calls


def test_clear_discards_a_running_conversion(app, slow_convert):
    app.src_var.set("latex")
    app.dst_var.set("mathml")
    set_input(app, "x^2")
    app.convert_now()
    pump(app, 0.1)
    app.clear()
    pump(app, 0.8)
    assert slow_convert and app.output.get("1.0", "end-1c") == ""


def test_convert_now_replaces_a_pending_auto_conversion(app, slow_convert):
    app.src_var.set("latex")
    app.dst_var.set("mathml")
    set_input(app, "x^2")
    app.auto_var.set(True)
    app._schedule()   # as after a keystroke...
    app.convert_now()  # ...followed by Ctrl+Enter
    assert pump(app, 3, until=lambda: "<math" in app.output.get("1.0", "end"))
    pump(app, 0.8)    # DEBOUNCE_MS has passed
    assert len(slow_convert) == 1


def test_swap_does_not_reuse_a_stale_output(app):
    app.src_var.set("latex")
    app.dst_var.set("mathml")
    set_input(app, "x^2")
    app.convert_now()
    assert pump(app, until=lambda: "<math" in app.output.get("1.0", "end"))
    app.dst_var.set("omml")  # auto-convert is off: the output pane still holds MathML
    app.swap()  # converts to OMML first, in the background
    assert pump(app, until=lambda: app.src_var.get() == "omml")
    assert app.input.get("1.0", "end").startswith("<m:oMathPara")
    assert pump(app, until=lambda: app.output.get("1.0", "end").strip() == "\\[ x^{2} \\]")


def test_swap_does_not_block_and_is_cancelled_by_a_change(app, slow_convert):
    app.src_var.set("latex")
    app.dst_var.set("mathml")
    set_input(app, "x^2")
    start = time.time()
    app.swap()  # stale output (nothing converted yet): the conversion runs in a worker
    assert time.time() - start < 0.3 and app.src_var.get() == "latex"
    app.dst_var.set("omml")  # auto-convert is off, so no newer conversion supersedes the swap's
    assert pump(app, 2, until=lambda: "cancelled" in app.status_var.get())
    assert app.src_var.get() == "latex" and app.input.get("1.0", "end-1c") == "x^2"

    # the same for a swap whose conversion fails: its error must not replace the output either
    app.dst_var.set("mathml")
    set_input(app, "\\frac{a}")
    app._set_output("kept")
    app.swap()
    set_input(app, "x")  # typed while converting
    assert pump(app, 2, until=lambda: "cancelled" in app.status_var.get())
    assert app.output.get("1.0", "end-1c") == "kept"


def test_long_input_is_saved_whole(app, tmp_path, monkeypatch):
    import json
    import mathshuttle.app as app_module
    monkeypatch.setattr(app_module, "SETTINGS_PATH", str(tmp_path / "settings.json"))
    text = "$" + "x" * 30000 + "$"
    set_input(app, text)
    app._save_settings()
    with open(app_module.SETTINGS_PATH, encoding="utf-8") as f:
        assert json.load(f)["input"] == text
    # a very large input (a whole document.xml) is not reloaded at every start - and never cut
    set_input(app, "x" * (app_module.MAX_SAVED_INPUT + 1))
    app._save_settings()
    with open(app_module.SETTINGS_PATH, encoding="utf-8") as f:
        assert json.load(f)["input"] == ""


def test_bad_saved_geometry_is_ignored(app, tmp_path, monkeypatch):
    # records what is applied (a withdrawn Tk window does not report later size changes)
    before, real, applied = app.geometry(), app.geometry, []

    def geometry(value=None):
        if value is None:
            return real()
        real(value)  # raises TclError for a malformed value, as in App()
        applied.append(value)

    monkeypatch.setattr(app, "geometry", geometry)
    try:
        app._restore_geometry("garbage")  # used to make App() fail - on every start
        app._restore_geometry("1100x600+-32000+-32000")  # on a monitor that is gone: keep the size only
        app._restore_geometry("1100x600+100+100")
        app._restore_geometry("1100x600-32000-32000")  # from the right / bottom edge: cannot be checked
        assert applied == ["1200x640", "1100x600", "1100x600+100+100", "1100x600"]
    finally:
        real(before)
    import mathshuttle.app as app_module
    settings = tmp_path / "settings.json"
    settings.write_text("[1, 2]", encoding="utf-8")
    monkeypatch.setattr(app_module, "SETTINGS_PATH", str(settings))
    assert app._load_settings() == {}


def test_engine_version_survives_auto_convert(app):
    # The engine pane is filled by the startup check and must outlive the first auto-conversion,
    # which rewrites the status pane ("Converting..." then the summary) DEBOUNCE_MS after startup.
    assert pump(app, until=lambda: "texmath" in app.engine_var.get())
    engine = app.engine_var.get()
    assert re.fullmatch(r"pandoc \S+  \(texmath \d+(?:\.\d+)+\)", engine), engine
    app.src_var.set("latex")
    app.dst_var.set("mathml")
    set_input(app, "$x^2$")
    app.auto_var.set(True)
    app._schedule()
    assert pump(app, until=lambda: "inline" in app.status_var.get())
    assert app.engine_var.get() == engine
    assert "texmath" not in app.status_var.get()
