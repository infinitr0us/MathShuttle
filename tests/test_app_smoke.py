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
    assert str(app.status_label.cget("foreground")) == app.palette["error"]  # errors in red, not like a success


@pytest.mark.skipif(sys.platform not in ("win32", "darwin"), reason="no clipboard backend here")
def test_copy_for_office_and_paste_back(app):
    app.src_var.set("latex")
    app.dst_var.set("omml")
    set_input(app, "\\begin{align} a &= b \\\\ c &= d \\end{align}")
    app.copy_for_office()
    import mathshuttle.app as app_module
    assert app_module.KEY + "+V" in app.status_var.get()  # Ctrl+V on Windows, Cmd+V on macOS
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


def test_copy_for_office_lets_you_pick_the_equation(app, monkeypatch):
    import tkinter as tk
    copied, popups = [], []
    monkeypatch.setattr(clipboard, "copy_mathml_for_office", lambda mathml, *a, **k: copied.append(mathml))
    monkeypatch.setattr(tk.Menu, "tk_popup", lambda menu, x, y, entry=None: popups.append(menu))
    app.src_var.set("latex")
    set_input(app, "$a$ and $\\frac{b}{2}$ and \\[c^2\\]")
    app.copy_for_office()
    menu = popups[-1]
    labels = [menu.entrycget(i, "label") for i in (0, 2, 3, 4)]
    assert labels == ["Next:   1   a", "1   a", "2   \\frac{b}{2}", "3   c^2"]
    assert menu.type(1) == "separator" and menu.entrycget(0, "underline") == 0  # N, and 1-9
    assert menu.entrycget(3, "underline") == 0 and not copied  # nothing copied before a choice
    menu.invoke(3)
    assert "<mfrac>" in copied[-1] and "Equation 2 of the 3" in app.status_var.get()
    app.copy_for_office()  # Next is the one after: equation 3
    assert popups[-1].entrycget(0, "label") == "Next:   3   c^2"
    popups[-1].invoke(0)
    assert "<msup>" in copied[-1]
    app.copy_for_office()
    assert popups[-1].entrycget(0, "label").startswith("Next:   1")  # after the last, the first again
    set_input(app, "$x$ and $y$")  # other input: from the first
    app.copy_for_office()
    assert popups[-1].entrycget(0, "label") == "Next:   1   x"
    set_input(app, "$x$")  # one equation: copied at once, no menu
    count = len(popups)
    app.copy_for_office()
    assert len(popups) == count and "<mi>x</mi>" in copied[-1] and "of the" not in app.status_var.get()


def test_equation_menu_copies_what_it_shows(app, monkeypatch):
    import tkinter as tk
    import mathshuttle.convert as convert_module
    copied, popups, reads = [], [], []
    monkeypatch.setattr(clipboard, "copy_mathml_for_office", lambda mathml, *a, **k: copied.append(mathml))
    monkeypatch.setattr(tk.Menu, "tk_popup", lambda menu, x, y, entry=None: popups.append(menu))
    real_read = convert_module._read
    monkeypatch.setattr(convert_module, "_read", lambda *a, **k: (reads.append(1), real_read(*a, **k))[1])
    app.src_var.set("latex")
    set_input(app, "$a$ and $\\frac{b}{2}$")
    app.copy_for_office()
    first = popups[-1]
    reads.clear()
    app.src_var.set("mathml")  # changed after the menu opened: the menu still copies what it showed
    first.invoke(3)
    assert "<mfrac>" in copied[-1] and not reads  # and without reading the input again
    app.src_var.set("latex")
    app.copy_for_office()
    assert not first.winfo_exists() and popups[-1].winfo_exists()  # one menu at a time


