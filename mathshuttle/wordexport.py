"""A passage (the window's input: a chat answer, Markdown notes, LaTeX text) or a whole .tex / .md document
as a Word file: its title, authors, abstract, prose, headings, lists, tables and figures through pandoc's
docx writer, and each equation a native Word equation, prepared as Copy for Word/PowerPoint prepares it
(the user's macros, the document's own definitions, the built-in packs).

Numbers go at the right margin as Word numbers equations itself: ``#(2.1)`` at the end of a row of an
equation array with maximum distribution (checked with Word 16.0.20527).  A row's number is its \\tag, or
the one LaTeX gives it: counted as LaTeX counts (equation and multline one number, align, gather,
eqnarray, flalign and alignat one per row; \\nonumber, \\notag, \\tag and starred environments
counted out; checked against a real LaTeX run) in a document that numbers equations (1), (2)... in
order, and checked against the .aux file LaTeX wrote next to it when there is one.  A document that
numbers them by chapter or section, or sets the counter itself, gets the .aux numbers of its labelled
equations only.  \\eqref, \\ref, \\cref, \\Cref, \\autoref and citations show what LaTeX prints for them.
An equation that texmath cannot write stays LaTeX text, with the reason after it.
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
_TAG_START = re.compile(r"\\tag(\*?)\s*\{")
_NO_NUMBER = re.compile(r"\\(?:nonumber|notag)(?![a-zA-Z@])")
_PER_ROW = frozenset("align flalign alignat xalignat xxalignat gather eqnarray".split())
_ONE_NUMBER = frozenset(("equation", "multline"))
_ENV = re.compile(r"(\s*\\begin\s*\{([a-zA-Z]+)(\*?)\}(?:\s*\{[^{}]*\})?)([\s\S]*?)(\\end\s*\{\2\3\}\s*)")
_REFERENCE_TYPES = ("eqref", "ref", "autoref", "cref", "Cref", "ref+label", "ref+Label")
REF_COMMANDS = "\x00ref-commands:"  # + the keys of an \autoref / \cref: the commands, in source order
_RAW_TEX = ("tex", "latex")
_BLOCK_TYPES = frozenset("Para Plain Header CodeBlock BlockQuote OrderedList BulletList DefinitionList Table Div "
                         "HorizontalRule LineBlock RawBlock Figure".split())
# what makes a passage Markdown to Word, besides what makes its equations Markdown (looks_like_markdown)
_PROSE_SIGNS = re.compile(r"^[ \t]*(?:[-*+]|\d{1,3}[.)])[ \t]+\S|^[ \t]*>[ \t]?\S", re.M)
_CHAPTER_CLASS = re.compile(r"\\documentclass\s*(?:\[[^\]]*\])?\s*\{\s*(?:report|book|memoir|scrbook|scrreprt|amsbook"
                            r"|[^{}]*thesis[^{}]*|siamart[^{}]*|siamonline[^{}]*)\s*\}")


# ---- the passage or document, as pandoc JSON ---------------------------------------------------------
def _prose(text: str) -> bool:
    """Words, not an equation (for a passage without maths delimiters): no command, script, brace or
    operator, and three words or a sentence ("sin x cos y" and "AB + CD" are equations)."""
    if re.search(r"\\[A-Za-z]|[_^{}=+<>|*/]", text):
        return False
    words = len(re.findall(r"[A-Za-z]{2,}", text))
    return words >= 3 or (words >= 2 and text.rstrip().endswith((".", "!", "?")))


# LaTeX text, not an equation: a passage without maths delimiters with these is read as LaTeX
_LATEX_TEXT = re.compile(r"\\(?:section|subsection|subsubsection|paragraph|chapter|item|textbf|textit|emph|"
                         r"citep?|citet|ref|eqref|footnote|begin\s*\{(?:itemize|enumerate|description|abstract|"
                         r"quote)\})(?![a-zA-Z@])")


def passage_blocks(text: str) -> Tuple[list, bool]:
    """The window's input as pandoc blocks, and whether it was read as Markdown: a chat answer or notes
    with Markdown in them (a heading, **bold**, a list, a table, `code`, money) as Markdown, prose as a
    paragraph, a bare equation (no delimiters, no words) as one display equation, LaTeX text as LaTeX."""
    from .convert import input_equations
    from .pandoc_engine import (_COMMENT_LINE, _LATEX_DOCUMENT, _has_math_delimiters, _without_code,
                                looks_like_markdown, strip_tex)
    prose = _without_code(text)
    if not _LATEX_DOCUMENT.search(prose) and (looks_like_markdown(text) or (
            _PROSE_SIGNS.search(prose) and not _COMMENT_LINE.search(prose))):
        return markdown_blocks(text, MARKDOWN)[0], True
    if not _has_math_delimiters(text):
        if _LATEX_TEXT.search(text):
            return json.loads(_run(["-f", "latex", "-t", "json"], text.encode("utf-8"))[0])["blocks"], False
        if _prose(text) or not text.strip():
            return markdown_blocks(text, MARKDOWN)[0], True
        return [{"t": "Para", "c": [{"t": "Math", "c": [{"t": "DisplayMath"}, strip_tex(text)]}]}], False
    try:
        as_markdown = input_equations(text, "latex").as_markdown
    except ConversionError:  # no equation after all ($5 and $10): text
        as_markdown = True
    if as_markdown:
        return markdown_blocks(text, MARKDOWN)[0], True
    return json.loads(_run(["-f", "latex", "-t", "json"], text.encode("utf-8"))[0])["blocks"], False


def passage_to_word(text: str, path: str, macros: Optional[compat.Macros] = None) -> List[str]:
    """The window's input (`text`, LaTeX or a chat answer / Markdown) as a Word document at `path`."""
    from .convert import input_macros
    blocks, as_markdown = passage_blocks(text)
    return write_word(blocks, path, input_macros(macros, text, "latex", as_markdown), markdown=as_markdown)


