"""Equations in whole documents: .docx, .pptx, .tex, .md - with where each one is.

Office files are read here, not by pandoc's docx / pptx readers, so that their equations go through
:func:`pandoc_engine.read_omml` like a pasted one (Tidy LaTeX included) and keep their location
(the heading above them, the slide and its title, the footnote).  What a reader of the final document
sees is read: tracked deletions are skipped, one of a markup-compatibility block's alternatives is
read.  Equation objects of the old Equation Editor 3.0 and MathType (OLE objects, no OMML inside)
cannot be read; they are counted so that the user knows. PowerPoint shapes can also hold LaTeX
from IguanaTeX or TeXPoint in their tags.
"""
from __future__ import annotations

import json
import os
import posixpath
import re
import tempfile
import time
import uuid
import zipfile
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from typing import Callable, Dict, List, Optional, Tuple, Union

from . import compat, wordmath
from .pandoc_engine import (MC_NS, OMML_NS, WML_NS, ConversionError, Equation, _LATEX_DOCUMENT, _api,
                            _collect_math, _has_math_delimiters, _run, _without_tex_comments, markdown_blocks,
                            markdown_maths, read_omml, split_math_paragraph, strip_tex)

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
    numbers: List[str] = field(default_factory=list)  # printed Word number of each numbered row

    def numbered_location(self, labels: Dict[str, str]) -> str:
        if self.numbers:
            return self.where + "  " + ", ".join(self.numbers)
        if labels and r"\label" in self.equation.tex:
            # Skip commented labels and escaped backslashes. TeX permits whitespace before the key.
            for m in re.finditer(r"%[^\n]*|\\label(?:\s|%[^\n]*\n)*\{([^{}]+)\}|\\.", self.equation.tex):
                key = m[1]
                if key in labels:
                    heading = re.sub(r"  \[[^{}]*\]$", "", self.where)
                    return f"{heading}  [{key}]  ({labels[key]})"
        return self.where


@dataclass
class EquationRow:
    number: int         # original position in the document, starting at 1
    found: Found
    where: str          # location including the number from LaTeX, when available
    count: int = 0      # occurrence count when grouped

    @property
    def location(self) -> str:
        return f"{self.where}  ({self.count}x)" if self.count else self.where


@dataclass
class DocumentEquations:
    path: str
    source: str  # "latex" when the equations are the document's own LaTeX, else "office"
    found: List[Found] = field(default_factory=list)
    notes: List[str] = field(default_factory=list)
    # a .tex file: its own source and that of the local files it reads, for the definitions pandoc does
    # not apply (\DeclarePairedDelimiter, the \foreach alphabet idiom...); its \label numbers (.aux)
    definitions: str = ""
    labels: Dict[str, str] = field(default_factory=dict)
    # read for Expand macros: the preamble's definitions with an @ in them too (\text{a@b}; an \eqlabel made
    # of \@namedef, which is then kept as written with a note), which `definitions` leaves out
    expansion_definitions: str = ""

    def rows(self, display: bool = False, unique: bool = False, match: str = "") -> List[EquationRow]:
        """Matching occurrences in source order. Group equal LaTeX after applying both filters."""
        rows: List[EquationRow] = []
        first: Dict[str, EquationRow] = {}
        needle = match.casefold()
        for number, found in enumerate(self.found, 1):
            eq = found.equation
            if display and not eq.display:
                continue
            where = found.numbered_location(self.labels)
            if needle and needle not in eq.tex.casefold() and needle not in where.casefold():
                continue
            if unique and eq.tex in first:
                first[eq.tex].count += 1
                continue
            row = EquationRow(number, found, where, 1 if unique else 0)
            rows.append(row)
            if unique:
                first[eq.tex] = row
        return rows

    def macros(self, user: Optional[compat.Macros]) -> Optional[compat.Macros]:
        """The macros for this document's equations: the user's files apply to a document's own LaTeX
        (with its own definitions, which win), not to equations read from Office."""
        if self.source != "latex":
            return None
        return compat.with_document(user, self.definitions, self.labels, preamble=self.expansion_definitions)


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
        # An OMML fragment's place (and Word's number marks it had), or an equation read from a shape's tags.
        self.entries: List[Union[Tuple[str, str, bool, List[str]], Found]] = []
        self.legacy = 0
        self.pictures: Dict[str, int] = {}
        self.notes: List[str] = []

    def add(self, math: ET.Element, where: str, context: str, display: bool = False, word: bool = False) -> None:
        if word:
            math = wordmath.revision_view(math)
        for piece in split_math_paragraph(math):  # several equations in one math paragraph
            numbers = wordmath.strip_numbers(piece) if word else []
            self.fragments.append(piece)
            self.entries.append((where, context, display or piece.tag == _q(OMML_NS, "oMathPara"), numbers))

    def result(self, path: str) -> DocumentEquations:
        from .convert import tidied, _UNICODE_NOTE

        result = DocumentEquations(path, "office", notes=list(self.notes))
        native = iter(_read_fragments(self.fragments))
        for entry in self.entries:
            if isinstance(entry, Found):
                result.found.append(entry)
                continue
            where, context, display, numbers = entry
            eq = next(native)
            if eq is None:
                result.notes.append("an equation in %s could not be read" % where)
                continue
            # A standalone equation on a slide is display maths even in a bare m:oMath.
            eq = tidied(Equation(eq.tex, eq.display or display))
            result.found.append(Found(eq, where, context, numbers))
            if not eq.tex.isascii() and _UNICODE_NOTE not in result.notes:
                result.notes.append(_UNICODE_NOTE)
        if self.legacy:
            result.notes.append("%d Equation Editor 3.0 / MathType object(s) cannot be read (no OMML inside)"
                                % self.legacy)
        if self.pictures:
            result.notes.append(", ".join("%d %s picture%s" % (count, addin, "" if count == 1 else "s")
                                          for addin, count in self.pictures.items()))
        return result