def test_equation_menu_starts_again_after_a_macro_file_changes(app, monkeypatch, tmp_path):
    import os
    import tkinter as tk
    copied, popups = [], []
    monkeypatch.setattr(clipboard, "copy_mathml_for_office", lambda mathml, *a, **k: copied.append(mathml))
    monkeypatch.setattr(tk.Menu, "tk_popup", lambda menu, x, y, entry=None: popups.append(menu))
    macros = tmp_path / "macros.tex"
    macros.write_text("\\newcommand{\\vv}{\\mathbf{v}}\n", encoding="utf-8")
    monkeypatch.setattr(app, "macro_files", [str(macros)])
    app.src_var.set("latex")
    set_input(app, "$a$ and $\\vv$ and $c$")
    app.copy_for_office()
    popups[-1].invoke(0)  # equation 1
    app.copy_for_office()
    assert popups[-1].entrycget(0, "label") == "Next:   2   \\vv"
    macros.write_text("\\newcommand{\\vv}{\\vec{v}}\n", encoding="utf-8")  # the same name, another definition
    os.utime(macros, ns=(macros.stat().st_atime_ns, macros.stat().st_mtime_ns + 10**9))
    app.copy_for_office()
    assert popups[-1].entrycget(0, "label") == "Next:   1   a"  # an edited macro file: from the first
    popups[-1].invoke(3)
    assert "→" in copied[-1] or "&#x2192;" in copied[-1]  # with the new definition


def test_paste_from_office_takes_omml_from_the_html(app, monkeypatch):
    # what Word copies for a sentence with an equation in it: no MathML, the OMML in its HTML
    omml = '<m:oMath><m:f><m:num><m:r><m:t>a</m:t></m:r></m:num><m:den><m:r><m:t>b</m:t></m:r></m:den></m:f></m:oMath>'
    monkeypatch.setattr(clipboard, "read_office_equation", lambda: ("omml", omml))
    app.src_var.set("latex")
    app.dst_var.set("omml")
    app.paste_from_office()
    assert app.src_var.get() == "omml" and app.dst_var.get() == "latex"  # not OMML -> OMML
    assert app.input.get("1.0", "end").strip() == omml
    assert pump(app, until=lambda: app.output.get("1.0", "end").strip() == "$\\frac{a}{b}$")
    monkeypatch.setattr(clipboard, "read_office_equation", lambda: None)
    app.paste_from_office()
    assert "No equation on the clipboard" in app.status_var.get()


def test_paste_from_office_gives_back_the_latex_it_was_copied_from(app, monkeypatch):
    copied = []
    monkeypatch.setattr(clipboard, "copy_mathml_for_office", lambda mathml, *a, **k: copied.append(mathml))
    app.src_var.set("latex")
    app.dst_var.set("mathml")
    tex = "\\[ \\operatorname{softmax}(z)_i = \\frac{e^{z_i}}{\\sum_j e^{z_j}} \\label{eq:sm} \\]"
    set_input(app, tex)
    app.copy_for_office()
    assert copied
    monkeypatch.setattr(clipboard, "read_office_equation", lambda: ("mathml", copied[-1]))
    app.paste_from_office()
    assert app.dst_var.get() == "latex"
    assert pump(app, until=lambda: "\\label{eq:sm}" in app.output.get("1.0", "end"))
    assert "unchanged" in app.status_var.get()
    app.wrapper_var.set("ddollar")  # another wrapper: the same LaTeX in it
    app.convert_now()
    assert pump(app, until=lambda: app.output.get("1.0", "end").startswith("$$"))
    assert "\\label{eq:sm}" in app.output.get("1.0", "end")
    app.wrapper_var.set("auto")
    app.tidy_var.set(False)  # texmath's own LaTeX asked for: no recall
    try:
        app.convert_now()
        assert pump(app, until=lambda: "softmax" in app.output.get("1.0", "end")
                    and "\\label" not in app.output.get("1.0", "end"))
    finally:
        app.tidy_var.set(True)
    set_input(app, copied[-1].replace("<mi>z</mi>", "<mi>y</mi>"))  # edited: converted as usual
    app.convert_now()
    assert pump(app, until=lambda: "y" in app.output.get("1.0", "end") and "softmax" in app.output.get("1.0", "end"))
    assert "\\label" not in app.output.get("1.0", "end") and "unchanged" not in app.status_var.get()


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
        import mathshuttle.app as app_module
        assert applied == [app_module.DEFAULT_GEOMETRY, "1100x600", "1100x600+100+100", "1100x600"]
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