def _local_class(source: str, text: str) -> str:
    """The text of the document's own class file, when it sits next to it (\\documentclass{siamart})."""
    from . import documents
    m = re.search(r"\\documentclass\s*(?:\[[^\]]*\])?\s*\{\s*([^{}\s]+)\s*\}", text)
    path = os.path.join(os.path.dirname(os.path.abspath(source)), m.group(1) + ".cls") if m else ""
    try:
        with open(path, "rb") as f:
            return documents._source_text(f.read())
    except OSError:
        return ""


def _cite_style(sources: str) -> str:
    """natbib without its numbers option prints author and year (as the ICLR and ICML styles load it)."""
    natbib = re.findall(r"\\(?:usepackage|RequirePackage)\s*(?:\[([^\]]*)\])?\s*\{[^{}]*\bnatbib\b[^{}]*\}", sources)
    if not natbib:
        return "numeric"
    numbers = any("numbers" in (options or "") for options in natbib) or re.search(
        r"\\(?:setcitestyle|citestyle)\s*\{[^{}]*\b(?:numbers|super)\b", sources)
    return "numeric" if numbers else "authoryear"


def document_to_word(source: str, path: str, macros: Optional[compat.Macros] = None) -> List[str]:
    """A whole .tex or .md file as a Word document at `path`; a .tex file with its own definitions, its
    equation numbers and the numbers of its .aux file."""
    from . import documents
    ext = os.path.splitext(source)[1].lower()
    if ext not in (".tex", ".md"):
        raise ConversionError("Only a .tex or .md file can be written as a whole Word document "
                              "(a .docx / .pptx: use Export all as .docx... for its equations).")
    folder = os.path.dirname(os.path.abspath(source))
    try:
        with open(source, "rb") as f:
            text = documents._source_text(f.read())  # (an old paper in Latin-1 too)
    except OSError as e:
        raise ConversionError("Cannot read %s: %s" % (source, e.strerror or e))
    notes: List[str] = []
    meta: dict = {}
    plans = None
    if ext == ".md":
        blocks, err = markdown_blocks(text, "markdown", folder, meta=meta)
        own = macros
        labels: Dict[str, str] = {}
    else:
        # (as pandoc reads it: with raw_tex, pandoc would no longer follow \input; a command it does not
        # know is left out, as pandoc's own conversions do)
        blocks, err, preamble = documents._read_latex_blocks(text, folder, notes, meta=meta)
        sources = documents._local_sources(source, text)
        definitions = compat.pandoc_leftovers(sources)
        if preamble is not None:
            definitions = documents._local_sources(source, preamble) + "\n" + definitions
        labels = documents._aux_labels(source)
        labels[compat.REF_STYLE] = "cleveref" if re.search(r"\bcleveref\b", sources) else "hyperref"
        # pandoc reads \autoref and \cref alike (ref+label): which one each reference is, in source order
        for m in re.finditer(r"\\(autoref|cref)\*?\s*\{([^{}]*)\}", sources):
            key = REF_COMMANDS + ",".join(k.strip() for k in m.group(2).split(","))
            labels[key] = (labels.get(key, "") + " " + m.group(1)).strip()
        labels[compat.CITE_STYLE] = _cite_style(sources)
        _split_grouped(blocks)
        graphics = re.search(r"\\graphicspath\s*\{((?:\s*\{[^{}]*\})+)\s*\}", sources)
        _find_figures(blocks, folder, re.findall(r"\{([^{}]*)\}", graphics.group(1)) if graphics else [])
        plans, counted, more = _count_equations(blocks, labels, sources + "\n" + _local_class(source, text))
        notes += more
        labels.update(counted)  # (the same as the .aux's when it has them; a \tag's as it prints)
        own = compat.with_document(macros, definitions, labels)
    notes += [" ".join(line.replace("[WARNING]", "").split()) for line in err.splitlines()
              if line.startswith("[WARNING]") and "Could not convert TeX math" not in line]
    return write_word(blocks, path, own, labels, notes, folder, markdown=ext == ".md", meta=meta, plans=plans)


