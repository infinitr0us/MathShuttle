"""High-level conversion API used by the GUI and the command line."""
from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass, field
from typing import List, Optional, Tuple

from . import compat, diagnose as _diagnose, latex_wrap, tidy as _tidy
from .pandoc_engine import (READ_FORMATS, WRITE_FORMATS, ConversionError, Equation,  # noqa: F401
                            _has_math_delimiters, drop_annotations, looks_like_markdown, mathml_elements, parse_mathml,
                            read_equations, read_latex_text, readable_mathml, strip_tex, write_equation, write_equations,
                            write_mathml)

FORMAT_LABELS = {
    "latex": "LaTeX",
    "mathml": "MathML",
    "omml": "OMML (Office Math)",
    "eqn": "eqn (groff)",
    "typst": "Typst",
}

MODES = [("auto", "Auto"), ("display", "Display"), ("inline", "Inline")]

_TAG_NOTE = "\\tag numbers are not carried over (Word / PowerPoint number equations themselves)."
_UNICODE_NOTE = "The LaTeX contains a Unicode character that pdfLaTeX may reject."


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


def _write(prepared: Equation, dst: str, eq: Equation, which: str = "", **kw) -> str:
    """write_equation, with texmath's parse error of LaTeX input explained in plain words first."""
    try:
        return write_equation(prepared, dst, **kw)
    except ConversionError as e:
        raise _diagnose.explained(e, eq.tex, prepared.tex, which) from None


# Markdown code: a fence, an indented block (after a blank line), `inline code`
_MARKDOWN_CODE = re.compile(r"^(`{3,}|~{3,})[^\n]*\n[\s\S]*?^\1[^\n]*$|(?:(?<=\n\n)|^)(?:(?: {4}|\t)[^\n]*(?:\n|$))+"
                            r"|(`+)[\s\S]*?\2", re.M)


def input_macros(macros: Optional[compat.Macros], text: str, src: str,
                 as_markdown: bool = False, preamble: Optional[str] = None) -> Optional[compat.Macros]:
    """The macros plus what LaTeX input defines outside its equations that pandoc's reader drops
    (\\DeclarePairedDelimiter above $\\abs{x}$, as Open file's Use writes it) and the packages it loads;
    in text read as Markdown (a chat answer), not from its code."""
    if src != "latex":
        return macros
    # (also when both readings agreed and the LaTeX one was kept: the code is still no preamble)
    markdown = as_markdown or ("\\" in text and looks_like_markdown(text))
    leftovers = compat.pandoc_leftovers(_MARKDOWN_CODE.sub(" ", text) if markdown else text)
    if preamble is None:
        return compat.with_document(macros, leftovers)
    document = [macros.document] if macros is not None else []
    document.append(leftovers)
    return compat.with_document(macros, "\n".join(part for part in document if part),
                                macros.labels if macros is not None else None, preamble=preamble)


def _read(text: str, src: str, mode: str, tidy: bool) -> List[Equation]:
    return _read_text(text, src, mode, tidy)[0]


def _read_text(text: str, src: str, mode: str, tidy: bool) -> "tuple[List[Equation], bool]":
    """The equations of the input, and whether LaTeX input was read as Markdown (a chat answer)."""
    if src == "latex":
        eqs, as_markdown = read_latex_text(text, default_display=(mode != "inline"))
        return apply_mode(eqs, mode), as_markdown
    return apply_mode(read_equations(text, src, default_display=(mode != "inline"), tidy=tidy), mode), False


_MARKDOWN_NOTE = ("Read as Markdown text (a chat answer, notes): % is a percent sign there and $5 is money; "
                  "the equations are those in $...$, $$...$$, \\(...\\) and \\[...\\].")


def _read_for_expansion(text: str, mode: str,
                        macros: Optional[compat.Macros]) -> Tuple[List[Equation], bool, Optional[compat.Macros]]:
    """Keep preamble macro calls in the equations so the LaTeX writer can expand and report them."""
    masked = None
    if looks_like_markdown(text):
        masked = _MARKDOWN_CODE.sub(lambda m: re.sub(r"[^\n]", " ", m.group()), text)
    body, preamble = compat.before_equations(text, masked)
    if preamble and not _has_math_delimiters(body):
        body, preamble = text, ""  # a bare equation keeps its own definitions
    equations, markdown = read_latex_text(body, default_display=(mode != "inline"))
    if preamble:
        # A macro in the prose may make an equation of its own ($x$ or \ensuremath{M}): the reading with
        # the macros applied finds it, and each equation of it is taken as written where the two line up.
        full, _ = compat.before_equations(text, masked, defer=False)
        try:
            complete, as_markdown = read_latex_text(full, default_display=(mode != "inline"))
        except ConversionError:
            pass  # expand each recovered equation separately
        else:
            if len(complete) != len(equations) or any(a.display != b.display for a, b in zip(complete, equations)):
                from .documents import _matched
                equations = [raw or eq for raw, eq in zip(_matched(complete, equations), complete)]
                markdown = as_markdown
    return apply_mode(equations, mode), markdown, input_macros(macros, body, "latex", markdown, preamble)