def test_open_document_lists_and_uses_equations(app, tmp_path):
    """Open file...: the equations of a presentation, one of them used as input, all exported."""
    docs = os.path.join(os.path.dirname(__file__), "fixtures", "documents")
    opened = []
    app._show_equations = lambda doc, show=app._show_equations: opened.append(show(doc)) or opened[-1]
    try:
        app.open_document(os.path.join(docs, "sample.pptx"))
        assert pump(app, seconds=20, until=lambda: opened)
        win = opened[0]
        assert win.tree.get_children() == ("0", "1")
        assert win.tree.item("1")["values"][0] == "slide 2: Update rule"
        app.dst_var.set("mathml")
        win.tree.selection_set("1")
        win.use()
        assert app.src_var.get() == "latex"
        assert app.input.get("1.0", "end-1c").startswith(r"\[ \theta_{t + 1}")
        assert pump(app, until=lambda: "<math" in app.output.get("1.0", "end"))
        target = tmp_path / "out.tex"
        win.export(str(target))
        text = target.read_text(encoding="utf-8")
        assert "% [1] slide 1: Objective" in text and "% [2] slide 2: Update rule" in text
        word = tmp_path / "out.docx"
        win.export_docx(str(word))  # in the background
        assert pump(app, seconds=20, until=lambda: "2 equations written to" in win.info.get())
        from mathshuttle.documents import read_document
        assert len(read_document(str(word)).found) == 2
        win.destroy()
    finally:
        del app._show_equations


def test_whole_tex_file_as_word(app, tmp_path):
    """The document window of a .tex file writes the whole paper as a Word file; of a .pptx, it has no such button."""
    import zipfile
    from mathshuttle.documents import DocumentEquations, Found, read_document
    from mathshuttle.pandoc_engine import Equation
    paper = tmp_path / "paper.tex"
    paper.write_text("\\documentclass{article}\n\\newcommand{\\vv}{\\mathbf{v}}\n\\begin{document}\n"
                     "\\section{Model}\nThe update is\n\\begin{equation} \\vv_{t+1} = \\vv_t - \\eta g \\label{eq:up}"
                     "\\end{equation}\nand \\eqref{eq:up} converges.\n\\end{document}\n", encoding="utf-8")
    (tmp_path / "paper.aux").write_text("\\newlabel{eq:up}{{1.3}{2}}\n", encoding="utf-8")  # LaTeX's numbers
    win = app._show_equations(read_document(str(paper)))
    try:
        buttons = [w.cget("text") for w in win.winfo_children()[-1].winfo_children()]
        assert "Whole file as Word..." in buttons
        out = tmp_path / "paper.docx"
        win.save_document_as_word(str(out))  # in the background
        assert pump(app, seconds=30, until=lambda: "Written: paper.docx" in win.info.get())
        xml = zipfile.ZipFile(str(out)).read("word/document.xml").decode("utf-8")
        assert "<m:oMath" in xml and "#(1.3)" in xml and "(1.3)" in xml.split("#(1.3)")[1].split("converges")[0]
        win.save_document_as_word(str(paper))
        assert "another file name" in win.info.get() and "documentclass" in paper.read_text(encoding="utf-8")
    finally:
        win.destroy()
    win = app._show_equations(DocumentEquations("talk.pptx", "pptx", [Found(Equation("x"), "slide 1")]))
    try:
        assert "Whole file as Word..." not in [w.cget("text") for w in win.winfo_children()[-1].winfo_children()]
    finally:
        win.destroy()