_PICTURES = ("", ".png", ".jpg", ".jpeg", ".gif", ".bmp", ".tif", ".tiff", ".svg", ".emf", ".wmf")


def _find_figures(node, folder: str, dirs: List[str]) -> None:
    """Each figure's file, found as LaTeX finds it: next to the document or in a \\graphicspath folder, with
    the extension LaTeX adds (\\includegraphics{plot}); a picture Word can take before a .pdf or .eps of
    the same name.  In place."""
    if isinstance(node, list):
        for n in node:
            _find_figures(n, folder, dirs)
    elif isinstance(node, dict):
        if node.get("t") == "Image" and isinstance(node.get("c"), list) and len(node["c"]) == 3:
            target = node["c"][2][0]
            if re.match(r"[a-z]+://", target):
                return
            stem, ext = os.path.splitext(target)
            names = ([stem + e for e in _PICTURES[1:]] if ext.lower() in (".pdf", ".eps", ".ps") else []) + \
                [target + e for e in _PICTURES]
            for base in [""] + dirs:
                found = next((p for p in (os.path.join(folder, base, n) for n in names) if os.path.isfile(p)), None)
                if found:
                    node["c"][2][0] = found
                    return
            return
        _find_figures(node.get("c"), folder, dirs)


# ---- equation numbers ---------------------------------------------------------------------------------
def _without_comments(tex: str) -> str:
    """TeX without its % comments (a \\% stays; a \\\\ before a % is a row break, not an escape)."""
    out, i = [], 0
    while i < len(tex):
        ch = tex[i]
        if ch == "\\" and i + 1 < len(tex):
            out.append(tex[i:i + 2])
            i += 2
            continue
        if ch == "%":
            end = tex.find("\n", i)
            if end < 0:
                break
            i = end  # (the line break stays: what follows is on a line of its own)
            continue
        out.append(ch)
        i += 1
    return "".join(out)


_CONTROL = re.compile(r"\\(?:(begin|end)\s*\{[^{}]*\}|\\\*?(?:\s*\[[^\]]*\])?|[a-zA-Z@]+|.)", re.S)


def _top_rows(body: str) -> List[str]:
    """`body` split at its own \\\\ (not those of a cases, aligned or matrix in it, nor inside braces):
    [row, separator, row, ...]."""
    parts, depth, start, i = [], 0, 0, 0
    while i < len(body):
        ch = body[i]
        if ch == "\\":
            m = _CONTROL.match(body, i)
            if m.group(1) == "begin":
                depth += 1
            elif m.group(1) == "end":
                depth -= 1
            elif depth == 0 and m.group(0).startswith("\\\\"):
                parts += [body[start:i], m.group(0)]
                start = m.end()
            i = m.end()
            continue
        if ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
        i += 1
    parts.append(body[start:])
    return parts


def _tag(row: str) -> Optional[Tuple[int, int, bool, str]]:
    """The row's \\tag: (start, end, starred, argument), its argument with braces in it."""
    m = _TAG_START.search(row)
    if not m:
        return None
    depth, i = 1, m.end()
    while i < len(row) and depth:
        if row[i] == "\\":
            i += 2
            continue
        depth += {"{": 1, "}": -1}.get(row[i], 0)
        i += 1
    return (m.start(), i, bool(m.group(1)), row[m.end():i - 1]) if not depth else None


_TAG_TEXT: Dict[str, str] = {}


def _tag_text(argument: str, labels: Dict[str, str]) -> str:
    """What a \\tag's argument prints: text, $\\dagger$ as †, \\text{Max} as Max, \\ref as the number."""
    def ref(m: "re.Match") -> str:
        keys = [k.strip() for k in m.group(2).split(",")]
        return compat.reference_text(m.group(1), keys, labels) or ", ".join(keys)
    argument = re.sub(r"\\(eqref|ref)\s*\{([^{}]*)\}", ref, argument)
    if not re.search(r"[\\$^_{}]", argument):
        return " ".join(argument.split())
    if argument not in _TAG_TEXT:
        try:
            # (a tag's argument is text, with maths in $...$; \text and symbols read as maths)
            out = _run(["-f", "latex", "-t", "plain", "--wrap=none"], (argument if "$" in argument else
                                                                       "$%s$" % argument).encode("utf-8"))[0]
            out = out.decode("utf-8")
            _TAG_TEXT[argument] = " ".join(out.split()) or argument
        except ConversionError:
            _TAG_TEXT[argument] = argument
    return _TAG_TEXT[argument]


