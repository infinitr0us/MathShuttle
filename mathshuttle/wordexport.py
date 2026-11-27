"""A passage (the window's input: a chat answer, Markdown notes, LaTeX text) or a whole .tex / .md document
as a Word file: its prose, headings, lists and tables through pandoc's docx writer, and each equation a
native Word equation, prepared as Copy for Word/PowerPoint prepares it (the user's macros, the document's
own definitions, the built-in packs).

Numbers: an equation's \\tag{..}, and the number LaTeX gave a \\label (from the .aux next to a .tex file),
go at the right margin as Word numbers equations itself: ``#(2.1)`` at the end of a row of an equation
array with maximum distribution (checked with Word 16.0.20527).  \\eqref / \\ref in the text and in the
equations show the number.  An equation that texmath cannot write stays LaTeX text, with the reason after it.
"""
from __future__ import annotations

import json
import os
import re
import tempfile
import time
import zipfile
from typing import Dict, List, Optional, Tuple

from . import compat
from .pandoc_engine import MARKDOWN, ConversionError, _api, _run, markdown_blocks

_SENTINEL = "QQMSN%dQQ"
_SENTINEL_RUN = re.compile(r"<m:r>(?:(?!</m:r>)[\s\S])*?QQMSN(\d+)QQ(?:(?!</m:r>)[\s\S])*?</m:r>")
_LABEL = re.compile(r"\\label\s*\{([^{}]*)\}")
_TAG = re.compile(r"\\tag(\*?)\s*\{([^{}]*)\}")
_REFERENCE_TYPES = {"eqref": "(%s)", "ref": "%s", "autoref": "%s", "cref": "%s", "Cref": "%s"}
_RAW_TEX = ("tex", "latex")
_BLOCK_TYPES = frozenset("Para Plain Header CodeBlock BlockQuote OrderedList BulletList DefinitionList Table Div "
                         "HorizontalRule LineBlock RawBlock Figure".split())


# ---- the passage or document, as pandoc JSON ---------------------------------------------------------
def passage_blocks(text: str) -> Tuple[list, bool]:
    """The window's input as pandoc blocks, read as the window reads it (LaTeX, or Markdown for a chat
    answer: :func:`convert.input_equations`), and whether it was read as Markdown.  A bare equation
    (no delimiters) is one display equation."""
    from .convert import input_equations
    from .pandoc_engine import _has_math_delimiters, strip_tex
    eqs = input_equations(text, "latex")
    if eqs.as_markdown:
        return markdown_blocks(text, MARKDOWN)[0], True
    if not _has_math_delimiters(text):
        return [{"t": "Para", "c": [{"t": "Math", "c": [{"t": "DisplayMath"}, strip_tex(text)]}]}], False
    return json.loads(_run(["-f", "latex", "-t", "json"], text.encode("utf-8"))[0])["blocks"], False


def passage_to_word(text: str, path: str, macros: Optional[compat.Macros] = None) -> List[str]:
    """The window's input (`text`, LaTeX or a chat answer / Markdown) as a Word document at `path`."""
    from .convert import input_macros
    blocks, as_markdown = passage_blocks(text)
    return write_word(blocks, path, input_macros(macros, text, "latex", as_markdown), markdown=as_markdown)


def document_to_word(source: str, path: str, macros: Optional[compat.Macros] = None) -> List[str]:
    """A whole .tex or .md file as a Word document at `path`; a .tex file with its own definitions and the
    numbers of its .aux file."""
    from . import documents
    ext = os.path.splitext(source)[1].lower()
    if ext not in (".tex", ".md"):
        raise ConversionError("Only a .tex or .md file can be written as a whole Word document "
                              "(a .docx / .pptx: use Export all as .docx... for its equations).")
    folder = os.path.dirname(os.path.abspath(source))
    try:
        with open(source, "rb") as f:
            text = documents.decode_text(f.read())
    except OSError as e:
        raise ConversionError("Cannot read %s: %s" % (source, e.strerror or e))
    notes: List[str] = []
    if ext == ".md":
        blocks, err = markdown_blocks(text, "markdown", folder)
        own = macros
        labels: Dict[str, str] = {}
    else:
        # (as pandoc reads it: with raw_tex, pandoc would no longer follow \input; a command it does not
        # know is left out, as pandoc's own conversions do)
        blocks, err, preamble = documents._read_latex_blocks(text, folder, notes)
        definitions = compat.pandoc_leftovers(documents._local_sources(source, text))
        if preamble is not None:
            definitions = documents._local_sources(source, preamble) + "\n" + definitions
        labels = documents._aux_labels(source)
        own = compat.with_document(macros, definitions, labels)
    notes += [" ".join(line.replace("[WARNING]", "").split()) for line in err.splitlines()
              if line.startswith("[WARNING]") and "Could not convert TeX math" not in line]
    return write_word(blocks, path, own, labels, notes, folder, markdown=ext == ".md")