def test_large_document_filters_selection_and_exports(app, tmp_path):
    from mathshuttle.documents import DocumentEquations, Found
    from mathshuttle.pandoc_engine import Equation

    found = [Found(Equation("x", False), "Intro") for _ in range(2996)]
    found += [Found(Equation("x", True), "Results"), Found(Equation("x", True), "Results"),
              Found(Equation(r"Y\label{eq:y}"), "Results  [eq:y]"), Found(Equation("z", True), "Appendix")]
    win = app._show_equations(DocumentEquations("thesis.tex", "latex", found, labels={"eq:y": "2.1"}))
    try:
        assert win.display_var.get() and str(win.tree.cget("selectmode")) == "extended"
        assert win.tree.get_children() == ("2996", "2997", "2998", "2999")
        win.filter_var.set("rEsUlTs")
        win.distinct_var.set(True)
        app.update_idletasks()
        assert win.tree.get_children() == ("2996", "2998")
        assert win.tree.set("2996", "where") == "Results  (2x)"
        assert win.tree.set("2998", "where") == "Results  [eq:y]  (2.1)  (1x)"
        win.tree.selection_set("2998", "2996")
        assert win._selected() is found[2996]
        selected = tmp_path / "selection.tex"
        win.export_selected(str(selected))
        text = selected.read_text(encoding="utf-8")
        assert "% [2997] Results  (2x)\n\\[ x \\]" in text
        assert "% [2999] Results  [eq:y]  (2.1)  (1x)" in text
        assert text.count("% [") == 2 and "2 equations written" in win.info.get()
        all_rows = tmp_path / "all.tex"
        win.export(str(all_rows))
        assert all_rows.read_text(encoding="utf-8").count("% [") == 3000
        win.filter_var.set("y\\LABEL")
        app.update_idletasks()
        assert win.tree.selection() == ("2998",)
        win.filter_var.set("absent")
        app.update_idletasks()
        assert not win.tree.get_children()
        win.export_selected(str(tmp_path / "empty.tex"))
        assert "Select an equation" in win.info.get() and not (tmp_path / "empty.tex").exists()
        win.filter_var.set("")
        win.distinct_var.set(False)
        win.display_var.set(False)
        app.update_idletasks()
        assert len(win.tree.get_children()) == 3000
        assert win.tree.set("2996", "where") == "Results"
    finally:
        win.destroy()


@pytest.mark.skipif(sys.platform not in ("win32", "darwin"), reason="no hotkey here")
def test_copy_for_office_hotkey_round_trip(app):
    """The hotkey (a simulated press: no keystroke is sent): LaTeX text on the clipboard becomes an
    equation for Office, and pressing again turns it back into LaTeX text.  Overwrites the clipboard."""
    from mathshuttle import hotkeys
    # the window's start-up check (50 ms after it opens) registers the hotkey itself: let it run first,
    # or it replaces the listener below (run alone, this test is the first to run Tk's events)
    assert pump(app, until=lambda: app.engine_var.get())
    app.src_var.set("latex")
    # not the user's key, which a running MathShuttle may hold (the Mac has no F21-F24)
    app.hotkey = "Ctrl+Alt+Shift+F23" if sys.platform == "win32" else "Ctrl+Alt+Shift+F18"
    app.hotkey_var.set(True)
    app._toggle_hotkey()
    assert app._listener is not None, app.status_var.get()
    try:
        clipboard.set_text(r"\frac{a}{b}")
        app._listener.simulate_press()
        assert pump(app, until=lambda: "Equation ready" in app.status_var.get())
        assert "<mfrac" in (clipboard.read_equation_from_office() or "")
        app._listener.simulate_press()
        assert pump(app, until=lambda: "LaTeX copied" in app.status_var.get())
        assert clipboard.get_text().strip() == r"\[ \frac{a}{b} \]"
        clipboard.set_text("")
        results = []
        app.shuttle_clipboard(done=lambda message, failed: results.append(failed))
        assert pump(app, until=lambda: results) and results == [True]
    finally:
        app.hotkey_var.set(False)
        app._toggle_hotkey()
        app.hotkey = hotkeys.DEFAULT