def _row_kinds(tex: str) -> Tuple[str, List[str], str, list]:
    """(head, rows, tail, kinds) of a display equation (comments out): its rows (rows[0::2]; one for an
    equation, multline or a bare display), and for each None, "count" (LaTeX numbers it) or
    ("tag", start, end, starred, argument)."""
    env = _ENV.fullmatch(tex)
    if env:
        head, name, star, body, tail = env.group(1), env.group(2), env.group(3), env.group(4), env.group(5)
    else:
        head, name, star, body, tail = "", "", "", tex, ""
    rows = _top_rows(body) if name in _PER_ROW else [body]
    kinds: list = []
    for row in rows[0::2]:
        tag = _tag(row)
        if tag:
            kinds.append(("tag",) + tag)
        elif name in _PER_ROW | _ONE_NUMBER and not star and not _NO_NUMBER.search(row):
            kinds.append("count")
        else:
            kinds.append(None)
    return head, rows, tail, kinds


def _display_maths(node, out: list) -> None:
    if isinstance(node, list):
        for n in node:
            _display_maths(n, out)
    elif isinstance(node, dict):
        if node.get("t") == "Math":
            if node["c"][0]["t"] == "DisplayMath":
                out.append(node)
            return
        _display_maths(node.get("c"), out)


_TOP_ENV = re.compile(r"\\begin\s*\{([a-zA-Z]+\*?)\}")


def _environments(tex: str) -> Optional[List[str]]:
    """The environments a display holds one after the other (pandoc gives a subequations group of
    two equation environments as one display; and \\label{group} before them), or None."""
    parts, i = [], 0
    while True:
        rest = re.match(r"(?:\s|\\label\s*\{[^{}]*\})*", tex[i:])
        i += rest.end()
        if i >= len(tex):
            return parts
        m = _TOP_ENV.match(tex, i)
        if not m:
            return None
        name, depth, j = m.group(1), 0, i
        pattern = re.compile(r"\\(begin|end)\s*\{%s\}" % re.escape(name))
        for e in pattern.finditer(tex, i):
            depth += 1 if e.group(1) == "begin" else -1
            if not depth:
                j = e.end()
                break
        if depth:
            return None
        parts.append(tex[i:j])
        i = j


def _split_grouped(node) -> None:
    """A display of several environments in a row (subequations) as one display each, in place."""
    if isinstance(node, list):
        k = 0
        while k < len(node):
            n = node[k]
            if isinstance(n, dict) and n.get("t") == "Math" and n["c"][0]["t"] == "DisplayMath":
                parts = _environments(n["c"][1])
                if parts and (len(parts) > 1 or parts[0] != n["c"][1]):
                    node[k:k + 1] = [{"t": "Math", "c": [{"t": "DisplayMath"}, p]} for p in parts]
                    k += len(parts)
                    continue
            else:
                _split_grouped(n)
            k += 1
    elif isinstance(node, dict):
        _split_grouped(node.get("c"))


def _count_equations(blocks: list, aux: Dict[str, str], sources: str
                     ) -> Tuple[Optional[Dict[int, List[Optional[str]]]], Dict[str, str], List[str]]:
    """The numbers LaTeX gives the display equations of a document read by pandoc: (for each display's
    id, its rows' numbers; the labels so numbered; notes), counted (1), (2)... as LaTeX does, unless the
    document numbers equations otherwise (then None, and the .aux numbers of labelled equations are used)
    or its .aux file says other numbers.  The labels of \\tag rows refer to the tag as it prints, always."""
    plans: Dict[int, List[Optional[str]]] = {}
    counted: Dict[str, str] = {}
    tagged: Dict[str, str] = {}
    counter = 0
    displays: list = []
    _display_maths(blocks, displays)
    for node in displays:
        head, rows, tail, kinds = _row_kinds(_without_comments(node["c"][1]))
        numbers: List[Optional[str]] = []
        for k, kind in enumerate(kinds):
            row = rows[2 * k]
            if kind == "count":
                counter += 1
                numbers.append(str(counter))
                for m in _LABEL.finditer(row):
                    counted.setdefault(m.group(1), str(counter))
            else:
                numbers.append(None)
                if kind:  # a \tag's label refers to the tag, as it prints (the .aux has its TeX: $\dagger $)
                    for m in _LABEL.finditer(row):
                        tagged.setdefault(m.group(1), _tag_text(kind[4], aux))
        if any(numbers):
            plans[id(node)] = numbers
    if not counter:
        return None, tagged, []
    have_aux = any(k in aux for k in counted)
    if _numbering_changed(sources) or _CHAPTER_CLASS.search(sources):
        unnumbered = sum(1 for numbers in plans.values() for n in numbers if n) - sum(
            1 for k in counted if k in aux)
        if not have_aux:
            return None, tagged, ["No equation numbers: the document numbers its equations by chapter or "
                                  "section (or another counter shares theirs, or it sets the counter itself), "
                                  "which only LaTeX's .aux file next to it tells (compile it once)."]
        return None, tagged, ["Equations without a \\label have no number (%d): the document numbers equations "
                              "by chapter or section, or another way the .aux file tells only for labelled ones."
                              % unnumbered] if unnumbered > 0 else []
    differ = [k for k, v in counted.items() if k in aux and aux[k] != v]
    if differ:
        return None, tagged, ["Only labelled equations have numbers: the .aux file's numbers are not (1), (2)... "
                              "in order (%s is %s there), so the others are not known (an .aux file older than "
                              "the document, or numbering the document sets itself)." % (differ[0], aux[differ[0]])]
    notes = [] if have_aux else ["Equations numbered (1), (2)... in order, as LaTeX numbers them in this document "
                                 "(not checked: no .aux file next to it holds their labels)."]
    return plans, dict(counted, **tagged), notes


