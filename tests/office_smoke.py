"""Manual smoke test: round-trip an equation through real Word and PowerPoint via COM.

Requires pywin32 and Microsoft Office.  It overwrites the clipboard.

    python tests/office_smoke.py

Steps: LaTeX -> MathML (pandoc) -> clipboard -> paste into Word / PowerPoint ->
check that a native equation object was created -> copy it back -> inspect what
Office put on the clipboard -> convert back to LaTeX.
"""
from __future__ import annotations

import os
import subprocess
import sys
import time

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import win32com.client  # noqa: E402

from mathshuttle import clipboard  # noqa: E402
from mathshuttle.convert import convert, office_copy  # noqa: E402
from mathshuttle.pandoc_engine import read_equations  # noqa: E402

TEX = r"\frac{\partial f}{\partial x} + \sum_{i=1}^{n} \mathbf{x}_i^2 = \text{const}"
ALIGN = "\\begin{align}\na &= b + c \\\\\nd &= \\int_0^1 e^{-t}\\,dt\n\\end{align}"
# aligned blocks must paste as an equation array (m:eqArr, = signs lined up), not a centred matrix
# (pandoc_engine._office_equation_arrays), and come back as aligned
CONT = "\\begin{align}\nf &= a + b \\\\\n&\\quad + c \\\\\n&+ d\n\\end{align}"
ALIGN4 = "\\begin{align}\na &= b & c &= d \\\\\nee &= f & g &= hh\n\\end{align}"  # two alignment columns
EQNARRAY = "\\begin{eqnarray}\na &=& b + c \\\\\ndd &=& e\n\\end{eqnarray}"
CASES = "\\begin{equation}\nf = \\begin{cases} 1 & x > 0 \\\\ 0 & \\text{else} \\end{cases}\n\\end{equation}"  # stays a matrix
# texmath gives these ragged rows (a trailing \\, a line without &): padded for Office
RAGGED = "\\begin{align}\na &= b + c \\\\\nd &= e \\\\\n\\end{align}"
NO_AMP = "\\begin{align}\na &= b + c \\\\\n\\quad + d\n\\end{align}"
# an array becomes an equation array only when it reads like one
SYSTEM = "\\[\n\\left\\{\\begin{array}{rl} x + y &= 1 \\\\ x - y &= 0 \\end{array}\\right.\n\\]"
VALUES = "\\[\n\\begin{array}{rcl} 1 & 2 & 3 \\\\ 4 & 5 & 6 \\end{array}\n\\]"  # stays a matrix
EQUATION_ARRAYS = (ALIGN, CONT, ALIGN4, EQNARRAY, RAGGED, NO_AMP, SYSTEM)
# what comes back (Paste from Word/PowerPoint, tidy on) for equations whose LaTeX must survive exactly:
# bold italic symbols, names, binomials, a norm, plain-TeX switches.  Not \mathbf{x}: Word's MathML import
# makes every bold Latin letter bold italic (m:sty bi), whatever the MathML says - even <mi>𝐱</mi>, which
# is how Word itself exports \mathbf{x} (checked with Word 16.0.20527)
ROUND_TRIP = {
    "\\boldsymbol{\\theta}^\\top \\bm{w}": "\\boldsymbol{\\theta}^{\\top}\\boldsymbol{w}",
    # Word imports an operator name as upright text, not as a function (m:func): it comes back \mathrm
    "\\operatorname{softmax}(z) + \\binom{n}{k}": "\\mathrm{softmax}(z) + \\binom{n}{k}",
    # a norm is written with U+2016 now (texmath writes \| as U+2225); texmath reads a pair of them as \left\| \right\|
    "\\|v\\|_2 + P_{\\rm{OOD}}": "\\left\\| v \\right\\|_{2} + P_{\\mathrm{OOD}}",
}


def paste(do_paste):
    """Word's Paste right after its own Copy can fail with "clipboard empty or invalid": retry."""
    for attempt in range(5):
        try:
            return do_paste()
        except Exception:
            if attempt == 4:
                raise
            time.sleep(0.5)


def is_running(exe: str) -> bool:
    out = subprocess.run(["tasklist", "/FI", f"IMAGENAME eq {exe}"], capture_output=True, text=True).stdout
    return exe.lower() in out.lower()


def show_clipboard():
    fmts = clipboard.available_formats()
    print("  clipboard formats:", [n for _, n in fmts])
    mathml = clipboard.read_equation_from_office()
    print("  MathML from clipboard:", (mathml or "")[:400].replace("\n", " "))
    return mathml


