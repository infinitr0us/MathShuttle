"""Capture what Word puts on the clipboard for typical equations, as test fixtures.

    python tools/capture_word_fixtures.py

Each sample is typed into Word the way a user would (plain LaTeX text pasted into Word, which
Microsoft 365 >= 2606 converts into a native equation), then copied back: Word's MathML goes to
tests/fixtures/word/<name>.mathml.xml, the equation's OMML to <name>.omml.xml.  Needs pywin32 and
Word; overwrites the clipboard; Word runs hidden and the document is closed without saving.
"""
from __future__ import annotations

import os
import sys
import time

ROOT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..")
sys.path.insert(0, ROOT)

import win32com.client  # noqa: E402

from mathshuttle import clipboard  # noqa: E402

SAMPLES = {
    "rm_subscript": r"$P_{\rm{OOD}}^{(t)} = \frac{a}{b}$",
    "aligned": "\\[\\begin{aligned} a &= b + c \\\\ d &= e \\end{aligned}\\]",
    "softmax": r"$\operatorname{softmax}(z_i)$",
    "binom": r"$\binom{n}{k}$",
    "bold": r"$\boldsymbol{\theta}^\top \mathbf{x} + \bm{w}$",
    "cases": "\\[ f(x) = \\begin{cases} 1 & x > 0 \\\\ 0 & \\text{otherwise} \\end{cases} \\]",
    "display_sum": r"\[ \mathcal{L} = \frac{1}{n}\sum_{i=1}^{n} w_i \ell(x_i) \]",
    "inline_sum": r"$\sum_{i=1}^n x_i$",
    "differential": r"\[ \int_0^1 f(x)\,\mathrm{d}x \]",
}


def main() -> int:
    out_dir = os.path.join(ROOT, "tests", "fixtures", "word")
    os.makedirs(out_dir, exist_ok=True)
    word = win32com.client.DispatchEx("Word.Application")
    word.Visible = False
    doc = word.Documents.Add()
    try:
        print("Word build", word.Build)
        for name, latex in SAMPLES.items():
            doc.Content.Delete()
            for attempt in range(5):  # Word's Paste right after its own Copy can fail "clipboard empty"
                clipboard.set_text(latex)
                time.sleep(0.2)
                try:
                    doc.Range(0, 0).Paste()
                    break
                except Exception:
                    if attempt == 4:
                        raise
                    time.sleep(0.5)
            time.sleep(0.5)
            if doc.OMaths.Count != 1:
                print("  %s: Word made no equation of it, skipped" % name)
                continue
            om = doc.OMaths(1)
            xml = om.Range.WordOpenXML
            start = xml.find("<m:oMathPara")
            if start < 0:
                start = xml.find("<m:oMath")
            tag = "m:oMathPara" if xml.startswith("<m:oMathPara", start) else "m:oMath"
            end = xml.find("</%s>" % tag, start) + len("</%s>" % tag)
            om.Range.Select()
            word.Selection.Copy()
            time.sleep(0.5)
            mathml = clipboard.read_equation_from_office()
            if not mathml:
                print("  %s: no MathML on the clipboard, skipped" % name)
                continue
            with open(os.path.join(out_dir, name + ".mathml.xml"), "w", encoding="utf-8", newline="\n") as f:
                f.write("<!-- Word %s, typed as: %s -->\n%s\n" % (word.Build, latex.replace("--", "- -"), mathml.strip()))
            with open(os.path.join(out_dir, name + ".omml.xml"), "w", encoding="utf-8", newline="\n") as f:
                f.write("<!-- Word %s, typed as: %s -->\n%s\n" % (word.Build, latex.replace("--", "- -"), xml[start:end]))
            print("  %s: saved" % name)
    finally:
        doc.Close(0)
        word.Quit()
    return 0


if __name__ == "__main__":
    sys.exit(main())