_COUNTER_CHANGE = re.compile(
    r"\\(?:numberwithin|counterwithin\*?)\s*\{equation\}|\\@addtoreset\s*\{equation\}"
    r"|\\(?:re|provide)?newcommand\*?\s*\{?\s*\\theequation(?![a-zA-Z@])|\\def\s*\\theequation(?![a-zA-Z@])"
    r"|\\(?:setcounter|addtocounter|stepcounter|refstepcounter)\s*\{equation\}|\\begin\s*\{subequations\}"
    r"|\\chapter(?![a-zA-Z@])|\\newtheorem\s*\{[^{}]*\}\s*\[\s*equation\s*\]|\\newaliascnt\s*\{[^{}]*\}\s*\{equation\}")
_DEFINITION = re.compile(r"\\(?:re|provide)?newcommand\*?\s*\{?\s*\\([a-zA-Z@]+)\s*\}?\s*(?:\[\d\])?\s*(?:\[[^\]]*\])?\s*\{"
                         r"|\\[gex]?def\s*\\([a-zA-Z@]+)[^{\n]*\{")


def _numbering_changed(sources: str) -> bool:
    """Whether the document numbers equations otherwise than (1), (2)... in order: by chapter or section,
    a theorem counter sharing theirs, subequations, or the counter set or stepped; a macro that steps it
    (\\numberthis) only if the document uses it."""
    stepping, spans = [], []
    for m in _DEFINITION.finditer(sources):
        depth, i = 1, m.end()
        while i < len(sources) and depth:
            if sources[i] == "\\":
                i += 2
                continue
            depth += {"{": 1, "}": -1}.get(sources[i], 0)
            i += 1
        body = sources[m.end():i - 1]
        name = m.group(1) or m.group(2)
        if name != "theequation" and _COUNTER_CHANGE.search(body):
            stepping.append(name)
            spans.append((m.start(), i))
    rest = sources
    for start, end in reversed(spans):
        rest = rest[:start] + rest[end:]
    return bool(_COUNTER_CHANGE.search(rest)) or any(
        re.search(r"\\%s(?![a-zA-Z@])" % re.escape(name), rest) for name in stepping)


