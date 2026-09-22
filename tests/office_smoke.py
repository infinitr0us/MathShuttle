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

from equation_tool import clipboard  # noqa: E402
from equation_tool.pandoc_engine import Equation, read_equations, write_equation  # noqa: E402

TEX = r"\frac{\partial f}{\partial x} + \sum_{i=1}^{n} \mathbf{x}_i^2 = \text{const}"
ALIGN = "\\begin{align}\na &= b + c \\\\\nd &= \\int_0^1 e^{-t}\\,dt\n\\end{align}"
# aligned blocks must paste as an equation array (m:eqArr, = signs lined up), not a centred matrix
# (pandoc_engine._office_equation_arrays), and come back as aligned
CONT = "\\begin{align}\nf &= a + b \\\\\n&\\quad + c \\\\\n&+ d\n\\end{align}"
ALIGN4 = "\\begin{align}\na &= b & c &= d \\\\\nee &= f & g &= hh\n\\end{align}"  # two alignment columns
EQNARRAY = "\\begin{eqnarray}\na &=& b + c \\\\\ndd &=& e\n\\end{eqnarray}"
CASES = "\\begin{equation}\nf = \\begin{cases} 1 & x > 0 \\\\ 0 & \\text{else} \\end{cases}\n\\end{equation}"  # stays a matrix
EQUATION_ARRAYS = (ALIGN, CONT, ALIGN4, EQNARRAY)


def is_running(exe: str) -> bool:
    out = subprocess.run(["tasklist", "/FI", f"IMAGENAME eq {exe}"], capture_output=True, text=True).stdout
    return exe.lower() in out.lower()


def show_clipboard():
    fmts = clipboard.available_formats()
    print("  clipboard formats:", [n for _, n in fmts])
    mathml = clipboard.read_equation_from_office()
    print("  MathML from clipboard:", (mathml or "")[:400].replace("\n", " "))
    return mathml


def test_word(mathml: str, eq_array: bool):
    print("== Word")
    was_running = is_running("WINWORD.EXE")
    word = win32com.client.DispatchEx("Word.Application")
    word.Visible = False
    doc = word.Documents.Add()
    try:
        clipboard.copy_mathml_for_office(mathml)
        word.Selection.Paste()
        time.sleep(0.5)
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
        assert back[0].startswith("\\begin{aligned}") == eq_array
    finally:
        doc.Close(0)
        if not was_running:
            word.Quit()


def test_powerpoint(mathml: str, eq_array: bool):
    print("== PowerPoint")
    was_running = is_running("POWERPNT.EXE")
    ppt = win32com.client.DispatchEx("PowerPoint.Application")
    pres = ppt.Presentations.Add(0)  # WithWindow = msoFalse
    try:
        slide = pres.Slides.Add(1, 12)  # ppLayoutBlank
        shape = slide.Shapes.AddTextbox(1, 50, 50, 500, 100)
        shape.TextFrame.TextRange.Text = ""
        clipboard.copy_mathml_for_office(mathml)
        shape.TextFrame.TextRange.Paste()
        time.sleep(0.5)
        tr2 = shape.TextFrame2.TextRange
        print("  MathZones.Count after paste into textbox:", tr2.MathZones.Count)
        print("  text:", tr2.Text[:100])
        if tr2.MathZones.Count:
            tr2.MathZones.Item(1).Copy()
            time.sleep(0.5)
            mml_back = show_clipboard()
            assert mml_back, "PowerPoint did not put MathML on the clipboard"
            print("  MathML -> LaTeX:", [e.tex for e in read_equations(mml_back, "mathml")])
            # an equation array is exported as a table without column gaps
            assert ('columnspacing="0em"' in mml_back) == eq_array
    finally:
        pres.Close()
        if not was_running:
            ppt.Quit()


if __name__ == "__main__":
    for tex in (TEX, ALIGN, CONT, ALIGN4, EQNARRAY, CASES):
        print("#" * 70)
        print("LaTeX:", tex)
        eq = read_equations(tex, "latex")[0] if "\\begin" in tex else Equation(tex, True)
        mathml = write_equation(eq, "mathml", for_office=True)  # what "Copy for Word/PowerPoint" puts there
        print("MathML:", mathml[:200].replace("\n", " "))
        test_word(mathml, tex in EQUATION_ARRAYS)
        test_powerpoint(mathml, tex in EQUATION_ARRAYS)
    print("done")