def _read_fragments(fragments: List[ET.Element]) -> List[Optional[Equation]]:
    """The equations of OMML fragments, one per fragment: all in one pandoc
    run, or one by one if the counts do not match (a fragment texmath skips: None)."""
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
    return eqs


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
            collector.add(el, where or state["heading"] or "(before the first heading)", _context(para, WML_NS, el),
                          word=True)
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
        if r.get("TargetMode") == "External":
            continue
        target = r.get("Target") or ""
        resolved = target.lstrip("/") if target.startswith("/") else posixpath.normpath(posixpath.join(folder, target))
        out.append((r.get("Id"), r.get("Type") or "", resolved))
    return out


def _slide_paths(z: zipfile.ZipFile) -> List[str]:
    """The slide parts in presentation order (the file names need not follow it)."""
    targets = {rid: target for rid, _, target in _targets(z, "ppt/presentation.xml")}
    pres = _parse(z.read("ppt/presentation.xml"))
    return [targets[s.get(_q(R_NS, "id"))] for s in pres.iter(_q(P_NS, "sldId")) if s.get(_q(R_NS, "id")) in targets]


def _picture_text(source: str, template: bool) -> str:
    """Extract the LaTeX from a shape or the formula field of a TeXPoint template."""
    text = source.replace("\r\n", "\n").replace("\r", "\n")
    if template:
        fields = text.split("\t")
        if len(fields) >= 4:
            text = fields[3]
        else:
            match = re.search(r"equation(.*?)template TP", text, re.I | re.S)
            text = match.group(1) if match else ""
        if not text.strip():
            raise ConversionError("the TeXPoint template contains no equation")
        text = "$" + text.strip() + "\n$"
    if "\\DeclareMathOperator" in text:
        text, _ = compat.before_equations(text, defer=False)
    return text


def _picture_equations(text: str, notes: List[str], blocks: Optional[list] = None) -> List[Equation]:
    """Read a shape's equations with only that shape's preamble and definitions."""
    warnings, preamble = "", None
    if blocks is None:
        blocks, warnings, preamble = _read_latex_blocks(text, None, notes)
    notes.extend(line.removeprefix("[WARNING]").strip() for line in warnings.splitlines()
                 if line.startswith("[WARNING]"))
    equations: List[Equation] = []
    _collect_math(blocks, equations)
    if not equations:
        plain = _without_tex_comments(text).strip()
        if plain and not warnings and not _LATEX_DOCUMENT.search(plain) and not _has_math_delimiters(plain):
            equations.append(Equation(strip_tex(text)))  # a single bare formula, as in read_latex_text
    # The full LaTeX reader applies ordinary preamble definitions. If it only read the body, add
    # those too. Paired delimiters and package commands still need expanding in either case.
    macros = compat.Macros(text=(preamble or "") + "\n" + compat.pandoc_leftovers(text))
    expanded = []
    for eq in equations:
        tex, new = compat.expand_macros(eq.tex, macros)
        if tex.strip():
            expanded.append(Equation(tex, eq.display))
        notes.extend(n for n in new if n not in notes)
    if not expanded:
        raise ConversionError("no equations found")
    return expanded