def convert(text: str, src: str, dst: str, mode: str = "auto", wrapper: str = "auto",
            annotate: bool = False, macros: Optional[compat.Macros] = None, tidy: bool = True, *,
            expand_macros: bool = False) -> Result:
    """Convert `text` from format `src` to format `dst`.

    mode     "auto" (as detected from the input; bare LaTeX counts as display),
             "display" or "inline" - the mode used for the *output*.
    wrapper  how LaTeX output is delimited (see latex_wrap.WRAPPERS); ignored otherwise.
    annotate keep the TeX source as an <annotation> inside MathML output.
    macros   definitions applied to LaTeX input (:class:`compat.Macros`).
    expand_macros  also write out the macros when converting LaTeX to LaTeX.
    tidy     write LaTeX from MathML / OMML input as an author would (:mod:`tidy`).
    """
    if src not in READ_FORMATS:
        raise ValueError("cannot read format %r" % src)
    if dst not in WRITE_FORMATS:
        raise ValueError("cannot write format %r" % dst)

    expand = expand_macros and src == dst == "latex"
    if expand:
        eqs, as_markdown, macros = _read_for_expansion(text, mode, macros)
    else:
        eqs, as_markdown = _read_text(text, src, mode, tidy)
        macros = input_macros(macros, text, src, as_markdown)
    notes: List[str] = [_MARKDOWN_NOTE] if as_markdown else []
    if len(eqs) > 1:
        notes.append("%d equations found in the input; each was converted separately." % len(eqs))

    outputs: List[str] = []
    numbering_conflict = False
    for i, eq in enumerate(eqs):
        if dst == "latex":
            display = not latex_wrap.wrapper_is_inline(wrapper, eq.display)
            if expand:
                tex, new = compat.expand_macros(eq.tex, macros)
                eq = Equation(tex, eq.display)
                _add(notes, new)
            if tidy and src != "latex":
                # for the mode of the output (\limits stays in $...$); bare: the equation's own mode
                eq = tidied(eq, eq.display if wrapper == "bare" else display)
            if src != "latex" and not eq.tex.isascii():
                _add(notes, [_UNICODE_NOTE])
            outputs.append(latex_wrap.wrap(eq.tex, wrapper, display))
            numbering_conflict |= latex_wrap.numbering_conflict(eq.tex, wrapper, display)
            # the mode of the output, which the wrapper decides (Auto: a kept environment)
            eqs[i] = Equation(eq.tex, latex_wrap.output_is_display(eq.tex, wrapper, display))
        else:
            # (tidy_tex makes LaTeX for a paper: \backslash -> \setminus...; MathML / OMML output keeps
            # the symbols of the input - the identifiers were already read right, see read_mathml)
            prepared, new = for_texmath(eq, src, macros, dst)
            _add(notes, new)
            which = "Equation %d of %d" % (i + 1, len(eqs)) if len(eqs) > 1 else ""
            outputs.append(_write(prepared, dst, eq, which, annotate=annotate, source=eq.tex))
            if src == "latex" and re.search(r"\\tag(?![a-zA-Z@])", eq.tex):
                _add(notes, [_TAG_NOTE])
    if numbering_conflict:
        notes.append("\\tag / \\label commands were kept, but LaTeX allows no \\tag and at most one "
                     "\\label in this wrapper - edit them, or choose the align wrapper.")
    if src == "latex" and (dst != "latex" or expand):
        _add(notes, macro_notes(macros))
    if src == "mathml" and "annotation" in text:
        _add(notes, annotation_notes(text, macros))
    return Result(eqs, outputs, compat.merge_notes(notes) if expand else notes)