@pytest.mark.skipif(sys.platform not in ("win32", "darwin"), reason="no clipboard backend here")
def test_shuttle_clipboard_without_the_hotkey(app):
    """What the hotkey does, called directly (there is no hotkey on macOS yet): LaTeX text becomes an
    equation for Office and back.  Overwrites the clipboard; reads only what this process wrote."""
    import mathshuttle.app as app_module
    app.src_var.set("latex")
    clipboard.set_text(r"\frac{a}{b}")
    results = []
    app.shuttle_clipboard(done=lambda message, failed: results.append((message, failed)))
    assert pump(app, until=lambda: results)
    assert results[-1] == ("Equation ready: press %s+V in Word / PowerPoint" % app_module.KEY, False)
    assert "<mfrac" in (clipboard.read_equation_from_office() or "")
    app.shuttle_clipboard(done=lambda message, failed: results.append((message, failed)))
    assert pump(app, until=lambda: len(results) == 2)
    assert results[-1][1] is False and clipboard.get_text().strip() == r"\[ \frac{a}{b} \]"


@pytest.mark.skipif(sys.platform != "darwin", reason="macOS")
def test_macos_window(app, tmp_path, monkeypatch):
    """The hotkey box names the keys the Mac way and saves the user's choice; Command keys; the
    settings file under Application Support; a monospace font that exists; no Windows key names in
    the help."""
    import json
    from tkinter import font as tkfont
    import mathshuttle.app as app_module
    assert str(app.hotkey_chk.cget("state")) != "disabled"
    assert app.hotkey_chk.cget("text") == "Hotkey Control+Option+Shift+M"
    monkeypatch.setattr(app_module, "SETTINGS_PATH", str(tmp_path / "settings.json"))
    app.hotkey_var.set(False)
    app._save_settings()
    with open(app_module.SETTINGS_PATH, encoding="utf-8") as f:
        assert json.load(f)["hotkey_enabled"] is False

    assert app_module.APP_DATA == os.path.expanduser("~/Library/Application Support")
    assert app_module.KEY == "Cmd" and "Ctrl" not in app_module.HELP and "APPDATA" not in app_module.HELP
    assert app.mono.actual("family") == "Menlo" and "Menlo" in tkfont.families(app)
    assert app.mono.metrics("linespace") <= 1.4 * tkfont.nametofont("TkDefaultFont").metrics("linespace")
    assert app.aqua
    for sequence in ("<Command-Shift-Key-C>", "<Command-Shift-Key-V>", "<Command-Key-Return>"):
        assert app.bind_all(sequence), sequence
    assert app.input.bind("<Command-Key-a>") and app.input.bind("<Command-Key-Return>")
    assert app.tk.call("info", "commands", "::tk::mac::Quit") and app.tk.call("info", "commands", "::tk::mac::ShowHelp")


def test_open_document_reports_errors(app, tmp_path):
    bad = tmp_path / "broken.pptx"
    bad.write_bytes(b"not a zip")
    app.open_document(str(bad))
    assert pump(app, until=lambda: "Could not read" in app.status_var.get())


def test_preview_shows_the_latex(app):
    from mathshuttle import preview
    if preview.tools() is None:
        pytest.skip("no LaTeX installation on PATH")
    assert pump(app, seconds=20, until=lambda: app._latex_found is not None)
    app.preview_var.set(True)
    app._toggle_preview()
    app.src_var.set("latex")
    app.dst_var.set("mathml")
    set_input(app, "$x^2 + y^2$")
    app.convert_now()
    assert pump(app, seconds=30, until=lambda: app._preview_image is not None)
    set_input(app, r"\frac{a}")  # an error clears the picture
    app.convert_now()
    assert pump(app, until=lambda: app._preview_image is None)