def _picture_blocks(texts):
    """Read independent LaTeX sources together. Leave warnings and failed reads to the individual reader."""
    if len(texts) < 2:
        return {}
    groups = [[]]
    token = uuid.uuid4().hex
    try:
        with tempfile.TemporaryDirectory() as folder:
            # With file scope, each preamble applies only to its own input. Pandoc concatenates the
            # resulting blocks, so put a separate one-word document between neighbouring inputs.
            marker = token + ".tex"
            with open(os.path.join(folder, marker), "w", encoding="ascii") as f:
                f.write(token)
            args = ["-f", "latex", "-t", "json", "--sandbox", "--file-scope"]
            for i, text in enumerate(texts.values()):
                if i:
                    args.append(marker)
                filename = "%s-%d.tex" % (token, i)
                with open(os.path.join(folder, filename), "wb") as f:
                    f.write(text.encode("utf-8"))
                args.append(filename)
            out, warnings = _run(args, b"", cwd=folder)
    except (ConversionError, OSError):
        return {}
    if warnings:
        return {}
    separator = {"t": "Para", "c": [{"t": "Str", "c": token}]}
    for block in json.loads(out)["blocks"]:
        if block == separator:
            groups.append([])
        else:
            groups[-1].append(block)
    return dict(zip(texts, groups)) if len(groups) == len(texts) else {}


def _resolve_pictures(collector, pictures):
    """Read each distinct source once and put its equations back among the native equations."""
    keys = list(dict.fromkeys(key for _, key, _, _ in pictures))
    cache = {}
    for i in range(0, len(keys), 256):  # keep the file list within Windows' command line limit
        texts = {}
        for key in keys[i:i + 256]:
            try:
                texts[key] = _picture_text(*key)
            except ConversionError as e:
                cache[key] = [], ["could not read the LaTeX: " + _shorten(str(e), 140)]
        blocks = _picture_blocks(texts)
        for key, text in texts.items():
            notes = []
            try:
                equations = _picture_equations(text, notes, blocks.get(key))
            except ConversionError as e:
                equations = []
                notes.append("could not read the LaTeX: " + _shorten(str(e), 140))
            cache[key] = equations, notes

    written = compat._NOTES["written"].split("%s")[0]
    entries = []
    last = 0
    for index, key, where, name in pictures:
        entries.extend(collector.entries[last:index])
        last = index
        equations, notes = cache[key]
        for eq in equations:
            entries.append(Found(Equation(eq.tex, eq.display), where, name))
        for note in notes:
            if not note.startswith(written):
                collector.notes.append("%s: %s" % (where + (" " + name if name else ""), note))
    collector.entries = entries + collector.entries[last:]


_SHAPES = {_q(P_NS, name) for name in ("pic", "grpSp", "sp")}
_TAG_REF = "*/%s/%s/%s" % (_q(P_NS, "nvPr"), _q(P_NS, "custDataLst"), _q(P_NS, "tags"))


def _read_picture(z: zipfile.ZipFile, shape: ET.Element, tag_paths: Dict[str, str], where: str,
                  collector: _Collector, pictures: list) -> bool:
    """Record the shape's source and return whether the shape is an add-in picture."""
    ref = shape.find(_TAG_REF)
    if ref is None:
        return False
    props = shape.find("*/" + _q(P_NS, "cNvPr"))
    name = _shorten(props.get("name", "")) if props is not None else ""
    try:
        part = tag_paths[ref.get(_q(R_NS, "id"), "")]
        root = _parse(z.read(part))
    except (KeyError, ET.ParseError, UnicodeDecodeError, zipfile.BadZipFile):
        collector.notes.append("%s: could not read the tags for %s" % (where, name or "a shape"))
        return False
    tags = {tag.get("name"): tag.get("val", "") for tag in root.findall(_q(P_NS, "tag"))}
    if "LATEXADDIN" in tags:
        addin, source = "IguanaTeX", tags["LATEXADDIN"]
    elif "SOURCE" in tags:
        addin, source = "TeXPoint", tags["SOURCE"]
    else:
        return False
    collector.pictures[addin] = collector.pictures.get(addin, 0) + 1
    location = "%s (%s)" % (where, addin)
    template = addin == "TeXPoint" and tags.get("TEXPOINT", "").strip().lower() == "template"
    pictures.append((len(collector.entries), (source, template), location, name))
    return True


def _walk_slide_part(z: zipfile.ZipFile, root: ET.Element, targets: List[Tuple[str, str, str]], where: str,
                     collector: _Collector, pictures: list, notes: bool = False) -> None:
    state = {"paras": []}
    tag_paths = {rid: target for rid, kind, target in targets if kind == R_NS + "/tags"}

    def visit(el):
        if el.tag in _SHAPES and _read_picture(z, el, tag_paths, where, collector, pictures):
            if el.tag == _q(P_NS, "grpSp"):
                return False  # the children draw the tagged group's equation
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
    pictures = []
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
            targets = _targets(z, slide)
            _walk_slide_part(z, root, targets, where, collector, pictures)
            for _, kind, target in targets:
                if kind.endswith("/notesSlide") and target in names:
                    _walk_slide_part(z, _parse(z.read(target)), _targets(z, target), "slide %d notes" % number,
                                     collector, pictures, notes=True)
    if pictures:
        _resolve_pictures(collector, pictures)
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