def test_word(copy, eq_array: bool, expected_back=None):
    print("== Word")
    was_running = is_running("WINWORD.EXE")
    word = win32com.client.DispatchEx("Word.Application")
    word.Visible = False
    doc = word.Documents.Add()
    try:
        for attempt in range(2):
            # as the app copies it; the plain-text format holds the MathML too, since Word pastes from that one.
            # Right after another Office application owned the clipboard, Word occasionally pastes nothing.
            clipboard.copy_mathml_for_office(copy.mathml)
            time.sleep(0.3)
            paste(word.Selection.Paste)
            time.sleep(0.5)
            if doc.OMaths.Count:
                break
            print("  (nothing pasted, retrying once)")
            doc.Content.Delete()
        n = doc.OMaths.Count
        print("  OMaths.Count after paste:", n)
        assert n >= 1, "Word did not create a native equation from the MathML clipboard"
        om = doc.OMaths(1)
        print("  linear text:", om.Range.Text)
        xml = om.Range.WordOpenXML
        i = xml.find("<m:oMath")
        print("  OMML (WordOpenXML):", xml[i:i + 250].replace("\n", " "))
        print("  equation array (m:eqArr):", "<m:eqArr>" in xml, "  matrix (m:m):", "<m:m>" in xml)
        assert ("<m:eqArr>" in xml) == eq_array, "expected %s" % ("an equation array" if eq_array else "no equation array")

        om.Range.Select()
        word.Selection.Copy()
        time.sleep(0.5)
        mml_back = show_clipboard()
        assert mml_back, "Word did not put MathML on the clipboard"
        back = [e.tex for e in read_equations(mml_back, "mathml")]
        print("  MathML -> LaTeX:", back)
        assert ("\\begin{aligned}" in back[0]) == eq_array  # SYSTEM: inside \left\{ ... \right.
        if expected_back is not None:
            tidy_back = convert(mml_back, "mathml", "latex", wrapper="bare").text
            print("  Paste from Word (tidy):", tidy_back)
            assert tidy_back == expected_back
    finally:
        doc.Close(0)
        if not was_running:
            word.Quit()


def test_powerpoint(copy, eq_array: bool, expected_back=None):
    print("== PowerPoint")
    was_running = is_running("POWERPNT.EXE")
    ppt = win32com.client.DispatchEx("PowerPoint.Application")
    pres = ppt.Presentations.Add(0)  # WithWindow = msoFalse
    try:
        slide = pres.Slides.Add(1, 12)  # ppLayoutBlank
        shape = slide.Shapes.AddTextbox(1, 50, 50, 500, 100)
        shape.TextFrame.TextRange.Text = ""
        for attempt in range(2):
            # right after Word (the previous clipboard owner) quits, PowerPoint occasionally pastes nothing
            clipboard.copy_mathml_for_office(copy.mathml)
            time.sleep(0.3)
            paste(shape.TextFrame.TextRange.Paste)
            time.sleep(0.5)
            if shape.TextFrame2.TextRange.MathZones.Count:
                break
            print("  (nothing pasted, retrying once)")
            shape.TextFrame.TextRange.Text = ""
        tr2 = shape.TextFrame2.TextRange
        print("  MathZones.Count after paste into textbox:", tr2.MathZones.Count)
        print("  text:", tr2.Text[:100])
        assert tr2.MathZones.Count >= 1, "PowerPoint did not create a native equation from the MathML clipboard"
        tr2.MathZones.Item(1).Copy()
        time.sleep(0.5)
        mml_back = show_clipboard()
        assert mml_back, "PowerPoint did not put MathML on the clipboard"
        print("  MathML -> LaTeX:", [e.tex for e in read_equations(mml_back, "mathml")])
        # an equation array is exported as a table without column gaps
        assert ('columnspacing="0em"' in mml_back) == eq_array
        if expected_back is not None:
            tidy_back = convert(mml_back, "mathml", "latex", wrapper="bare").text
            print("  Paste from PowerPoint (tidy):", tidy_back)
            assert tidy_back == expected_back
    finally:
        pres.Close()
        if not was_running:
            ppt.Quit()


if __name__ == "__main__":
    for stream in (sys.stdout, sys.stderr):  # MathML holds ∂, 𝐱...; a cp936 / cp1252 console cannot print them
        stream.reconfigure(encoding="utf-8", errors="replace")
    for tex in (TEX, ALIGN, CONT, ALIGN4, EQNARRAY, CASES, RAGGED, NO_AMP, SYSTEM, VALUES, *ROUND_TRIP):
        print("#" * 70)
        print("LaTeX:", tex)
        copy = office_copy(tex, "latex")  # what "Copy for Word/PowerPoint" puts there
        print("MathML:", copy.mathml[:200].replace("\n", " "))
        test_word(copy, tex in EQUATION_ARRAYS, ROUND_TRIP.get(tex))
        test_powerpoint(copy, tex in EQUATION_ARRAYS, ROUND_TRIP.get(tex))
    print("done")
