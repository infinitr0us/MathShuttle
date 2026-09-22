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


def is_running(exe: str) -> bool:
    out = subprocess.run(["tasklist", "/FI", f"IMAGENAME eq {exe}"], capture_output=True, text=True).stdout
    return exe.lower() in out.lower()


def show_clipboard():
    fmts = clipboard.available_formats()
    print("  clipboard formats:", [n for _, n in fmts])
    mathml = clipboard.read_equation_from_office()
    print("  MathML from clipboard:", (mathml or "")[:400].replace("\n", " "))
    return mathml


def test_word(mathml: str):
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

        om.Range.Select()
        word.Selection.Copy()
        time.sleep(0.5)
        mml_back = show_clipboard()
        assert mml_back, "Word did not put MathML on the clipboard"
        print("  MathML -> LaTeX:", [e.tex for e in read_equations(mml_back, "mathml")])
    finally:
        doc.Close(0)
        if not was_running:
            word.Quit()


def test_powerpoint(mathml: str):
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
            if mml_back:
                print("  MathML -> LaTeX:", [e.tex for e in read_equations(mml_back, "mathml")])
    finally:
        pres.Close()
        if not was_running:
            ppt.Quit()


if __name__ == "__main__":
    for tex in (TEX, ALIGN):
        print("#" * 70)
        print("LaTeX:", tex)
        eq = read_equations(tex, "latex")[0] if "\\begin" in tex else Equation(tex, True)
        mathml = write_equation(eq, "mathml")
        print("MathML:", mathml[:200].replace("\n", " "))
        test_word(mathml)
        test_powerpoint(mathml)
    print("done")