# ---- preparing the blocks -----------------------------------------------------------------------------
class _Prepare:
    """Walks pandoc JSON: raw TeX (in Markdown) read as LaTeX, each Math prepared for texmath with its
    numbers marked by sentinels, references and citations replaced by what LaTeX prints."""

    def __init__(self, macros: Optional[compat.Macros], labels: Dict[str, str], folder: Optional[str],
                 markdown: bool = False, plans: Optional[Dict[int, List[Optional[str]]]] = None):
        self.macros, self.labels, self.folder = macros, labels, folder
        self.base_macros = macros
        self.ref_commands = {k[len(REF_COMMANDS):]: v.split() for k, v in labels.items() if k.startswith(REF_COMMANDS)}
        # raw TeX in Markdown is read by the LaTeX reader; in a LaTeX document it is what that reader could
        # not read (\gls{..}, a package's command): kept as code, not dropped as pandoc's docx writer would
        self.markdown = markdown
        self.plans = plans or {}
        self.raw_kept = 0
        self.raw_dropped: Dict[str, int] = {}
        self.numbers: Dict[int, str] = {}       # sentinel id -> "(2.1)"
        self.maths: List[Tuple[dict, str, str]] = []  # (Math node, original TeX, prepared TeX)
        self.notes: List[str] = []
        self.definitions: List[str] = []        # raw TeX definitions so far (Markdown)
        self.read: Dict[str, bytes] = {}
        self.unresolved = 0

    def blocks(self, blocks: list) -> list:
        return self.node(blocks)

    def dropped(self, raw: str) -> None:
        """A LaTeX block pandoc does not know (tikzpicture, algorithm): left out, and said so."""
        env = re.match(r"\s*\\begin\s*\{([^{}]*)\}", raw)
        name = env.group(1) if env else (re.match(r"\s*(\\[A-Za-z@]+)", raw) or [None, "raw TeX"])[1]
        self.raw_dropped[name] = self.raw_dropped.get(name, 0) + 1

    def raw_block(self, raw: str) -> list:
        """Raw TeX written into Markdown (\\begin{align} or \\begin{aligned} without $$, a \\newcommand,
        an \\input): pandoc's docx writer would drop it; read by the LaTeX reader after the definitions
        before it (what those give themselves left out)."""
        from .pandoc_engine import _MACRO_DEF
        try:
            read = self.after_definitions(raw)
        except ConversionError:
            read = [{"t": "CodeBlock", "c": [["", [], []], raw]}]
        if re.search(r"\\(?:input|include)(?![a-zA-Z@])", raw) or _MACRO_DEF.search(raw) or \
                re.search(r"\\(?:DeclareMathOperator|let)(?![a-zA-Z@])", raw):
            self.definitions.append(raw)
            # for the Markdown maths after it too ($\R$), which pandoc expands only from definitions written
            # into the Markdown itself, not from an \input file's
            from . import documents
            sources = documents._local_sources(os.path.join(self.folder or os.getcwd(), "notes.md"),
                                               "\n".join(self.definitions))
            self.macros = compat.with_document(self.base_macros, sources, self.labels)
        return self.as_latex(read) if read and isinstance(read[0], dict) and read[0].get("t") != "CodeBlock" \
            else read

    def after_definitions(self, raw: str) -> list:
        """The blocks of raw TeX read after the definitions so far, without those the definitions give
        themselves (an equation of a raw block that also defines a macro); a blank line between them,
        so that the two never share a paragraph."""
        from .pandoc_engine import _INNER_MATH_ENV
        source = "\\[" + raw + "\\]" if _INNER_MATH_ENV.match(raw) else raw
        if not self.definitions:
            return self.read_latex(source)
        definitions = "\n\n".join(self.definitions)
        return self.read_latex(definitions + "\n\n" + source)[len(self.read_latex(definitions)):]

    def read_latex(self, source: str) -> list:
        """pandoc's LaTeX reading of raw TeX (remembered: the definitions alone are read once)."""
        if source not in self.read:
            self.read[source] = _run(["-f", "latex", "-t", "json"], source.encode("utf-8"), cwd=self.folder)[0]
        return json.loads(self.read[source])["blocks"]

    def as_latex(self, nodes):
        """Nodes the LaTeX reader gave (no second reading of the raw TeX it left)."""
        markdown, self.markdown = self.markdown, False
        try:
            return self.node(nodes)
        finally:
            self.markdown = markdown

    def node(self, node):
        if isinstance(node, list):
            out = []
            for n in node:
                if isinstance(n, dict) and n.get("t") == "RawBlock" and n["c"][0] in _RAW_TEX:
                    if self.markdown:
                        out += self.raw_block(n["c"][1])
                    else:
                        self.dropped(n["c"][1])
                else:
                    out.append(self.node(n))
            return out
        if not isinstance(node, dict):
            return node
        t = node.get("t")
        if t is None:  # the metadata (title, abstract: its citations and maths as the text's), a MetaMap's
            return {k: self.node(v) for k, v in node.items()}
        if t == "Math":
            self.math(node)
            return node
        if t == "Link":
            attrs = dict(node["c"][0][2])
            kind, key = attrs.get("reference-type"), attrs.get("reference", "")
            if kind in _REFERENCE_TYPES:
                keys = [k.strip() for k in key.split(",") if k.strip()]
                if kind == "ref+label":  # \autoref or \cref: the next of those the source has for these keys
                    queue = self.ref_commands.get(",".join(keys))
                    if queue:
                        kind = queue.pop(0)
                shown = compat.reference_text(kind, keys, self.labels)
                if shown is not None:
                    return {"t": "Str", "c": shown}
                if _inline_text(node["c"][1]).strip() == "[%s]" % key:  # pandoc's placeholder: no number
                    self.unresolved += 1
        if t == "Cite":
            return self.cite(node)
        if t == "RawInline" and node["c"][0] in _RAW_TEX:
            return self.raw_inline(node)
        if "c" in node:
            node = dict(node)
            node["c"] = self.node(node["c"])
        return node

    def cite(self, node: dict):
        """What LaTeX prints for a citation (pandoc's docx writer drops the raw \\cite in it): its number or
        author and year from the .aux, else [key]; in Markdown, a key no .aux knows stays as written."""
        raws = [n["c"][1] for n in node["c"][1] if isinstance(n, dict) and n.get("t") == "RawInline"]
        command = re.match(r"\s*\\([A-Za-z]+)", raws[0]) if raws else None  # \citep, \citet, \citeauthor...
        cites = [(c["citationId"], _inline_text(c.get("citationPrefix") or []).strip(),
                  _inline_text(c.get("citationSuffix") or []).strip().lstrip(", ")) for c in node["c"][0]]
        shown, found = compat.citation_text(command.group(1) if command else "cite", cites, self.labels)
        if self.markdown and not found:
            return {"t": "Span", "c": [["", [], []], self.node(node["c"][1])]}
        return {"t": "Str", "c": shown}

    def raw_inline(self, node: dict):
        code = {"t": "Code", "c": [["", [], []], node["c"][1]]}
        if not self.markdown:
            self.raw_kept += 1
            return code
        try:
            read = self.after_definitions(node["c"][1])
        except ConversionError:
            return code
        if len(read) == 1 and read[0].get("t") in ("Para", "Plain"):
            return {"t": "Span", "c": [["", [], []], self.as_latex(read[0]["c"])]}
        return code

    def math(self, node: dict) -> None:
        kind, tex = node["c"]
        marked = tex
        if kind["t"] == "DisplayMath":
            marked = self.mark_numbers(tex, self.plans.get(id(node)))
        try:
            prepared, notes = compat.for_texmath(marked, self.macros, "omml")
            self.notes += [n for n in notes if n not in self.notes]
        except ConversionError:
            prepared = tex
        node["c"] = [kind, prepared]
        self.maths.append((node, tex, prepared))

    def mark_numbers(self, tex: str, plan: Optional[List[Optional[str]]] = None) -> str:
        """Each numbered row of a display with a sentinel at its end: its \\tag, its number counted as LaTeX
        counts (`plan`), or the .aux number of its \\label."""
        def sentinel(shown: str) -> str:
            n = len(self.numbers) + 1
            self.numbers[n] = shown
            return "\\text{%s}" % (_SENTINEL % n)

        tex = _without_comments(tex)
        head, rows, tail, kinds = _row_kinds(tex)
        for k, kind in enumerate(kinds):
            i = 2 * k
            row = rows[i]
            if kind and kind != "count":
                _, start, end, starred, argument = kind
                shown = _tag_text(argument, self.labels)
                rows[i] = _LABEL.sub("", row[:start] + row[end:]) + sentinel(shown if starred else "(%s)" % shown)
            elif kind == "count":
                label = next((m for m in _LABEL.finditer(row) if m.group(1) in self.labels), None)
                shown = plan[k] if plan and k < len(plan) and plan[k] else (
                    self.labels[label.group(1)] if label else None)
                if shown is not None:
                    rows[i] = _LABEL.sub("", row) + sentinel("(%s)" % shown)
        return head + "".join(rows) + tail