_READS = re.compile(r"\\(input|include|subfile|usepackage|RequirePackage)\s*(?:\[[^\]]*\])?\s*\{([^{}]+)\}")
# \newlabel{key}{{number}{page}{title}{anchor}{}} (hyperref); without hyperref only {number}{page};
# cleveref adds \newlabel{key@cref}{{[equation][7][]7}{[1][1][]1}}
_NEWLABEL_START = re.compile(r"\\newlabel\{([^{}]+)\}\s*\{")
_AUX_INPUT = re.compile(r"\\@input\{([^{}]+)\}")
# \bibcite{key}{12}, or natbib's \bibcite{key}{{12}{2020}{{Smith et~al.}}{{Smith, Jones, and Lee}}} (its
# argument read by brace depth: a name holds {\"o})
_BIBCITE = re.compile(r"\\bibcite\{([^{}]+)\}\s*\{")
_TEX_ACCENTS = {'"': "\u0308", "'": "\u0301", "`": "\u0300", "^": "\u0302", "~": "\u0303", "=": "\u0304",
                ".": "\u0307", "c": "\u0327", "v": "\u030c", "u": "\u0306", "H": "\u030b", "r": "\u030a", "k": "\u0328"}
_TEX_LETTERS = {"ss": "ß", "o": "ø", "O": "Ø", "ae": "æ", "AE": "Æ", "oe": "œ", "OE": "Œ", "aa": "å", "AA": "Å",
                "l": "ł", "L": "Ł", "i": "ı", "j": "ȷ"}
_ACCENTED = re.compile(r"\\([\"'`^~=.])\s*(?:\{\s*(\\?[A-Za-z]+)\s*\}|(\\[A-Za-z]+|[A-Za-z]))"
                       r"|\\([cvuHrk])(?:\s*\{\s*(\\?[A-Za-z]+)\s*\}|\s+([A-Za-z]))")


def _group_end(text: str, start: int) -> int:
    """Index after the {...} group whose { is at `start` (-1 if it does not close)."""
    depth, i = 0, start
    while i < len(text):
        ch = text[i]
        if ch == "\\":
            i += 2
            continue
        depth += {"{": 1, "}": -1}.get(ch, 0)
        i += 1
        if not depth:
            return i
    return -1


def _groups(text: str) -> List[str]:
    """The contents of the top-level {...} groups of `text`, in order."""
    groups, depth, start = [], 0, 0
    for i, ch in enumerate(text):
        if ch == "{":
            depth += 1
            if depth == 1:
                start = i + 1
        elif ch == "}" and depth:
            depth -= 1
            if not depth:
                groups.append(text[start:i])
    return groups


def _aux_text(value: str) -> str:
    """Text from an .aux field as it prints: accents ({\\"o} as ö), \\ss and the like, ~, \\&, no braces."""
    import unicodedata

    def accent(m: "re.Match") -> str:
        mark = _TEX_ACCENTS[m.group(1) or m.group(4)]
        base = next(g for g in (m.group(2), m.group(3), m.group(5), m.group(6)) if g)
        base = {"\\i": "i", "\\j": "j"}.get(base, base.lstrip("\\"))
        return unicodedata.normalize("NFC", base[:1] + mark + base[1:])
    value = _ACCENTED.sub(accent, value)
    value = re.sub(r"\\(ss|ae|AE|oe|OE|aa|AA|o|O|l|L|i|j)(?![A-Za-z])\s*", lambda m: _TEX_LETTERS[m.group(1)], value)
    value = value.replace("~", " ").replace("\\&", "&").replace("\\ ", " ")
    return " ".join(value.replace("{", "").replace("}", "").split())


def _source_text(data: bytes) -> str:
    """A source file's text: as decode_text, but a style file in Latin-1 (an old author's é in a comment)
    is read all the same."""
    try:
        return decode_text(data)
    except UnicodeDecodeError:
        return data.decode("latin-1")


