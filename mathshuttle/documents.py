"""Equations in whole documents: .docx, .pptx (OMML), .tex, .md - with where each one is.

Office files are read here, not by pandoc's docx / pptx readers, so that their equations go through
:func:`pandoc_engine.read_omml` like a pasted one (Tidy LaTeX included) and keep their location
(the heading above them, the slide and its title, the footnote).  What a reader of the final document
sees is read: tracked deletions are skipped, one of a markup-compatibility block's alternatives is
read.  Equation objects of the old Equation Editor 3.0 and MathType (OLE objects, no OMML inside)
cannot be read; they are counted so that the user knows.
"""
from __future__ import annotations

import json
import os
import posixpath
import re
import tempfile
import zipfile
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from typing import Callable, List, Optional, Tuple

from . import compat
from .pandoc_engine import (MC_NS, OMML_NS, WML_NS, ConversionError, Equation, _api, _collect_math, _run,
                            markdown_definitions, markdown_maths, read_omml, split_math_paragraph)

P_NS = "http://schemas.openxmlformats.org/presentationml/2006/main"
A_NS = "http://schemas.openxmlformats.org/drawingml/2006/main"
R_NS = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"
REL_NS = "http://schemas.openxmlformats.org/package/2006/relationships"
# Strict Open XML (ISO 29500 strict) names the same vocabularies differently
_STRICT = {
    "http://purl.oclc.org/ooxml/officeDocument/math": OMML_NS,
    "http://purl.oclc.org/ooxml/wordprocessingml/main": WML_NS,
    "http://purl.oclc.org/ooxml/presentationml/main": P_NS,
    "http://purl.oclc.org/ooxml/drawingml/main": A_NS,
    "http://purl.oclc.org/ooxml/officeDocument/relationships": R_NS,
}

READABLE = {".docx": "Word document", ".pptx": "PowerPoint presentation", ".tex": "LaTeX file",
            ".md": "Markdown file"}
FILE_TYPES = [("Documents with equations", "*.docx *.pptx *.tex *.md"), ("Word", "*.docx"),
              ("PowerPoint", "*.pptx"), ("LaTeX", "*.tex"), ("Markdown", "*.md")]
MARK = "\u25a1"  # where the equation is, in its context
_LEGACY = re.compile(r"Equation\.(?:3|DSMT\d*)|MathType", re.I)


@dataclass
class Found:
    equation: Equation
    where: str          # "Method", "slide 2: Update rule", "footnote 3", "Bounds  [eq:bound]"
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
# tracked changes: what the final document no longer holds
_DELETED = (_q(WML_NS, "del"), _q(WML_NS, "moveFrom"))


def decode_text(data: bytes) -> str:
    """Text with a UTF-16 / UTF-8 byte-order mark, or UTF-8."""
    if data[:2] in (b"\xff\xfe", b"\xfe\xff"):
        return data.decode("utf-16")
    return data.decode("utf-8-sig")


def _parse(data: bytes) -> ET.Element:
    """An Office XML part (UTF-8 or UTF-16, Transitional or Strict) as Transitional XML."""
    if data[:2] in (b"\xff\xfe", b"\xfe\xff"):
        text = data.decode("utf-16")
    elif data[1:2] == b"\0":  # UTF-16 without a byte-order mark: "<" then a zero byte
        text = data.decode("utf-16-le")
    elif data[:1] == b"\0":
        text = data.decode("utf-16-be")
    else:
        text = data.decode("utf-8-sig")
    text = re.sub(r"^\s*<\?xml[^>]*\?>", "", text)  # the declaration names the bytes' encoding, not the text's
    for strict, transitional in _STRICT.items():
        text = text.replace(strict, transitional)
    return ET.fromstring(text)