# ---- a TeX annotation that does not show what its MathML shows -----------------------------------
# characters that renderers use for the same thing (applied after NFKC, which folds 𝑥 / x, ϕ / φ and
# makes "…" "..."); MathType writes angle brackets as < >; invisible operators and zero-width spaces go
_SAME_SYMBOL = {ord(a): b for a, b in (
    ("⋅", "·"), ("∙", "·"), ("−", "-"), ("∗", "*"), ("∣", "|"), ("∥", "‖"), ("⋯", "..."), ("⩽", "≤"), ("⩾", "≥"),
    ("⟨", "<"), ("〈", "<"), ("〈", "<"), ("⟩", ">"), ("〉", ">"), ("〉", ">"), ("≔", ":="), ("⟺", "⇔"), ("⟹", "⇒"),
    ("⟸", "⇐"), ("⟷", "↔"), ("⟶", "→"), ("⟵", "←"), ("⟼", "↦"))}
_SAME_SYMBOL.update({cp: None for cp in (0x2061, 0x2062, 0x2063, 0x2064, 0x200B)})
# what renderers put over or under a symbol as an accent or a bar, brace or arrow, each in its own way
# (\hat: ^ ˆ U+0302; \overline: ‾ ¯ ―; \vec, \underleftarrow: arrows; ...)
_ACCENTS = frozenset("^ˆ~˜¯‾ˉ―_˘ˇ˙¨´`°˚‿⁀⏞⏟⏜⏝⎴⎵︷︸→←↔⟶⟵⟷⇀↼⃗" + "".join(map(chr, range(0x0300, 0x0370)))
                     + "".join(map(chr, range(0x20D0, 0x20F0))))
# names that take limits over and under them (\lim_{x\to 0}; Word writes "lim" as <mi>)
_LIMIT_NAMES = frozenset("lim limsup liminf max min sup inf det gcd Pr arg".split())
_SCRIPTS = {"msub": "_", "munder": "_", "msup": "^", "mover": "^", "msubsup": "_^", "munderover": "_^"}
_PRESENTATION_TOKENS = frozenset("mi mn mo mtext ms mfenced".split())
_OPEN, _CLOSE = "", ""  # the bounds of a script's or fraction's argument (not a shown { })


def _local(el) -> str:
    return el.tag.rsplit("}", 1)[-1] if isinstance(el.tag, str) else ""


def _is_accent(base, child) -> bool:
    """Whether `child`, over or under `base`, is an accent (a bar, brace, arrow, hat...) rather than a
    script or limit: an <mo> of accent characters over or under anything but an operator (∑, lim, =).
    Decided by what is shown, not by the accent / accentunder attributes: renderers set them each in
    their own way (Word's overline is accent="false", texmath's arrow under \\lim accent="true")."""
    text = "".join(child.itertext()).strip()
    if _local(child) != "mo" or not text or not all(ch in _ACCENTS for ch in text):
        return False
    shown = unicodedata.normalize("NFKC", "".join(base.itertext())).strip()
    operator = _local(base) == "mo" or shown in _LIMIT_NAMES or (
        len(shown) == 1 and unicodedata.category(shown) == "Sm")
    return not operator


def _symbols(math) -> str:
    """What a <math> shows, for comparing two renderings of an equation: its symbols in order, a script
    (_{..} ^{..}), fraction ({..}/{..}) or root (√{..}) marked with its arguments' bounds, the fences of
    <mfenced> written out, prescripts before their base; no spacing, no invisible operators, nothing
    in <mphantom>, font variants folded (NFKC), accents left out (renderers write them with different
    characters).  Sub- and superscripts and limits under or over an operator are marked alike:
    renderers choose between them by display mode."""
    out: List[str] = []

    def arg(el, mark: str = "") -> None:
        """An argument within its bounds, after `mark` (_ ^); a script with nothing in it is left out."""
        start = len(out)
        out.extend((mark, _OPEN))
        walk(el)
        if mark and not "".join(out[start + 2:]).translate(_SAME_SYMBOL).strip():
            del out[start:]
        else:
            out.append(_CLOSE)

    def walk(el) -> None:
        tag = _local(el)
        kids = [k for k in el if _local(k) != "none"] if tag != "mmultiscripts" else list(el)
        if tag == "mphantom":
            return
        if tag == "mfenced":
            seps = [ch for ch in el.get("separators", ",") if not ch.isspace()]
            out.append(el.get("open", "("))
            for k, child in enumerate(kids):
                if k and seps:
                    out.append(seps[min(k - 1, len(seps) - 1)])
                walk(child)
            out.append(el.get("close", ")"))
            return
        out.append(el.text or "")
        if tag in _SCRIPTS and kids:
            walk(kids[0])
            for mark, child in zip(_SCRIPTS[tag], kids[1:]):
                if tag in ("mover", "munder", "munderover") and _is_accent(kids[0], child):
                    continue
                arg(child, mark)
            return
        if tag == "mmultiscripts" and kids:  # base, (sub sup)*, <mprescripts/>, (sub sup)*
            names = [_local(k) for k in kids]
            split = names.index("mprescripts") if "mprescripts" in names else len(kids)
            post, pre = kids[1:split], kids[split + 1:]
            for scripts in (pre, None, post):
                if scripts is None:
                    walk(kids[0])
                    continue
                for k in range(0, len(scripts) - 1, 2):
                    for mark, child in (("_", scripts[k]), ("^", scripts[k + 1])):
                        if _local(child) != "none":
                            arg(child, mark)
            return
        if tag == "mfrac" and len(kids) == 2:
            arg(kids[0])
            out.append("/")
            arg(kids[1])
            return
        if tag in ("msqrt", "mroot"):
            out.append("√")
            if tag == "mroot" and len(kids) == 2:
                arg(kids[1])
                kids = kids[:1]
            out.append(_OPEN)
            for child in kids:
                walk(child)
            out.append(_CLOSE)
            return
        k = 0
        while k < len(kids):
            # Word's linear fraction (\tfrac, a/b typed in Word): <mrow>a</mrow><mo>/</mo><mrow>b</mrow>
            if k + 2 < len(kids) and _local(kids[k]) == _local(kids[k + 2]) == "mrow" \
                    and _local(kids[k + 1]) == "mo" and (kids[k + 1].text or "").strip() == "/":
                arg(kids[k])
                out.append("/")
                arg(kids[k + 2])
                k += 3
                continue
            walk(kids[k])
            k += 1

    walk(math)
    text = unicodedata.normalize("NFKC", "".join(out)).translate(_SAME_SYMBOL)
    return "".join(ch for ch in text if not ch.isspace())