def _local_sources(path: str, text: str) -> str:
    """The document's text with the local files it reads (\\input, \\include, \\subfile, and a
    \\usepackage'd .sty next to it) put in where they are read (so that definitions after an \\input
    win over the file's), without comments and verbatim parts, for its definitions.  At most 300
    files of up to 4 MB."""
    folder = os.path.dirname(os.path.abspath(path))
    cache: Dict[str, Optional[str]] = {}
    size = [0]  # characters put in (a file \input twice is put in twice, as LaTeX reads it twice)

    def read(full: str) -> Optional[str]:
        if full not in cache:
            try:
                if os.path.getsize(full) > 4 << 20:
                    cache[full] = None
                else:
                    with open(full, "rb") as f:
                        cache[full] = _source_text(f.read())
            except OSError:
                cache[full] = None
        return cache[full]

    def expand(current: str, active: Tuple[str, ...]) -> str:
        current = compat.without_verbatim(re.sub(r"(?<!\\)((?:\\\\)*)%[^\n]*", r"\1", current))
        out, last = [], 0
        for m in _READS.finditer(current):
            package = m.group(1) in ("usepackage", "RequirePackage")
            inserted = []
            for name in m.group(2).split(","):
                name = name.strip()
                for candidate in ([name + ".sty"] if package else [name, name + ".tex"]) if name else []:
                    full = os.path.normcase(os.path.abspath(os.path.join(folder, candidate)))
                    if not os.path.isfile(full):
                        continue
                    # a file that includes itself (directly or not) once; a package once, as LaTeX loads it;
                    # at most 300 different files and 40 million characters in all
                    if full in active or (package and full in cache) or (full not in cache and len(cache) >= 300):
                        break
                    data = read(full)
                    if data is not None and size[0] + len(data) <= 40_000_000:
                        size[0] += len(data)
                        inserted.append(expand(data, active + (full,)))
                    break
            out.append(current[last:m.end()])
            out += ["\n" + part + "\n" for part in inserted]
            last = m.end()
        return "".join(out) + current[last:]
    return expand(text, (os.path.normcase(os.path.abspath(path)),))


def _document_bounds(text: str) -> Tuple[Optional[int], Optional[int]]:
    """Where the body starts and ends (after \\begin{document}, at \\end{document}), not counting
    those in comments or verbatim parts."""
    plain = compat.without_verbatim(text)
    begin = end = None
    for m in re.finditer(r"\\(begin|end)\s*\{document\}", plain):
        if compat._in_comment(plain, m.start()):
            continue
        if m.group(1) == "begin" and begin is None:
            begin = m.end()
        elif m.group(1) == "end" and begin is not None and end is None:
            end = m.start()
    return begin, end


def _aux_labels(path: str) -> Dict[str, str]:
    """\\label -> number from the .aux file LaTeX wrote next to the document (and the .aux files of its
    \\include s), plus "page:key" and "title:key" for \\pageref and \\nameref; empty without one."""
    labels: Dict[str, str] = {}
    folder = os.path.dirname(os.path.abspath(path))
    todo, seen = [os.path.splitext(os.path.abspath(path))[0] + ".aux"], set()
    while todo and len(seen) < 300:
        aux = todo.pop(0)
        if aux in seen or not os.path.isfile(aux):
            continue
        seen.add(aux)
        try:
            with open(aux, "rb") as f:
                data = f.read().decode("utf-8", "replace")
        except OSError:
            continue
        for m in _NEWLABEL_START.finditer(data):  # (its fields read by brace depth: a title holds $L^{2}$)
            end = _group_end(data, m.end() - 1)
            if end < 0:
                continue
            fields = _groups(data[m.end():end - 1]) + ["", "", "", ""]
            key, number = m.group(1), fields[0].strip()
            if key.endswith("@cref"):  # what the label is a number of (cleveref): "type:key"
                kind = re.match(r"\[([A-Za-z]+)\]", number)
                if kind:
                    labels["type:" + key[:-len("@cref")]] = kind.group(1)
                continue
            if number.startswith("{") and number.endswith("}") and len(_groups(number)) == 1:
                number = number[1:-1].strip()  # a \tag{T}'s label: {T}
            if "@cref" in key or not number or number.startswith("\\"):
                continue
            labels.setdefault(key, number)
            for field, value in (("page:", fields[1]), ("title:", fields[2])):
                value = (value or "").strip()
                if value and "\\" not in value:
                    labels.setdefault(field + key, value)
            anchor = re.match(r"([A-Za-z]+)\.", (fields[3] or "").strip())  # hyperref's equation.2.1
            if anchor:
                labels.setdefault("type:" + key, anchor.group(1))
        for m in _BIBCITE.finditer(data):  # a citation's number ("cite:key"); natbib's author and year too
            end = _group_end(data, m.end() - 1)
            if end < 0:
                continue
            key, value = m.group(1), data[m.end():end - 1].strip()
            fields = _groups(value) if value.startswith("{") else [value]
            number = fields[0].strip() if fields else ""
            if re.fullmatch(r"[\w.\-]{1,12}", number):
                labels.setdefault("cite:" + key, number)
            if len(fields) >= 3 and _aux_text(fields[1]) and _aux_text(fields[2]):
                labels.setdefault("citeyear:" + key, _aux_text(fields[1]))
                labels.setdefault("citeauthor:" + key, _aux_text(fields[2]))
        todo += [os.path.join(folder, n) for n in _AUX_INPUT.findall(data)]
    return labels


