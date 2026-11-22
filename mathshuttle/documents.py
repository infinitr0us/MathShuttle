"""Equations in whole documents: .docx, .pptx (OMML), .tex, .md - with where each one is.

Office files are read here, not by pandoc's docx / pptx readers, so that their equations go through
:func:`pandoc_engine.read_omml` like a pasted one (Tidy LaTeX included) and keep their location
(the heading above them, the slide and its title).  Equation objects of the old Equation Editor 3.0
and MathType (OLE objects, no OMML inside) cannot be read; they are counted so that the user knows.
"""
from __future__ import annotations

import json
import os
import posixpath
import re
import zipfile
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from typing import Callable, List, Optional, Tuple

from .pandoc_engine import (MC_NS, OMML_NS, WML_NS, ConversionError, Equation, _collect_math, _run,
                            read_omml)

P_NS = "http://schemas.openxmlformats.org/presentationml/2006/main"
A_NS = "http://schemas.openxmlformats.org/drawingml/2006/main"
R_NS = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"
REL_NS = "http://schemas.openxmlformats.org/package/2006/relationships"

READABLE = {".docx": "Word document", ".pptx": "PowerPoint presentation", ".tex": "LaTeX file",
            ".md": "Markdown file"}
FILE_TYPES = [("Documents with equations", "*.docx *.pptx *.tex *.md"), ("Word", "*.docx"),
              ("PowerPoint", "*.pptx"), ("LaTeX", "*.tex"), ("Markdown", "*.md")]
MARK = "\u25a1"  # where the equation is, in its context


@dataclass
class Found:
    equation: Equation
    where: str          # "Method", "slide 2: Update rule", "Bounds  [eq:bound]"
    context: str = ""   # the text around it, shortened, with MARK for the equation


@dataclass
class DocumentEquations:
    path: str
    source: str  # "latex" when the equations are the document's own LaTeX, else "office"
    found: List[Found] = field(default_factory=list)
    notes: List[str] = field(default_factory=list)


def _q(ns: str, tag: str) -> str:
    return "{%s}%s" % (ns, tag)


_MATH_TAGS = (_q(OMML_NS, "oMathPara"), _q(OMML_NS, "oMath"))


def _walk(el: ET.Element, visit: Callable[[ET.Element], bool]) -> None:
    """Depth-first, in document order; of a markup-compatibility block only the first Choice (the
    Fallback repeats the same content, e.g. as a picture).  `visit` returns False to skip children."""
    for child in el:
        if child.tag == _q(MC_NS, "AlternateContent"):
            branches = list(child)
            branch = next((b for b in branches if b.tag == _q(MC_NS, "Choice")), branches[0] if branches else None)
            if branch is not None:
                _walk(branch, visit)
            continue
        if visit(child):
            _walk(child, visit)


def _shorten(text: str, limit: int = 90) -> str:
    text = " ".join(text.split())
    return text if len(text) <= limit else text[:limit - 1] + "\u2026"


def _context(para: Optional[ET.Element], ns: str, math: ET.Element) -> str:
    """The text of the paragraph holding `math`, with MARK where it is (other equations: …)."""
    if para is None:
        return ""
    parts: List[str] = []

    def visit(el):
        if el.tag in _MATH_TAGS:
            parts.append(MARK if el is math else "\u2026")
            return False
        if el.tag == _q(ns, "t") and el.text:
            parts.append(el.text)
        return True

    _walk(para, visit)
    text = "".join(parts).strip()
    return "" if text == MARK else _shorten(text)


def _read_fragments(fragments: List[ET.Element]) -> List[Optional[Equation]]:
    """The equations of OMML fragments, one per fragment, cleaned up as when pasted: all in one pandoc
    run, or one by one if the counts do not match (a fragment texmath skips: None)."""
    from .convert import tidied
    if not fragments:
        return []
    xml = "".join(ET.tostring(f, encoding="unicode") for f in fragments)
    try:
        eqs: List[Optional[Equation]] = list(read_omml(xml))
        if len(eqs) != len(fragments):
            raise ConversionError("counts differ")
    except ConversionError:
        eqs = []
        for f in fragments:
            try:
                eqs.append(read_omml(ET.tostring(f, encoding="unicode"))[0])
            except ConversionError:
                eqs.append(None)
    return [tidied(eq) if eq is not None else None for eq in eqs]