def annotation_notes(text: str, macros: Optional[compat.Macros] = None) -> List[str]:
    """MathML that carries its TeX source as an annotation (MathType, KaTeX / MathJax pages, the "TeX
    annotation" option): a note for each <math> whose annotation shows something else than the MathML
    itself, which the conversion follows - it was edited after the TeX was written.  Not compared: an
    annotation that cannot be read (macros not given), and an equation whose MathML texmath cannot read
    (Content MathML, KaTeX's \\dddot): its LaTeX comes from the annotation.  The annotations are drawn
    by texmath in one pandoc run, in each equation's mode."""
    try:
        maths = parse_mathml(text)
    except ConversionError:
        return []
    annotations = [drop_annotations(math) for math in maths]  # what is left of each is what it shows
    try:
        readable = readable_mathml(maths)
    except ConversionError:
        return []
    todo = []  # (number, math, annotation, the annotation as texmath gets it)
    for i, (math, tex, ok) in enumerate(zip(maths, annotations, readable), 1):
        if not ok or not tex or not tex.strip() or not any(_local(e) in _PRESENTATION_TOKENS for e in math.iter()):
            continue
        try:
            prepared, _ = for_texmath(Equation(strip_tex(tex), math.get("display") == "block"), "latex", macros)
        except ConversionError:
            continue
        todo.append((i, math, tex, prepared))
    if not todo:
        return []
    try:
        drawn = mathml_elements([t[3] for t in todo])
    except ConversionError:
        return []
    notes: List[str] = []
    for (i, math, tex, _), annotated in zip(todo, drawn):
        if annotated is None or _symbols(math) == _symbols(annotated):
            continue
        short = " ".join(tex.split())
        short = short if len(short) <= 60 else short[:57] + "..."
        which = "" if len(maths) == 1 else " of equation %d" % i
        notes.append("The TeX annotation%s (%s) does not show what the MathML shows: it may be out of date. "
                     "The LaTeX here is what the MathML shows." % (which, short))
    return notes


def convert_equation(eq: Equation, dst: str, wrapper: str = "auto", macros: Optional[compat.Macros] = None,
                     annotate: bool = False, *, expand_macros: bool = False) -> "tuple[str, List[str]]":
    """One equation given as LaTeX (a document's, or tidied from Office) in format `dst`, and notes."""
    if dst == "latex":
        display = not latex_wrap.wrapper_is_inline(wrapper, eq.display)
        tex, notes = compat.expand_macros(eq.tex, macros) if expand_macros else (eq.tex, [])
        return latex_wrap.wrap(tex, wrapper, display), notes
    prepared, notes = for_texmath(eq, "latex", macros, dst)
    return _write(prepared, dst, eq, annotate=annotate, source=eq.tex), notes