# ---- preparing the blocks -----------------------------------------------------------------------------
class _Prepare:
    """Walks pandoc JSON: raw TeX (in Markdown) read as LaTeX, each Math prepared for texmath with its
    numbers marked by sentinels, references replaced by their numbers."""

    def __init__(self, macros: Optional[compat.Macros], labels: Dict[str, str], folder: Optional[str],
                 markdown: bool = False):
        self.macros, self.labels, self.folder = macros, labels, folder
        # raw TeX in Markdown is read by the LaTeX reader; in a LaTeX document it is what that reader could
        # not read (\gls{..}, a package's command): kept as code, not dropped as pandoc's docx writer would
        self.markdown = markdown
        self.raw_kept = 0
        self.raw_dropped: Dict[str, int] = {}
        self.numbers: Dict[int, str] = {}       # sentinel id -> "(2.1)"
        self.maths: List[Tuple[dict, str, str]] = []  # (Math node, original TeX, prepared TeX)
        self.notes: List[str] = []
        self.definitions: List[str] = []        # raw TeX definitions so far (Markdown)
        self.unresolved = 0

    def blocks(self, blocks: list) -> list:
        out: list = []
        for block in blocks:
            if isinstance(block, dict) and block.get("t") == "RawBlock" and block["c"][0] in _RAW_TEX:
                if self.markdown:
                    out += self.raw_block(block["c"][1])
                else:  # a LaTeX block pandoc does not know (tikzpicture, algorithm): left out, and said so
                    env = re.match(r"\s*\\begin\s*\{([^{}]*)\}", block["c"][1])
                    name = env.group(1) if env else (re.match(r"\s*(\\[A-Za-z@]+)", block["c"][1]) or [None, "raw TeX"])[1]
                    self.raw_dropped[name] = self.raw_dropped.get(name, 0) + 1
            else:
                out.append(self.node(block))
        return out

    def raw_block(self, raw: str) -> list:
        """Raw TeX written into Markdown (\\begin{align} without $$, a \\newcommand): pandoc's docx writer
        would drop it; read by the LaTeX reader after the definitions before it."""
        if re.search(r"\\(?:re)?newcommand|\\def(?![a-zA-Z@])|\\DeclareMathOperator|\\let(?![a-zA-Z@])", raw):
            self.definitions.append(raw)
        try:
            source = "\n".join(self.definitions + [raw])
            read = json.loads(_run(["-f", "latex", "-t", "json"], source.encode("utf-8"), cwd=self.folder)[0])["blocks"]
        except ConversionError:
            return [{"t": "CodeBlock", "c": [["", [], []], raw]}]
        return self.as_latex(read)

    def as_latex(self, nodes):
        """Nodes the LaTeX reader gave (no second reading of the raw TeX it left)."""
        markdown, self.markdown = self.markdown, False
        try:
            return self.blocks(nodes) if isinstance(nodes, list) and nodes and isinstance(nodes[0], dict) \
                and nodes[0].get("t") in _BLOCK_TYPES else self.node(nodes)
        finally:
            self.markdown = markdown

    def node(self, node):
        if isinstance(node, list):
            return [self.node(n) for n in node]
        if not isinstance(node, dict):
            return node
        t = node.get("t")
        if t == "Math":
            self.math(node)
            return node
        if t == "Link":
            attrs = dict(node["c"][0][2])
            kind, key = attrs.get("reference-type"), attrs.get("reference", "")
            if kind in _REFERENCE_TYPES:
                keys = [k.strip() for k in key.split(",") if k.strip()]
                if keys and all(k in self.labels for k in keys):
                    shown = _REFERENCE_TYPES[kind] % ", ".join(self.labels[k] for k in keys)
                    return {"t": "Str", "c": shown}
                if _inline_text(node["c"][1]).strip() == "[%s]" % key:  # pandoc's placeholder: no number
                    self.unresolved += 1
        if t == "Cite":  # pandoc's docx writer drops the raw \cite in it: [key] or [12] from the .aux
            parts = []
            for c in node["c"][0]:
                shown = self.labels.get("cite:" + c["citationId"], c["citationId"])
                suffix = _inline_text(c.get("citationSuffix") or []).strip()
                parts.append(shown + (", " + suffix.lstrip(", ") if suffix else ""))
            return {"t": "Str", "c": "[%s]" % "; ".join(parts) if any("," in p for p in parts) else
                    "[%s]" % ", ".join(parts)}
        if t == "RawInline" and node["c"][0] in _RAW_TEX:
            return self.raw_inline(node)
        if "c" in node:
            node = dict(node)
            node["c"] = self.node(node["c"])
        return node

    def raw_inline(self, node: dict):
        code = {"t": "Code", "c": [["", [], []], node["c"][1]]}
        if not self.markdown:
            self.raw_kept += 1
            return code
        try:
            read = json.loads(_run(["-f", "latex", "-t", "json"], node["c"][1].encode("utf-8"), cwd=self.folder)[0])["blocks"]
        except ConversionError:
            return code
        if len(read) == 1 and read[0].get("t") in ("Para", "Plain"):
            return {"t": "Span", "c": [["", [], []], self.as_latex(read[0]["c"])]}
        return code

    def math(self, node: dict) -> None:
        kind, tex = node["c"]
        marked = tex
        if kind["t"] == "DisplayMath":
            marked = self.mark_numbers(tex)
        try:
            prepared, notes = compat.for_texmath(marked, self.macros, "omml")
            self.notes += [n for n in notes if n not in self.notes]
        except ConversionError:
            prepared = tex
        node["c"] = [kind, prepared]
        self.maths.append((node, tex, prepared))

    def mark_numbers(self, tex: str) -> str:
        """\\tag{x} and a \\label with an .aux number: a sentinel each (per row, where they are)."""
        def sentinel(shown: str) -> str:
            n = len(self.numbers) + 1
            self.numbers[n] = shown
            return "\\text{%s}" % (_SENTINEL % n)

        env = re.fullmatch(r"(\s*\\begin\s*\{([a-zA-Z]+\*?)\}(?:\s*\{[^{}]*\})?)([\s\S]*?)(\\end\s*\{\2\}\s*)", tex)
        head, body, tail = (env.group(1), env.group(3), env.group(4)) if env else ("", tex, "")
        if env and env.group(2).endswith("*"):
            return tex  # equation*, align*: no numbers (a \tag there is rare; texmath shows none)
        rows = re.split(r"(\\\\(?:\s*\[[^\]]*\])?)", body)  # a row's \tag wins over its \label
        for i in range(0, len(rows), 2):
            row = rows[i]
            tag = _TAG.search(row)
            if tag:
                row = _TAG.sub("", row, count=1)
                row = _LABEL.sub("", row)
                rows[i] = row + sentinel(tag.group(2) if tag.group(1) else "(%s)" % tag.group(2))
                continue
            label = next((m for m in _LABEL.finditer(row) if m.group(1) in self.labels), None)
            if label and not re.search(r"\\(?:nonumber|notag)(?![a-zA-Z@])", row):
                rows[i] = row[:label.start()] + row[label.end():] + sentinel("(%s)" % self.labels[label.group(1)])
        return head + "".join(rows) + tail