def _legacy_objects(xml: str) -> int:
    return len(re.findall(r'ProgID="(?:Equation\.(?:3|DSMT\d*)|MathType)', xml))


def _collect(result: DocumentEquations, fragments, places, legacy: int,
             display: Optional[List[bool]] = None) -> DocumentEquations:
    """Read the fragments into `result`; `display`: per fragment, True to show it as display maths."""
    for i, (eq, (where, context)) in enumerate(zip(_read_fragments(fragments), places)):
        if eq is None:
            result.notes.append("an equation in %s could not be read" % where)
            continue
        if display and display[i]:
            eq = Equation(eq.tex, True)
        result.found.append(Found(eq, where, context))
    if legacy:
        result.notes.append("%d Equation Editor 3.0 / MathType object(s) cannot be read (no OMML inside)" % legacy)
    return result


# ---- Word ----------------------------------------------------------------------------------------
def _heading_styles(z: zipfile.ZipFile) -> set:
    """Style ids of headings and titles (localised Office versions use ids such as "1", "2")."""
    try:
        styles = ET.fromstring(z.read("word/styles.xml"))
    except KeyError:
        return set()
    ids = set()
    for st in styles.iter(_q(WML_NS, "style")):
        name = st.find(_q(WML_NS, "name"))
        value = ((name.get(_q(WML_NS, "val")) if name is not None else "") or "").lower()
        outline = st.find("%s/%s" % (_q(WML_NS, "pPr"), _q(WML_NS, "outlineLvl")))
        if re.match(r"(heading \d|title)$", value) or outline is not None:
            ids.add(st.get(_q(WML_NS, "styleId")))
    return ids


def read_docx(path: str) -> DocumentEquations:
    with zipfile.ZipFile(path) as z:
        xml = z.read("word/document.xml").decode("utf-8")
        headings = _heading_styles(z)
    fragments: List[ET.Element] = []
    places: List[Tuple[str, str]] = []
    state = {"heading": "", "para": None}

    def visit(el):
        if el.tag == _q(WML_NS, "p"):
            style = el.find("%s/%s" % (_q(WML_NS, "pPr"), _q(WML_NS, "pStyle")))
            outline = el.find("%s/%s" % (_q(WML_NS, "pPr"), _q(WML_NS, "outlineLvl")))
            if (style is not None and style.get(_q(WML_NS, "val")) in headings) or outline is not None:
                state["heading"] = _shorten("".join(t.text or "" for t in el.iter(_q(WML_NS, "t"))), 60)
            state["para"] = el
            return True
        if el.tag in _MATH_TAGS:
            fragments.append(el)
            places.append((state["heading"] or "(before the first heading)", _context(state["para"], WML_NS, el)))
            return False
        return True

    _walk(ET.fromstring(xml), visit)
    return _collect(DocumentEquations(path, "office"), fragments, places, _legacy_objects(xml))


# ---- PowerPoint ----------------------------------------------------------------------------------
def _slide_paths(z: zipfile.ZipFile) -> List[str]:
    """The slide parts in presentation order (the file names need not follow it)."""
    pres = ET.fromstring(z.read("ppt/presentation.xml"))
    rels = ET.fromstring(z.read("ppt/_rels/presentation.xml.rels"))
    targets = {r.get("Id"): r.get("Target") for r in rels.iter(_q(REL_NS, "Relationship"))}
    paths = []
    for sid in pres.iter(_q(P_NS, "sldId")):
        target = targets.get(sid.get(_q(R_NS, "id")))
        if target:
            paths.append(target.lstrip("/") if target.startswith("/") else posixpath.normpath(posixpath.join("ppt", target)))
    return paths