def _walk(el: ET.Element, visit: Callable[[ET.Element], bool], leave: Optional[Callable[[ET.Element], None]] = None) -> None:
    """Depth-first, in document order; of a markup-compatibility block only the first Choice (the
    Fallback repeats the same content, e.g. as a picture), nothing of a tracked deletion.  `visit`
    returns False to skip the children; `leave` is called after the children of a visited element."""
    for child in el:
        if child.tag == _q(MC_NS, "AlternateContent"):
            branches = list(child)
            branch = next((b for b in branches if b.tag == _q(MC_NS, "Choice")), branches[0] if branches else None)
            if branch is not None:
                _walk(branch, visit, leave)
            continue
        if child.tag in _DELETED:
            continue
        if visit(child):
            _walk(child, visit, leave)
            if leave:
                leave(child)


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
            parts.append(MARK if el is math or math in el.iter() else "\u2026")
            return False
        if el.tag == _q(ns, "p") and el is not para:
            return False  # a paragraph nested in this one (a text box) has its own context
        if el.tag == _q(ns, "t") and el.text:
            parts.append(el.text)
        return True

    _walk(para, visit)
    text = "".join(parts).strip()
    return "" if text == MARK else _shorten(text)


def _is_legacy_object(el: ET.Element) -> bool:
    """An Equation Editor 3.0 / MathType OLE object (Word: o:OLEObject ProgID, PowerPoint: p:oleObj progId)."""
    return bool(_LEGACY.match(el.get("ProgID") or el.get("progId") or ""))


class _Collector:
    """Equations found while walking the parts of a document, with where they are."""

    def __init__(self) -> None:
        self.fragments: List[ET.Element] = []
        self.places: List[Tuple[str, str]] = []
        self.display: List[bool] = []
        self.legacy = 0

    def add(self, math: ET.Element, where: str, context: str, display: bool = False) -> None:
        for piece in split_math_paragraph(math):  # several equations in one math paragraph
            self.fragments.append(piece)
            self.places.append((where, context))
            self.display.append(display or piece.tag == _q(OMML_NS, "oMathPara"))

    def result(self, path: str) -> DocumentEquations:
        result = DocumentEquations(path, "office")
        for eq, (where, context), display in zip(_read_fragments(self.fragments), self.places, self.display):
            if eq is None:
                result.notes.append("an equation in %s could not be read" % where)
                continue
            result.found.append(Found(Equation(eq.tex, eq.display or display), where, context))
        if self.legacy:
            result.notes.append("%d Equation Editor 3.0 / MathType object(s) cannot be read (no OMML inside)"
                                % self.legacy)
        return result


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


# ---- Word ----------------------------------------------------------------------------------------
def _heading_styles(z: zipfile.ZipFile) -> set:
    """Style ids of headings and titles (localised Office versions use ids such as "1", "2")."""
    try:
        styles = _parse(z.read("word/styles.xml"))
    except KeyError:
        return set()
    ids = set()
    for st in styles.iter(_q(WML_NS, "style")):
        name = st.find(_q(WML_NS, "name"))
        value = ((name.get(_q(WML_NS, "val")) if name is not None else "") or "").lower()
        if re.match(r"(heading \d|title)$", value) or _outline_level(st) is not None:
            ids.add(st.get(_q(WML_NS, "styleId")))
    return ids


def _outline_level(el: ET.Element) -> Optional[int]:
    """The outline level 0-8 of a paragraph or style (9 is body text), or None."""
    outline = el.find("%s/%s" % (_q(WML_NS, "pPr"), _q(WML_NS, "outlineLvl")))
    try:
        level = int(outline.get(_q(WML_NS, "val"))) if outline is not None else None
    except (TypeError, ValueError):
        return None
    return level if level is not None and 0 <= level <= 8 else None


def _walk_word_part(root: ET.Element, headings: set, collector: _Collector, where: Optional[str] = None) -> None:
    """The equations of a Word part; `where`: a fixed location (a footnote), else the heading above."""
    state = {"heading": "", "paras": []}

    def visit(el):
        if el.tag == _q(WML_NS, "p"):
            style = el.find("%s/%s" % (_q(WML_NS, "pPr"), _q(WML_NS, "pStyle")))
            if where is None and ((style is not None and style.get(_q(WML_NS, "val")) in headings)
                                  or _outline_level(el) is not None):
                state["heading"] = _shorten("".join(t.text or "" for t in el.iter(_q(WML_NS, "t"))), 60)
            state["paras"].append(el)
            return True
        if _is_legacy_object(el):
            collector.legacy += 1
        if el.tag in _MATH_TAGS:
            para = state["paras"][-1] if state["paras"] else None
            collector.add(el, where or state["heading"] or "(before the first heading)", _context(para, WML_NS, el))
            return False
        return True

    def leave(el):
        if el.tag == _q(WML_NS, "p") and state["paras"]:
            state["paras"].pop()  # back to the paragraph around a text box

    _walk(root, visit, leave)