# ---- writing --------------------------------------------------------------------------------------------
def write_word(blocks: list, path: str, macros: Optional[compat.Macros] = None,
               labels: Optional[Dict[str, str]] = None, notes: Optional[List[str]] = None,
               folder: Optional[str] = None, markdown: bool = False, meta: Optional[dict] = None,
               plans: Optional[Dict[int, List[Optional[str]]]] = None) -> List[str]:
    """`blocks` (pandoc JSON; read from Markdown when `markdown`; `meta`: its title, authors, abstract) as a
    Word document at `path`, written next to it first and then put in its place, with figures found from
    `folder`.  Returns notes (what was approximated, what could not be converted, numbers)."""
    from . import diagnose, documents
    prep = _Prepare(macros, labels or {}, folder, markdown, plans)
    meta = prep.node(meta or {})
    blocks = prep.blocks(blocks)
    notes = list(notes or []) + prep.notes

    def source() -> bytes:
        return json.dumps({"pandoc-api-version": _api(), "meta": meta, "blocks": blocks}).encode("utf-8")

    target = os.path.abspath(path)
    try:
        handle, part = tempfile.mkstemp(dir=os.path.dirname(target), prefix="." + os.path.basename(target) + ".",
                                        suffix=".part")
        os.close(handle)
    except OSError as e:
        raise ConversionError("Could not write %s: %s" % (path, e.strerror or e))
    failed: Dict[int, str] = {}
    try:
        _, err = _run(["-f", "json", "-t", "docx", "-o", part], source(), cwd=folder)
        failed = documents._unconverted(err, [p for _, _, p in prep.maths])
        if failed:  # again, each such equation as its LaTeX (without sentinels) and the reason after it
            for count, i in enumerate(sorted(failed), 1):
                node, original, prepared = prep.maths[i - 1]
                words = diagnose.explain(original, prepared, failed[i], short=True) if count <= 50 else []
                node["c"] = [node["c"][0], original]
                node["reason"] = words[0] if words else failed[i]
            meta = _with_reasons(meta)
            blocks = _with_reasons(blocks)
            _, err = _run(["-f", "json", "-t", "docx", "-o", part], source(), cwd=folder)
        missing = sorted({m.group(1) for m in re.finditer(r"Could not fetch resource '?([^'\n]+?)'?(?::|\n|$)", err)})
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
    if missing:
        notes.append("Not found, so left out: %s." % ", ".join(missing[:5] + (["..."] if len(missing) > 5 else [])))
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
        elif "t" not in clean:  # the meta mapping
            clean = {k: _with_reasons(v) for k, v in clean.items()}
        return clean
    return node