def read_pptx(path: str) -> DocumentEquations:
    with zipfile.ZipFile(path) as z:
        names = set(z.namelist())
        parts = [(n, z.read(p).decode("utf-8")) for n, p in enumerate(_slide_paths(z), 1) if p in names]
    fragments: List[ET.Element] = []
    places: List[Tuple[str, str]] = []
    standalone: List[bool] = []
    legacy = 0
    for number, xml in parts:
        legacy += _legacy_objects(xml)
        root = ET.fromstring(xml)
        title = ""
        for sp in root.iter(_q(P_NS, "sp")):
            ph = sp.find(".//" + _q(P_NS, "ph"))
            if ph is not None and ph.get("type") in ("title", "ctrTitle"):
                title = _shorten("".join(t.text or "" for t in sp.iter(_q(A_NS, "t"))), 50)
                break
        where = "slide %d" % number + (": " + title if title else "")
        state = {"para": None}

        def visit(el, where=where, state=state):
            if el.tag == _q(A_NS, "p"):
                state["para"] = el
                return True
            if el.tag in _MATH_TAGS:
                fragments.append(el)
                context = _context(state["para"], A_NS, el)
                places.append((where, context))
                standalone.append(not context)
                return False
            return True

        _walk(root, visit)
    # PowerPoint keeps every equation inline (m:oMath); one alone in its paragraph is shown as display
    return _collect(DocumentEquations(path, "office"), fragments, places, legacy, standalone)


# ---- LaTeX and Markdown --------------------------------------------------------------------------
def _inline_text(node, words: List[str]) -> None:
    if isinstance(node, dict):
        t = node.get("t")
        if t == "Str":
            words.append(node["c"])
        elif t in ("Space", "SoftBreak"):
            words.append(" ")
        elif t == "Math":
            words.append("$%s$" % node["c"][1])
        else:
            _inline_text(node.get("c"), words)
    elif isinstance(node, list):
        for n in node:
            _inline_text(n, words)


def read_text_document(path: str, fmt: str) -> DocumentEquations:
    """A .tex file (\\input and \\include found next to it) or a .md file, read by pandoc: each
    equation as LaTeX, with the heading above it and its \\label."""
    folder = os.path.dirname(os.path.abspath(path))
    with open(path, "rb") as f:
        data = f.read()
    out, _ = _run(["-f", fmt, "-t", "json"], data, cwd=folder)
    result = DocumentEquations(path, "latex")
    heading = ""
    for block in json.loads(out)["blocks"]:
        if isinstance(block, dict) and block.get("t") == "Header":
            words: List[str] = []
            _inline_text(block["c"][2], words)
            heading = _shorten("".join(words), 60)
            continue
        eqs: List[Equation] = []
        _collect_math(block, eqs)
        context = ""
        if eqs and isinstance(block, dict) and block.get("t") in ("Para", "Plain"):
            words = []
            _inline_text(block["c"], words)
            context = _shorten("".join(words))
        for eq in eqs:
            label = re.search(r"\\label\{([^{}]*)\}", eq.tex)
            where = (heading or "(before the first heading)") + ("  [%s]" % label.group(1) if label else "")
            result.found.append(Found(eq, where, "" if eq.display else context))
    return result


def read_document(path: str) -> DocumentEquations:
    """Every equation in a .docx, .pptx, .tex or .md file, in document order."""
    ext = os.path.splitext(path)[1].lower()
    if ext not in READABLE:
        raise ConversionError("Cannot read %s files (only %s)." % (ext or "these", ", ".join(READABLE)))
    try:
        if ext == ".docx":
            return read_docx(path)
        if ext == ".pptx":
            return read_pptx(path)
        return read_text_document(path, "latex" if ext == ".tex" else "markdown")
    except OSError as e:
        raise ConversionError("Cannot read %s: %s" % (path, e.strerror or e))
    except (zipfile.BadZipFile, KeyError, ET.ParseError, UnicodeDecodeError) as e:
        raise ConversionError("%s is not a readable %s (%s)" % (os.path.basename(path), READABLE[ext], e))