def _read_latex_blocks(text: str, folder: Optional[str], notes: List[str], raw: bool = False,
                       meta: Optional[dict] = None) -> Tuple[list, str, Optional[str]]:
    """pandoc's reading of a .tex file; if pandoc cannot read the file (a style file it trips over:
    "unexpected \\@ ... \\maketitle"), the body alone, whose macros then come from the preamble.
    Returns the blocks, pandoc's warnings, and the preamble when the body was read alone.  `raw`: keep
    what the reader does not know as raw TeX (it drops it otherwise, without a word).  `meta`, if given,
    gets pandoc's metadata (title, authors, date, abstract). An embedded source has no folder from
    which to read other files."""
    fmt = "latex+raw_tex" if raw else "latex"
    flags = ["--sandbox"] if folder is None else []
    args = ["-f", fmt, "-t", "json"] + flags
    try:
        out, err = _run(args, text.encode("utf-8"), cwd=folder)
        document = json.loads(out)
        if meta is not None:
            meta.update(document.get("meta") or {})
        return document["blocks"], err, None
    except ConversionError as e:
        begin, end = _document_bounds(text)
        if begin is None:
            raise
        body = text[begin:end if end is not None else len(text)]
        out, err = _run(args, body.encode("utf-8"), cwd=folder)
        reason = next((line.strip() for line in str(e).splitlines() if line.strip() and "WARNING" not in line), "")
        notes.append("pandoc could not read the preamble (%s): the body was read alone, with the preamble's "
                     "definitions." % _shorten(reason, 120))
        document = json.loads(out)
        if meta is not None:
            meta.update(document.get("meta") or {})
            front = _front_matter(text[:begin])
            if front:  # the title, authors and date of the preamble pandoc could not read, read alone
                try:
                    meta.update(json.loads(_run(["-f", "latex", "-t", "json"] + flags, (
                        front + "\n\\begin{document}\\end{document}").encode("utf-8"), cwd=folder)[0]).get("meta") or {})
                except (ConversionError, ValueError):
                    pass
        return document["blocks"], err, text[:begin]


_FRONT = re.compile(r"\\(title|author|date)\s*(?:\[[^\]]*\])?\s*\{")


def _front_matter(preamble: str) -> str:
    """The \\title{...}, \\author{...} and \\date{...} commands of a preamble (comments out)."""
    preamble = re.sub(r"(?<!\\)%[^\n]*", "", preamble)
    found = []
    for m in _FRONT.finditer(preamble):
        depth, i = 1, m.end()
        while i < len(preamble) and depth:
            if preamble[i] == "\\":
                i += 2
                continue
            depth += {"{": 1, "}": -1}.get(preamble[i], 0)
            i += 1
        if not depth:
            found.append("\\%s{%s}" % (m.group(1), preamble[m.end():i - 1]))
    return "\n".join(found)


def read_text_document(path: str, fmt: str, expand_macros: bool = False) -> DocumentEquations:
    """A .tex file (\\input and \\include found next to it) or a .md file, read by pandoc: each
    equation as LaTeX, with the heading above it and its \\label; pandoc's warnings (a file that
    \\input names but that is not there) become notes.  A .tex file also gets its own definitions and
    \\label numbers (see :class:`DocumentEquations`)."""
    folder = os.path.dirname(os.path.abspath(path))
    with open(path, "rb") as f:
        text = _source_text(f.read())  # (an old paper in Latin-1 too)
    result = DocumentEquations(path, "latex")
    # a .md file: pandoc's Markdown, where \[1\] is an escaped bracket (pandoc writes [1] so), not maths
    # as in pasted chat text (MARKDOWN); raw TeX environments in it are read all the same
    markdown = fmt == "markdown"
    raw_equations: List[Equation] = []
    if markdown:  # (with money before maths read right: "$5.00 ($p$ after tax)")
        blocks, err = markdown_blocks(text, "markdown", folder)
        # what pandoc's Markdown reader does not apply either (\DeclarePairedDelimiter...), not from code
        from .pandoc_engine import _without_code
        result.definitions = compat.pandoc_leftovers(_without_code(text))
    else:
        read_text = text
        if expand_macros and "\\DeclareMathOperator" in text:
            read_text, _ = compat.before_equations(text, defer=False)
        blocks, err, preamble = _read_latex_blocks(read_text, folder, result.notes)
        # what pandoc did not apply (\DeclarePairedDelimiter...); with the body read alone, also the whole
        # preamble's definitions, which pandoc did not see
        result.definitions = compat.pandoc_leftovers(_local_sources(path, text))
        if preamble is not None:
            result.definitions = _local_sources(path, preamble) + "\n" + result.definitions
        if expand_macros and preamble is None:
            begin, end = _document_bounds(text)
            body, leading = compat.before_equations(text[begin:end])
            if begin is not None:
                local = _local_sources(path, text[:begin])
                # also those with an @ in what they stand for (\text{a@b}, or \eqlabel made of \@namedef,
                # which expand_macros then keeps as written, with a note); not internal names (\@foo)
                top = [d.source for d in compat.parse_definitions(local)[0]
                       if "@" not in d.name and not compat._depth(local, d.span[0])]
                leading = "\n".join(top + [leading])
                result.expansion_definitions = "\n".join(s for s in top if "@" in s)
            if leading.strip():
                # Keep the raw macro calls for the LaTeX writer. Some macros produce whole
                # equations in prose, so use the full reading if the body alone loses any.
                result.definitions = leading + "\n" + result.definitions
                try:
                    raw_blocks, _, _ = _read_latex_blocks(body, folder, [])
                except ConversionError:
                    pass
                else:
                    original: List[Equation] = []
                    raw: List[Equation] = []
                    _collect_math(blocks, original)
                    _collect_math(raw_blocks, raw)
                    raw_equations = _matched(original, raw)
        result.labels = _aux_labels(path)
    result.notes += [" ".join(line.replace("[WARNING]", "").split()) for line in err.splitlines()
                     if line.startswith("[WARNING]")]
    definitions: List[str] = []  # the raw TeX definitions so far (a .md file)
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
            markdown_maths(block, eqs, definitions, folder)
        else:
            _collect_math(block, eqs)  # also the maths of a heading
        for eq in eqs:
            label = re.search(r"\\label\{([^{}]*)\}", eq.tex)
            where = (heading or "(before the first heading)") + ("  [%s]" % label.group(1) if label else "")
            if len(result.found) < len(raw_equations) and raw_equations[len(result.found)] is not None:
                eq = raw_equations[len(result.found)]
            result.found.append(Found(eq, where, "" if eq.display else context))
    return result


