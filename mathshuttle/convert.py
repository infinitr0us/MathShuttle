"""High-level conversion API used by the GUI and the command line."""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import List, Optional

from . import compat, latex_wrap, tidy as _tidy
from .pandoc_engine import (READ_FORMATS, WRITE_FORMATS, ConversionError, Equation,  # noqa: F401
                            read_equations, write_equation, write_mathml)

FORMAT_LABELS = {
    "latex": "LaTeX",
    "mathml": "MathML",
    "omml": "OMML (Office Math)",
    "eqn": "eqn (groff)",
    "typst": "Typst",
}

MODES = [("auto", "Auto"), ("display", "Display"), ("inline", "Inline")]

_TAG_NOTE = "\\tag numbers are not carried over (Word / PowerPoint number equations themselves)."


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


def _add(notes: List[str], new: List[str]) -> None:
    for n in new:
        if n not in notes:
            notes.append(n)


def macro_notes(macros: Optional[compat.Macros]) -> List[str]:
    """What went wrong while loading the macro files."""
    if macros is None:
        return []
    notes = list(macros.errors)
    if macros.skipped:
        notes.append("macros not usable (delimited \\def parameters or malformed): " +
                     ", ".join(sorted(set(macros.skipped))))
    return notes


def tidied(eq: Equation, display: Optional[bool] = None) -> Equation:
    """An equation read from MathML / OMML with texmath's TeX cleaned up (:func:`tidy.tidy_tex`) for
    output in `display` mode (default: the equation's); if the cleaned TeX no longer converts, texmath's."""
    tex = _tidy.tidy_tex(eq.tex, eq.display if display is None else display)
    if tex != eq.tex:
        try:
            write_mathml(Equation(compat.for_texmath(tex)[0], eq.display))
        except ConversionError:
            tex = eq.tex
    return Equation(tex, eq.display)


def for_texmath(eq: Equation, src: str, macros: Optional[compat.Macros],
                target: str = "mathml") -> "tuple[Equation, List[str]]":
    """The equation as texmath gets it, through :func:`compat.for_texmath` (plain-TeX font switches,
    \\notag, macros, texmath's symbol bugs...).  The user's macros apply to LaTeX input only; LaTeX
    read from MathML / OMML (texmath's or tidied) still needs the symbol and bold corrections."""
    tex, notes = compat.for_texmath(eq.tex, macros if src == "latex" else None, target)
    return Equation(tex, eq.display), notes if src == "latex" else []


def _read(text: str, src: str, mode: str, tidy: bool) -> List[Equation]:
    return apply_mode(read_equations(text, src, default_display=(mode != "inline"), tidy=tidy), mode)


def convert(text: str, src: str, dst: str, mode: str = "auto", wrapper: str = "auto",
            annotate: bool = False, macros: Optional[compat.Macros] = None, tidy: bool = True) -> Result:
    """Convert `text` from format `src` to format `dst`.

    mode     "auto" (as detected from the input; bare LaTeX counts as display),
             "display" or "inline" - the mode used for the *output*.
    wrapper  how LaTeX output is delimited (see latex_wrap.WRAPPERS); ignored otherwise.
    annotate keep the TeX source as an <annotation> inside MathML output.
    macros   definitions applied to LaTeX input (:class:`compat.Macros`); LaTeX -> LaTeX keeps the macros.
    tidy     write LaTeX from MathML / OMML input as an author would (:mod:`tidy`).
    """
    if src not in READ_FORMATS:
        raise ValueError("cannot read format %r" % src)
    if dst not in WRITE_FORMATS:
        raise ValueError("cannot write format %r" % dst)

    eqs = _read(text, src, mode, tidy)
    notes: List[str] = []
    if len(eqs) > 1:
        notes.append("%d equations found in the input; each was converted separately." % len(eqs))

    outputs: List[str] = []
    numbering_conflict = False
    for i, eq in enumerate(eqs):
        if dst == "latex":
            display = not latex_wrap.wrapper_is_inline(wrapper, eq.display)
            if tidy and src != "latex":
                eq = tidied(eq, display)  # for the mode of the output: \limits stays in $...$
            outputs.append(latex_wrap.wrap(eq.tex, wrapper, display))
            numbering_conflict |= latex_wrap.numbering_conflict(eq.tex, wrapper, display)
            # the mode of the output, which the wrapper decides (Auto: a kept environment)
            eqs[i] = Equation(eq.tex, latex_wrap.output_is_display(eq.tex, wrapper, display))
        else:
            if tidy and src != "latex":
                eqs[i] = eq = tidied(eq)
            prepared, new = for_texmath(eq, src, macros, dst)
            _add(notes, new)
            outputs.append(write_equation(prepared, dst, annotate=annotate, source=eq.tex))
            if src == "latex" and re.search(r"\\tag(?![a-zA-Z@])", eq.tex):
                _add(notes, [_TAG_NOTE])
    if numbering_conflict:
        notes.append("\\tag / \\label commands were kept, but LaTeX allows no \\tag and at most one "
                     "\\label in this wrapper - edit them, or choose the align wrapper.")
    if src == "latex" and dst != "latex":
        _add(notes, macro_notes(macros))
    return Result(eqs, outputs, notes)


@dataclass
class OfficeCopy:
    mathml: str           # for the clipboard: Word / PowerPoint paste a native equation
    latex: str            # the same (first) equation as LaTeX.  Not for the clipboard's plain-text
    #                       format: Word pastes from that one (see clipboard.copy_mathml_for_office)
    notes: List[str] = field(default_factory=list)


def office_copy(text: str, src: str, mode: str = "auto", macros: Optional[compat.Macros] = None,
                tidy: bool = True) -> OfficeCopy:
    """What "Copy for Word/PowerPoint" puts on the clipboard (first equation of the input)."""
    eqs = _read(text, src, mode, tidy)
    notes: List[str] = []
    if len(eqs) > 1:
        notes.append("%d equations in the input - only the first one was copied." % len(eqs))
    eq = tidied(eqs[0]) if tidy and src != "latex" else eqs[0]
    prepared, new = for_texmath(eq, src, macros)
    _add(notes, new)
    if src == "latex":
        if re.search(r"\\tag(?![a-zA-Z@])", eq.tex):
            _add(notes, [_TAG_NOTE])
        _add(notes, macro_notes(macros))
    mathml = write_equation(prepared, "mathml", for_office=True)
    return OfficeCopy(mathml, latex_wrap.wrap(eq.tex, "auto", eq.display), notes)


def mathml_for_office(text: str, src: str, mode: str = "auto",
                      macros: Optional[compat.Macros] = None) -> "tuple[str, Optional[str]]":
    """MathML (first equation) to place on the clipboard for Word/PowerPoint, plus a note."""
    copy = office_copy(text, src, mode, macros)
    return copy.mathml, "   ".join(copy.notes) or None