# ---- writing --------------------------------------------------------------------------------------------
def write_word(blocks: list, path: str, macros: Optional[compat.Macros] = None,
               labels: Optional[Dict[str, str]] = None, notes: Optional[List[str]] = None,
               folder: Optional[str] = None, markdown: bool = False) -> List[str]:
    """`blocks` (pandoc JSON; read from Markdown when `markdown`) as a Word document at `path`, written next
    to it first and then put in its place.  Returns notes (what was approximated, what could not be
    converted, numbers)."""
    from . import diagnose, documents
    prep = _Prepare(macros, labels or {}, folder, markdown)
    blocks = prep.blocks(blocks)
    notes = list(notes or []) + prep.notes

    def source() -> bytes:
        return json.dumps({"pandoc-api-version": _api(), "meta": {}, "blocks": blocks}).encode("utf-8")

    target = os.path.abspath(path)
    try:
        handle, part = tempfile.mkstemp(dir=os.path.dirname(target), prefix="." + os.path.basename(target) + ".",
                                        suffix=".part")
        os.close(handle)
    except OSError as e:
        raise ConversionError("Could not write %s: %s" % (path, e.strerror or e))
    try:
        _, err = _run(["-f", "json", "-t", "docx", "-o", part], source())
        failed = documents._unconverted(err, [p for _, _, p in prep.maths])
        if failed:  # again, each such equation as its LaTeX (without sentinels) and the reason after it
            for i in sorted(failed):
                node, original, prepared = prep.maths[i - 1]
                words = diagnose.explain(original, prepared, failed[i], short=True) if i <= 50 else []
                node["c"] = [node["c"][0], original]
                node["reason"] = words[0] if words else failed[i]
            blocks = _with_reasons(blocks)
            _, err = _run(["-f", "json", "-t", "docx", "-o", part], source())
        numbered = _number_equations(part, prep.numbers)
        for attempt in range(5):
            try:
                os.replace(part, target)
                break
            except PermissionError:  # open in Word, or a scanner holds it for a moment
                if attempt == 4:
                    raise
                time.sleep(0.1)
    except OSError as e:
        raise ConversionError("Could not write %s: %s" % (path, e.strerror or e))
    finally:
        try:
            os.remove(part)
        except OSError:
            pass
    if failed:
        notes.append("%d equation%s could not be converted; %s in the file as LaTeX text with the reason after "
                     "%s." % (len(failed), "" if len(failed) == 1 else "s", "it is" if len(failed) == 1 else "they are",
                              "it" if len(failed) == 1 else "them"))
    if numbered:
        notes.append("%d equation number%s at the right margin, as Word numbers equations." % (
            numbered, "" if numbered == 1 else "s"))
    if prep.unresolved:
        notes.append("%d reference%s without a number (no .aux file next to the document, or a label it does "
                     "not hold)." % (prep.unresolved, "" if prep.unresolved == 1 else "s"))
    if prep.raw_kept:
        notes.append("%d LaTeX command%s that pandoc does not know (a package's command) %s in the file "
                     "as code." % (prep.raw_kept, "" if prep.raw_kept == 1 else "s",
                                   "is" if prep.raw_kept == 1 else "are"))
    if prep.raw_dropped:
        notes.append("Left out, as pandoc does not know them: %s." % ", ".join(
            "%s (%d)" % (name, n) if n > 1 else name for name, n in sorted(prep.raw_dropped.items())))
    return compat.merge_notes(notes)