def read_docx(path: str) -> DocumentEquations:
    collector = _Collector()
    with zipfile.ZipFile(path) as z:
        names = set(z.namelist())
        headings = _heading_styles(z)
        _walk_word_part(_parse(z.read("word/document.xml")), headings, collector)
        for part, kind in (("word/footnotes.xml", "footnote"), ("word/endnotes.xml", "endnote")):
            if part not in names:
                continue
            for note in _parse(z.read(part)):
                if note.get(_q(WML_NS, "type")) in ("separator", "continuationSeparator", "continuationNotice"):
                    continue
                _walk_word_part(note, headings, collector, "%s %s" % (kind, note.get(_q(WML_NS, "id"), "?")))
    return collector.result(path)


# ---- PowerPoint ----------------------------------------------------------------------------------
def _targets(z: zipfile.ZipFile, part: str) -> List[Tuple[str, str, str]]:
    """(id, type, part name) of the relationships of `part`."""
    folder, name = posixpath.split(part)
    rels = posixpath.join(folder, "_rels", name + ".rels")
    if rels not in z.namelist():
        return []
    out = []
    for r in _parse(z.read(rels)).iter(_q(REL_NS, "Relationship")):
        target = r.get("Target") or ""
        resolved = target.lstrip("/") if target.startswith("/") else posixpath.normpath(posixpath.join(folder, target))
        out.append((r.get("Id"), r.get("Type") or "", resolved))
    return out


def _slide_paths(z: zipfile.ZipFile) -> List[str]:
    """The slide parts in presentation order (the file names need not follow it)."""
    targets = {rid: target for rid, _, target in _targets(z, "ppt/presentation.xml")}
    pres = _parse(z.read("ppt/presentation.xml"))
    return [targets[s.get(_q(R_NS, "id"))] for s in pres.iter(_q(P_NS, "sldId")) if s.get(_q(R_NS, "id")) in targets]


def _walk_slide_part(root: ET.Element, where: str, collector: _Collector, notes: bool = False) -> None:
    state = {"paras": []}

    def visit(el):
        if el.tag == _q(P_NS, "sp") and notes:
            ph = el.find(".//" + _q(P_NS, "ph"))
            if ph is None or ph.get("type") != "body":
                return False  # a notes page also holds the slide picture and a page number
        if el.tag == _q(A_NS, "p"):
            state["paras"].append(el)
            return True
        if _is_legacy_object(el):
            collector.legacy += 1
        if el.tag in _MATH_TAGS:
            para = state["paras"][-1] if state["paras"] else None
            context = _context(para, A_NS, el)
            # PowerPoint keeps every equation inline (m:oMath); one alone in its paragraph shows as display
            collector.add(el, where, context, display=not context)
            return False
        return True

    def leave(el):
        if el.tag == _q(A_NS, "p") and state["paras"]:
            state["paras"].pop()

    _walk(root, visit, leave)