def test_pandoc_missing_offers_a_download(app, monkeypatch, tmp_path):
    # the start-up check finds no pandoc: a strip offers the download; Download installs (here: a stand-in
    # for pandoc_fetch.install that reports progress) and the app looks for pandoc again
    from mathshuttle import pandoc_engine, pandoc_fetch
    monkeypatch.delenv("MATHSHUTTLE_PANDOC", raising=False)
    monkeypatch.delenv("EQUATION_TOOL_PANDOC", raising=False)
    real_find = pandoc_engine.find_pandoc
    state = {"installed": False, "resets": 0}

    def find():
        if not state["installed"]:
            raise pandoc_engine.PandocNotFound("pandoc.exe was not found.")
        return real_find()

    def install(progress=None, cancel=None):
        for done in (0, 20_000_000, 40_000_000):
            progress(done, 40_000_000)
            time.sleep(0.05)
        state["installed"] = True
        return str(tmp_path / "pandoc.exe")

    def reset():
        state["resets"] += 1

    import mathshuttle.app as app_module
    monkeypatch.setattr(app_module, "engine_description", lambda: find() and "pandoc (test)")
    monkeypatch.setattr(pandoc_fetch, "install", install)
    monkeypatch.setattr(pandoc_engine, "reset_pandoc", reset)
    app._startup_check()
    assert app._banner is not None and "not installed" in app._banner_text.get()
    assert app._fetch_button.cget("text") == "Download pandoc"
    app._fetch_button.invoke()
    assert pump(app, until=lambda: app._banner is None)
    assert state["resets"] == 1
    assert app.engine_var.get() == "pandoc (test)"
    assert "pandoc 3.14 installed" in app.status_var.get()


def test_pandoc_download_failure_can_be_retried(app, monkeypatch):
    from mathshuttle import pandoc_engine, pandoc_fetch
    monkeypatch.delenv("MATHSHUTTLE_PANDOC", raising=False)
    monkeypatch.delenv("EQUATION_TOOL_PANDOC", raising=False)
    import mathshuttle.app as app_module

    def missing():
        raise pandoc_engine.PandocNotFound("pandoc.exe was not found.")

    def install(progress=None, cancel=None):
        raise pandoc_fetch.FetchError("Could not download pandoc. Check your connection and try again.")

    monkeypatch.setattr(app_module, "engine_description", missing)
    monkeypatch.setattr(pandoc_fetch, "install", install)
    app._startup_check()
    app._fetch_button.invoke()
    assert pump(app, until=lambda: app._fetch_button.cget("text") == "Try again")
    assert "Check your connection" in app._banner_text.get()
    app._banner.destroy()
    app._banner = None


def test_a_theme_set_by_hand_is_kept(app):
    import json
    app.settings["theme"] = "dark"
    try:
        app._save_settings()
        with open(SETTINGS_TMP, encoding="utf-8") as f:
            assert json.load(f)["theme"] == "dark"
    finally:
        del app.settings["theme"]


def test_theme_choice():
    from mathshuttle import theme
    assert theme.resolve("native") == "native"
    if sys.platform != "darwin":
        assert theme.resolve("dark") == "dark" and theme.resolve("light") == "light"
        assert theme.resolve(None) in ("light", "dark")
    else:  # Aqua follows the system by itself
        assert theme.resolve("dark") == "native"


def test_expand_macros_in_the_window(app):
    app.src_var.set("latex")
    app.dst_var.set("latex")
    app.wrapper_var.set("bare")
    app.update_idletasks()
    assert str(app.expand_chk.cget("state")) == "normal"
    set_input(app, r"\newcommand{\R}{\mathbb{R}}" + "\n" + r"$x \in \R$")
    app.expand_var.set(True)
    try:
        app.convert_now()
        assert pump(app, until=lambda: r"\mathbb{R}" in app.output.get("1.0", "end"))
        assert r"\R" not in app.output.get("1.0", "end")
    finally:
        app.expand_var.set(False)
    app.dst_var.set("mathml")
    app.update_idletasks()
    assert str(app.expand_chk.cget("state")) == "disabled"


def test_macros_dialog_opens_in_the_theme(app):
    # a Listbox takes no text-cursor colours: the dialog used to stop half-built in the Sun Valley look
    before = set(app.winfo_children())
    app.edit_macros()
    app.update_idletasks()
    new = [w for w in app.winfo_children() if w not in before]
    try:
        assert len(new) == 1 and new[0].title() == "MathShuttle macros"
        kinds = {w.winfo_class() for w in new[0].winfo_children()}
        assert {"Listbox", "Text"} <= kinds
    finally:
        for w in new:
            w.destroy()