# ---- numbers in the written file -------------------------------------------------------------------------
_TAG_TOKEN = re.compile(r"<(/?)m:(\w+)\b[^>]*?(/?)>")
_MAX_DIST = '<m:maxDist m:val="1"/>'
_ALIGN = "<m:r><m:t>&amp;</m:t></m:r>"
_PARTS = ("word/document.xml", "word/footnotes.xml", "word/endnotes.xml")


def _number_equations(docx: str, numbers: Dict[int, str]) -> int:
    """Replaces the sentinels in the written file (its text, footnotes and endnotes) by Word's own
    equation numbers; how many there are."""
    if not numbers:
        return 0
    with zipfile.ZipFile(docx) as z:
        files = {info: z.read(info.filename) for info in z.infolist()}
    names = {info.filename: info for info in files}
    count = [0]

    def omath(m: "re.Match") -> str:
        body = m.group(1)
        if "QQMSN" not in body:
            return m.group(0)
        return "<m:oMath>%s</m:oMath>" % _numbered(body, numbers, count)

    for part in _PARTS:
        if part in names:
            xml = files[names[part]].decode("utf-8")
            if "QQMSN" in xml:
                files[names[part]] = re.sub(r"<m:oMath>([\s\S]*?)</m:oMath>", omath, xml).encode("utf-8")
    with zipfile.ZipFile(docx, "w", zipfile.ZIP_DEFLATED) as z:
        for info, data in files.items():
            z.writestr(info, data)
    return count[0]


def _number_run(shown: str) -> str:
    """Word's number mark: # and the number, upright as Word sets its own numbers ((Q), not (𝑄))."""
    return ('<m:r><m:rPr><m:sty m:val="p"/></m:rPr><m:t>#%s</m:t></m:r>'
            % shown.replace("&", "&amp;").replace("<", "&lt;"))


def _as_array(matrix: str) -> Optional[str]:
    """texmath's matrix for gather, eqnarray, flalign (and an align without &) as the equation array Word
    numbers rows of: a row's cells joined at alignment points (eqnarray's at its middle column), a gather
    row centred."""
    first_row = matrix.find("<m:mr>")
    jcs = re.findall(r'<m:mcJc m:val="(\w+)"\s*/>', matrix[:first_row] if first_row > 0 else "")
    rows: List[List[str]] = []
    depth, start = 0, None
    for m in _TAG_TOKEN.finditer(matrix):  # its own rows (depth 2) and cells (depth 3), not a nested matrix's
        if m.group(3):
            continue
        if not m.group(1):
            depth += 1
            if depth == 2 and m.group(2) == "mr":
                rows.append([])
            elif depth == 3 and m.group(2) == "e" and rows:
                start = m.end()
        else:
            if depth == 3 and m.group(2) == "e" and start is not None:
                rows[-1].append(matrix[start:m.start()])
                start = None
            depth -= 1
    out = []
    for cells in rows:
        if jcs == ["right", "center", "left"] and len(cells) == 3:
            out.append(cells[0] + _ALIGN + cells[1] + cells[2])
        else:
            out.append(_ALIGN.join(cells))
    return "<m:eqArr>%s</m:eqArr>" % "".join("<m:e>%s</m:e>" % r for r in out) if len(out) > 1 else None


def _numbered(body: str, numbers: Dict[int, str], count: List[int]) -> str:
    """An equation's OMML (inside m:oMath) with its sentinels made Word numbers: in an equation array at
    the end of their rows, else around the whole equation as a one-row array."""
    stripped = body.strip()
    if stripped.startswith("<m:m>") and _top_level_end(stripped, 0) == len(stripped):
        stripped = _as_array(stripped) or stripped  # rows numbered one by one (or the last, multline)
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