def read_pptx(path: str) -> DocumentEquations:
    collector = _Collector()
    with zipfile.ZipFile(path) as z:
        names = set(z.namelist())
        for number, slide in enumerate(_slide_paths(z), 1):
            if slide not in names:
                continue
            root = _parse(z.read(slide))
            title = ""
            for sp in root.iter(_q(P_NS, "sp")):
                ph = sp.find(".//" + _q(P_NS, "ph"))
                if ph is not None and ph.get("type") in ("title", "ctrTitle"):
                    title = _shorten("".join(t.text or "" for t in sp.iter(_q(A_NS, "t"))), 50)
                    break
            where = "slide %d" % number + (": " + title if title else "")
            _walk_slide_part(root, where, collector)
            for _, kind, target in _targets(z, slide):
                if kind.endswith("/notesSlide") and target in names:
                    _walk_slide_part(_parse(z.read(target)), "slide %d notes" % number, collector, notes=True)
    return collector.result(path)


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
    equation as LaTeX, with the heading above it and its \\label; pandoc's warnings (a file that
    \\input names but that is not there) become notes."""
    folder = os.path.dirname(os.path.abspath(path))
    with open(path, "rb") as f:
        text = decode_text(f.read())
    # a .md file: pandoc's Markdown, where \[1\] is an escaped bracket (pandoc writes [1] so), not maths
    # as in pasted chat text (MARKDOWN); raw TeX environments in it are read all the same
    markdown = fmt == "markdown"
    out, err = _run(["-f", fmt, "-t", "json"], text.encode("utf-8"), cwd=folder)
    result = DocumentEquations(path, "latex")
    result.notes += [" ".join(line.replace("[WARNING]", "").split()) for line in err.splitlines()
                     if line.startswith("[WARNING]")]
    blocks = json.loads(out)["blocks"]
    definitions = markdown_definitions(blocks) if markdown else ""
    heading = ""
    for block in blocks:
        context = ""
        if isinstance(block, dict) and block.get("t") == "Header":
            words: List[str] = []
            _inline_text(block["c"][2], words)
            heading = _shorten("".join(words), 60)
        elif isinstance(block, dict) and block.get("t") in ("Para", "Plain"):
            words = []
            _inline_text(block["c"], words)
            context = _shorten("".join(words))
        eqs: List[Equation] = []
        if markdown:  # with the maths of raw TeX in the text (\begin{align} ... without $$)
            markdown_maths(block, eqs, definitions)
        else:
            _collect_math(block, eqs)  # also the maths of a heading
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


def _inlines(text: str) -> list:
    """Plain text as pandoc JSON inlines."""
    out: list = []
    for word in text.split():
        if out:
            out.append({"t": "Space"})
        out.append({"t": "Str", "c": word})
    return out


def write_docx(doc: DocumentEquations, path: str, macros: Optional[compat.Macros] = None) -> List[str]:
    """The document's equations as a Word document (`path`), each a native equation under a line that
    says where it was ("[3] Method"), in one pandoc run; for a collaborator who edits them in Word.
    Macros apply to a document's own LaTeX.  Returns notes: equations that texmath cannot write are in
    the file as LaTeX text.  The file is written next to `path` first and then put in its place, so a
    failed export leaves an older file as it was."""
    blocks: list = [{"t": "Header", "c": [1, ["", [], []], _inlines("Equations of " + os.path.basename(doc.path))]}]
    own = doc.source == "latex"
    notes: List[str] = []
    tags = False
    for i, found in enumerate(doc.found, 1):
        tex, new = compat.for_texmath(found.equation.tex, macros if own else None, "omml")
        if own:  # what was approximated (a colour, a resized fraction...); LaTeX from Office has none
            notes += [n for n in new if n not in notes]
            tags |= bool(re.search(r"\\tag(?![a-zA-Z@])", tex))
        kind = "DisplayMath" if found.equation.display else "InlineMath"
        blocks.append({"t": "Para", "c": [{"t": "Emph", "c": _inlines("[%d] %s" % (i, found.where))}]})
        blocks.append({"t": "Para", "c": [{"t": "Math", "c": [{"t": kind}, tex]}]})
    source = json.dumps({"pandoc-api-version": _api(), "meta": {}, "blocks": blocks}).encode("utf-8")
    target = os.path.abspath(path)
    try:  # a name of its own next to the target (two exports at once do not meet)
        handle, part = tempfile.mkstemp(dir=os.path.dirname(target), prefix="." + os.path.basename(target) + ".",
                                        suffix=".part")
        os.close(handle)
    except OSError as e:
        raise ConversionError("Could not write %s: %s" % (path, e.strerror or e))
    try:
        _, err = _run(["-f", "json", "-t", "docx", "-o", part], source)
        os.replace(part, target)
    except OSError as e:  # e.g. the file is open in Word
        raise ConversionError("Could not write %s: %s" % (path, e.strerror or e))
    finally:
        try:
            os.remove(part)
        except OSError:
            pass  # moved into place, or held for a moment by a virus scanner
    failed = len(re.findall(r"^\[WARNING\] Could not convert TeX math", err, re.M))
    if failed:
        notes.append("%d equation%s could not be converted; %s in the file as LaTeX text."
                     % (failed, "" if failed == 1 else "s", "it is" if failed == 1 else "they are"))
    if tags:
        notes.append("\\tag numbers are not carried over (Word numbers equations itself).")
    return notes