def _inline_text(nodes) -> str:
    """The plain text of pandoc inlines."""
    words: List[str] = []

    def walk(n):
        if isinstance(n, list):
            for v in n:
                walk(v)
        elif isinstance(n, dict):
            if n.get("t") == "Str":
                words.append(n["c"])
            elif n.get("t") in ("Space", "SoftBreak"):
                words.append(" ")
            elif "c" in n:
                walk(n["c"])
    walk(nodes)
    return "".join(words)


def _with_reasons(node):
    """The failed Math nodes (marked with "reason") followed by their reason, in bold."""
    if isinstance(node, list):
        out = []
        for n in node:
            out.append(_with_reasons(n))
            if isinstance(n, dict) and n.get("t") == "Math" and n.get("reason"):
                out += [{"t": "Space"}, {"t": "Strong", "c": [{"t": "Str", "c": "[not converted: %s]" % n["reason"]}]}]
        return out
    if isinstance(node, dict):
        clean = {k: v for k, v in node.items() if k != "reason"}
        if "c" in clean:
            clean["c"] = _with_reasons(clean["c"])
        return clean
    return node


# ---- numbers in the written file -------------------------------------------------------------------------
_TAG_TOKEN = re.compile(r"<(/?)m:(\w+)\b[^>]*?(/?)>")
_MAX_DIST = '<m:maxDist m:val="1"/>'