def convert_equations(eqs: List[Equation], dst: str, wrapper: str = "auto",
                      macros: Optional[compat.Macros] = None, *,
                      expand_macros: bool = False) -> list[tuple[str, List[str]] | ConversionError]:
    """Document equations in order, with the same notes or error as converting each separately."""
    if dst == "latex":
        return [convert_equation(eq, dst, wrapper, macros, expand_macros=expand_macros) for eq in eqs]
    keys = [(eq.tex, eq.display) for eq in eqs]
    results = {}
    pending = []
    for key in dict.fromkeys(keys):
        eq = Equation(*key)
        try:
            prepared, notes = for_texmath(eq, "latex", macros, dst)
        except ConversionError as e:
            results[key] = e
        else:
            pending.append((key, eq, prepared, notes))
    try:
        outputs = write_equations([prepared for _, _, prepared, _ in pending], dst)
    except ConversionError:  # e.g. timeout of the whole writer: recover individual results
        outputs = [None] * len(pending)
    for (key, eq, prepared, notes), output in zip(pending, outputs):
        if output is None:
            try:
                output = _write(prepared, dst, eq)
            except ConversionError as e:
                results[key] = e
                continue
        results[key] = output, notes
    return [results[key] for key in keys]


@dataclass
class OfficeCopy:
    mathml: str           # for the clipboard: Word / PowerPoint paste a native equation
    latex: str            # the same (first) equation as LaTeX.  Not for the clipboard's plain-text
    #                       format: Word pastes from that one (see clipboard.copy_mathml_for_office)
    notes: List[str] = field(default_factory=list)
    source: Optional[Equation] = None  # the LaTeX it was made from, as written (for recall.remember)

    def remember(self) -> None:
        """Note it for Paste from Word/PowerPoint (recall): after it is on the clipboard."""
        if self.source is not None:
            from . import recall
            recall.remember(self.mathml, self.source.tex, self.source.display)


class Equations(list):
    """Equations of an input, and whether it was read as Markdown (a chat answer), for the note."""
    as_markdown = False


def input_equations(text: str, src: str, mode: str = "auto", tidy: bool = True) -> List[Equation]:
    """The equations of the input, in order: what "Copy for Word/PowerPoint" can copy (by index)."""
    eqs, as_markdown = _read_text(text, src, mode, tidy)
    found = Equations(eqs)
    found.as_markdown = as_markdown
    return found


def office_copy(text: str, src: str, mode: str = "auto", macros: Optional[compat.Macros] = None,
                tidy: bool = True, index: int = 0, eqs: Optional[List[Equation]] = None) -> OfficeCopy:
    """What "Copy for Word/PowerPoint" puts on the clipboard: equation `index` (0: the first) of the
    input; Word and PowerPoint paste one equation at a time.  `eqs`: the input's equations, when the
    caller read them already (:func:`input_equations` with the same arguments)."""
    as_markdown = getattr(eqs, "as_markdown", False)  # (eqs from input_equations)
    if eqs is None:
        eqs, as_markdown = _read_text(text, src, mode, tidy)
    if not 0 <= index < len(eqs):
        raise ConversionError("There is no equation %d: the input has %d." % (index + 1, len(eqs)))
    macros = input_macros(macros, text, src, as_markdown)
    notes: List[str] = [_MARKDOWN_NOTE] if as_markdown else []
    if len(eqs) > 1:
        notes.append("Equation %d of the %d in the input was copied." % (index + 1, len(eqs)))
    eq = eqs[index]
    prepared, new = for_texmath(eq, src, macros)
    _add(notes, new)
    if src == "latex":
        if re.search(r"\\tag(?![a-zA-Z@])", eq.tex):
            _add(notes, [_TAG_NOTE])
        _add(notes, macro_notes(macros))
    which = "Equation %d of %d" % (index + 1, len(eqs)) if len(eqs) > 1 and src == "latex" else ""
    mathml = _write(prepared, "mathml", eq, which, for_office=True)
    latex = tidied(eq) if tidy and src != "latex" else eq
    if src != "latex" and not latex.tex.isascii():
        _add(notes, [_UNICODE_NOTE])
    return OfficeCopy(mathml, latex_wrap.wrap(latex.tex, "auto", eq.display), notes,
                      source=eq if src == "latex" else None)


def mathml_for_office(text: str, src: str, mode: str = "auto",
                      macros: Optional[compat.Macros] = None) -> "tuple[str, Optional[str]]":
    """MathML (first equation) to place on the clipboard for Word/PowerPoint, plus a note."""
    copy = office_copy(text, src, mode, macros)
    return copy.mathml, "   ".join(copy.notes) or None
