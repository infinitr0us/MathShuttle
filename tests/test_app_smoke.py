"""Drives the tkinter window without showing it: convert, copy for Office, paste from Office, swap.

Needs a desktop session (tkinter) and pandoc; overwrites the clipboard.
"""
import os
import re
import sys
import time

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from equation_tool import clipboard  # noqa: E402
from equation_tool.app import App  # noqa: E402


SETTINGS_TMP = os.path.join(os.path.dirname(__file__), "_settings_tmp.json")


@pytest.fixture(scope="module")
def root():
    # One Tk root for the module: Anaconda's Tk occasionally fails to initialise when a
    # process creates and destroys several roots in a row.
    import equation_tool.app as app_module
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

    set_input(app, "\\frac{a}")
    app.convert_now()
    assert pump(app, until=lambda: "unexpected eof" in app.output.get("1.0", "end"))
    assert app.output.tag_ranges("error")


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