def _number_equations(docx: str, numbers: Dict[int, str]) -> int:
    """Replaces the sentinels in the written file by Word's own equation numbers; how many there are."""
    if not numbers:
        return 0
    with zipfile.ZipFile(docx) as z:
        files = {info: z.read(info.filename) for info in z.infolist()}
    names = {info.filename: info for info in files}
    xml = files[names["word/document.xml"]].decode("utf-8")
    count = [0]

    def omath(m: "re.Match") -> str:
        body = m.group(1)
        if "QQMSN" not in body:
            return m.group(0)
        return "<m:oMath>%s</m:oMath>" % _numbered(body, numbers, count)

    xml = re.sub(r"<m:oMath>([\s\S]*?)</m:oMath>", omath, xml)
    files[names["word/document.xml"]] = xml.encode("utf-8")
    with zipfile.ZipFile(docx, "w", zipfile.ZIP_DEFLATED) as z:
        for info, data in files.items():
            z.writestr(info, data)
    return count[0]


def _number_run(shown: str) -> str:
    """Word's number mark: # and the number, upright as Word sets its own numbers ((Q), not (𝑄))."""
    return ('<m:r><m:rPr><m:sty m:val="p"/></m:rPr><m:t>#%s</m:t></m:r>'
            % shown.replace("&", "&amp;").replace("<", "&lt;"))


def _numbered(body: str, numbers: Dict[int, str], count: List[int]) -> str:
    """An equation's OMML (inside m:oMath) with its sentinels made Word numbers: in an equation array at
    the end of their rows, else around the whole equation as a one-row array."""
    stripped = body.strip()
    if stripped.startswith("<m:eqArr>") and _top_level_end(stripped, 0) == len(stripped):
        rows = _rows(stripped)
        out, last = [], 0
        for start, end in rows:  # end: where the row's </m:e> starts
            row = stripped[start:end]
            ids = [int(i) for i in re.findall(r"QQMSN(\d+)QQ", row)]
            if ids:
                row = _SENTINEL_RUN.sub("", row) + _number_run(numbers[ids[0]])
                count[0] += 1
            out += [stripped[last:start], row]
            last = end
        array = "".join(out) + stripped[last:]
        if "<m:eqArrPr>" in array[:len("<m:eqArr><m:eqArrPr>")]:
            return array.replace("<m:eqArrPr>", "<m:eqArrPr>" + _MAX_DIST, 1)
        return array.replace("<m:eqArr>", "<m:eqArr><m:eqArrPr>%s</m:eqArrPr>" % _MAX_DIST, 1)
    ids = [int(i) for i in re.findall(r"QQMSN(\d+)QQ", body)]
    count[0] += 1
    return ("<m:eqArr><m:eqArrPr>%s</m:eqArrPr><m:e>%s%s</m:e></m:eqArr>"
            % (_MAX_DIST, _SENTINEL_RUN.sub("", body), _number_run(numbers[ids[0]])))


def _top_level_end(xml: str, start: int) -> int:
    """Index after the element that starts at `start` (its closing tag at the same depth)."""
    depth = 0
    for m in _TAG_TOKEN.finditer(xml, start):
        if m.group(3):  # <m:x/>
            continue
        depth += -1 if m.group(1) else 1
        if not depth:
            return m.end()
    return -1


def _rows(array: str) -> List[Tuple[int, int]]:
    """(start of content, start of </m:e>) of each row (a direct m:e child) of the m:eqArr `array`."""
    rows, depth, start = [], 0, None
    for m in _TAG_TOKEN.finditer(array):
        if m.group(3):
            continue
        closing, name = m.group(1), m.group(2)
        if not closing:
            depth += 1
            if depth == 2 and name == "e":
                start = m.end()
        else:
            if depth == 2 and name == "e" and start is not None:
                rows.append((start, m.start()))
                start = None
            depth -= 1
    return rows