def _shape(eq: Equation) -> tuple:
    """An equation as a macro leaves it: inline or display, its environment, and what it shows apart from
    commands (letters and digits), which tells two equations of the same kind apart."""
    env = re.match(r"\s*\\begin\s*\{([^{}]*)\}", eq.tex)
    return eq.display, env.group(1) if env else "", _skeleton(eq.tex)


def _skeleton(tex: str) -> str:
    return re.sub(r"[^A-Za-z0-9]", "", re.sub(r"\\(?:[a-zA-Z@]+|.)", "", tex))


def _matched(expanded: List[Equation], raw: List[Equation]) -> "List[Optional[Equation]]":
    """For each equation of pandoc's reading (macros expanded), the same equation as written, from the
    reading without the preamble (macros kept), or None where the two do not line up: a macro in the
    prose that makes an equation of its own is in the first reading only.  Equations that differ only
    where macros were expanded pair up when they are of the same kind and mostly show the same."""
    import difflib
    out: "List[Optional[Equation]]" = [None] * len(expanded)
    a, b = [_shape(e) for e in expanded], [_shape(e) for e in raw]
    matcher = difflib.SequenceMatcher(None, a, b, autojunk=False)
    for tag, i1, i2, j1, j2 in matcher.get_opcodes():
        if tag == "equal":
            pairs = zip(range(i1, i2), range(j1, j2))
        elif tag == "replace" and i2 - i1 == j2 - j1:
            pairs = ((i, j) for i, j in zip(range(i1, i2), range(j1, j2))
                     if a[i][:2] == b[j][:2]
                     and difflib.SequenceMatcher(None, a[i][2], b[j][2], autojunk=False).ratio() >= 0.5)
        else:
            continue
        for i, j in pairs:
            out[i] = raw[j]
    return out


def read_document(path: str, *, expand_macros: bool = False) -> DocumentEquations:
    """Every equation in document order. For expanded LaTeX, retain preamble macros for the writer."""
    ext = os.path.splitext(path)[1].lower()
    if ext not in READABLE:
        raise ConversionError("Cannot read %s files (only %s)." % (ext or "these", ", ".join(READABLE)))
    try:
        if ext == ".docx":
            return read_docx(path)
        if ext == ".pptx":
            return read_pptx(path)
        return read_text_document(path, "latex" if ext == ".tex" else "markdown", expand_macros)
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


# the TeX keeps its line breaks (continuation lines indented by two spaces), then texmath's message
_UNCONVERTED = re.compile(r"\[WARNING\] Could not convert TeX math ([\s\S]*?), rendering as TeX:\n((?:  .*\n?)*)")


def _unconverted(err: str, prepared: List[str]) -> Dict[int, str]:
    """Which equations (1-based) pandoc wrote as TeX text, with texmath's reason, from its warnings
    ("[WARNING] Could not convert TeX math <tex>, rendering as TeX:" then texmath's message)."""
    by_text: Dict[str, str] = {}
    for m in _UNCONVERTED.finditer(err.replace("\r\n", "\n")):
        lines = [ln.strip() for ln in m.group(2).split("\n") if ln.strip()]
        reason = next((b for a, b in zip(lines, lines[1:]) if a.startswith("^") and not b.startswith("^")), "")
        by_text.setdefault(" ".join(m.group(1).split()), reason)
    out: Dict[int, str] = {}
    for i, tex in enumerate(prepared, 1):
        key = " ".join(tex.split())
        if key in by_text:
            out[i] = by_text[key] or "texmath could not read it"
    return out


