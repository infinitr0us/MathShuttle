"""High-level conversion API used by the GUI and the command line."""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import List, Optional

from . import latex_wrap
from .pandoc_engine import (READ_FORMATS, WRITE_FORMATS, ConversionError, Equation,  # noqa: F401
                            read_equations, write_equation)

FORMAT_LABELS = {
    "latex": "LaTeX",
    "mathml": "MathML",
    "omml": "OMML (Office Math)",
    "eqn": "eqn (groff)",
    "typst": "Typst",
}

MODES = [("auto", "Auto"), ("display", "Display"), ("inline", "Inline")]


@dataclass
class Result:
    equations: List[Equation]
    outputs: List[str]
    notes: List[str] = field(default_factory=list)

    @property
    def text(self) -> str:
        return latex_wrap.join_outputs(self.outputs)


def apply_mode(eqs: List[Equation], mode: str) -> List[Equation]:
    if mode == "display":
        return [Equation(e.tex, True) for e in eqs]
    if mode == "inline":
        return [Equation(e.tex, False) for e in eqs]
    return eqs


def convert(text: str, src: str, dst: str, mode: str = "auto", wrapper: str = "auto",
            annotate: bool = False) -> Result:
    """Convert `text` from format `src` to format `dst`.

    mode     "auto" (as detected from the input; bare LaTeX counts as display),
             "display" or "inline" - the mode used for the *output*.
    wrapper  how LaTeX output is delimited (see latex_wrap.WRAPPERS); ignored otherwise.
    annotate keep the TeX source as an <annotation> inside MathML output.
    """
    if src not in READ_FORMATS:
        raise ValueError("cannot read format %r" % src)
    if dst not in WRITE_FORMATS:
        raise ValueError("cannot write format %r" % dst)

    eqs = read_equations(text, src, default_display=(mode != "inline"))
    eqs = apply_mode(eqs, mode)
    notes: List[str] = []
    if len(eqs) > 1:
        notes.append("%d equations found in the input; each was converted separately." % len(eqs))

    outputs: List[str] = []
    numbering_conflict = False
    for i, eq in enumerate(eqs):
        if dst == "latex":
            display = not latex_wrap.wrapper_is_inline(wrapper, eq.display)
            outputs.append(latex_wrap.wrap(eq.tex, wrapper, display))
            numbering_conflict |= latex_wrap.numbering_conflict(eq.tex, wrapper)
            eqs[i] = Equation(eq.tex, display)  # the mode of the output, which the wrapper decides
        else:
            outputs.append(write_equation(eq, dst, annotate=annotate))
    if numbering_conflict:
        notes.append("Per-line \\tag / \\label commands were kept, but LaTeX allows no \\tag and at most one "
                     "\\label in this wrapper - edit them, or choose the align wrapper.")
    return Result(eqs, outputs, notes)


def mathml_for_office(text: str, src: str, mode: str = "auto") -> "tuple[str, Optional[str]]":
    """MathML (first equation) to place on the clipboard for Word/PowerPoint, plus a note."""
    eqs = apply_mode(read_equations(text, src, default_display=(mode != "inline")), mode)
    note = None
    if len(eqs) > 1:
        note = "%d equations in the input - only the first one was copied." % len(eqs)
    return write_equation(eqs[0], "mathml", for_office=True), note