def write_docx(doc: DocumentEquations, path: str, macros: Optional[compat.Macros] = None,
               rows: Optional[List[EquationRow]] = None) -> List[str]:
    """The document's equations (`rows`, all of them by default) as a Word document (`path`), each a
    native equation under a line that says where it was, as the list shows it ("[3] Method  [eq:x]
    (2.1)": its place in the document, its LaTeX number), in one pandoc run; for a collaborator who
    edits them in Word.  Macros apply to a document's own LaTeX.  Returns notes: equations that texmath
    cannot write are in the file as LaTeX text.  The file is written next to `path` first and then put
    in its place, so a failed export leaves an older file as it was.  An equation that texmath cannot
    write is in the file as LaTeX text, under its line with the reason."""
    own = doc.source == "latex"
    used = doc.macros(macros)
    rows = doc.rows() if rows is None else rows
    notes: List[str] = []
    tags = False
    prepared: List[str] = []
    for found in [row.found for row in rows]:
        try:
            tex, new = compat.for_texmath(found.equation.tex, used, "omml")
        except ConversionError:  # (a definition compat cannot use): as it is, pandoc writes it as text
            tex, new = found.equation.tex, []
        if own:  # what was approximated (a colour, a resized fraction...); LaTeX from Office has none
            notes += [n for n in new if n not in notes]
            # (a % after an even number of backslashes starts a comment: \\% \tag{old} is one)
            active = re.sub(r"(?<!\\)((?:\\\\)*)%[^\n]*", r"\1", tex)
            tags |= bool(re.search(r"\\tag(?![a-zA-Z@])", active))
        prepared.append(tex)

    def source(reasons: Dict[int, str]) -> bytes:
        blocks: list = [{"t": "Header", "c": [1, ["", [], []],
                                              _inlines("Equations of " + os.path.basename(doc.path))]}]
        for i, (row, tex) in enumerate(zip(rows, prepared), 1):
            kind = "DisplayMath" if row.found.equation.display else "InlineMath"
            line = [{"t": "Emph", "c": _inlines("[%d] %s" % (row.number, row.location))}]
            if i in reasons:
                line += [{"t": "Space"}, {"t": "Strong", "c": _inlines(
                    "(not converted: %s; it is here as LaTeX text)" % reasons[i])}]
            blocks.append({"t": "Para", "c": line})
            blocks.append({"t": "Para", "c": [{"t": "Math", "c": [{"t": kind}, tex]}]})
        return json.dumps({"pandoc-api-version": _api(), "meta": {}, "blocks": blocks}).encode("utf-8")

    target = os.path.abspath(path)
    try:  # a name of its own next to the target (two exports at once do not meet)
        handle, part = tempfile.mkstemp(dir=os.path.dirname(target), prefix="." + os.path.basename(target) + ".",
                                        suffix=".part")
        os.close(handle)
    except OSError as e:
        raise ConversionError("Could not write %s: %s" % (path, e.strerror or e))
    try:
        _, err = _run(["-f", "json", "-t", "docx", "-o", part], source({}))
        reasons = _unconverted(err, prepared)
        from . import diagnose
        for i in sorted(reasons)[:50]:  # in plain words (a probe each: not for hundreds of them)
            words = diagnose.explain(rows[i - 1].found.equation.tex, prepared[i - 1], reasons[i], short=True)
            reasons[i] = words[0] if words else reasons[i]
        if reasons:  # once more, with the reason above each equation that stays LaTeX text
            _, err = _run(["-f", "json", "-t", "docx", "-o", part], source(reasons))
        for attempt in range(5):
            try:
                os.replace(part, target)
                break
            except PermissionError:  # another export replacing it this moment, or open in Word
                if attempt == 4:
                    raise
                time.sleep(0.1)
    except OSError as e:  # e.g. the file is open in Word
        raise ConversionError("Could not write %s: %s" % (path, e.strerror or e))
    finally:
        try:
            os.remove(part)
        except OSError:
            pass  # moved into place, or held for a moment by a virus scanner
    failed = len(re.findall(r"^\[WARNING\] Could not convert TeX math", err, re.M))
    if failed:
        notes.append("%d equation%s could not be converted; %s in the file as LaTeX text, under %s line with "
                     "the reason.%s"
                     % (failed, "" if failed == 1 else "s", "it is" if failed == 1 else "they are",
                        "its" if failed == 1 else "their",
                        " Definitions from a file that the document does not read itself can be given as a "
                        "macro file (Macros..., --macros)." if own else ""))
    if tags:
        notes.append("\\tag numbers are not carried over: number the equations in Word if they need numbers.")
    return compat.merge_notes(notes)
