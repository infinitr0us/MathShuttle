"""Conversion engine: drives pandoc (which embeds texmath) through its JSON AST.

Every equation is normalised to pandoc's own representation, a ``Math`` inline
holding TeX plus an inline/display flag.  Reading a format means asking pandoc
to parse a tiny document and collecting the ``Math`` nodes; writing means
feeding a one-paragraph JSON document to the relevant pandoc writer and cutting
the equation back out of its output.

Supported formats:  read  latex | mathml | omml
                    write mathml | omml | eqn | typst   (LaTeX output is handled
                    in :mod:`latex_wrap` because the TeX is already in hand).
"""
from __future__ import annotations

import html.entities
import io
import json
import os
import re
import subprocess
import sys
import zipfile
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from typing import List, Optional

MATHML_NS = "http://www.w3.org/1998/Math/MathML"
OMML_NS = "http://schemas.openxmlformats.org/officeDocument/2006/math"
WML_NS = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"
MC_NS = "http://schemas.openxmlformats.org/markup-compatibility/2006"

ET.register_namespace("", MATHML_NS)
ET.register_namespace("m", OMML_NS)
ET.register_namespace("w", WML_NS)

READ_FORMATS = ("latex", "mathml", "omml")
WRITE_FORMATS = ("latex", "mathml", "omml", "eqn", "typst")

_CREATE_NO_WINDOW = 0x08000000 if sys.platform == "win32" else 0


class PandocNotFound(RuntimeError):
    pass


class ConversionError(ValueError):
    """The input could not be converted; the message is meant for the user."""


@dataclass
class Equation:
    tex: str
    display: bool = True


# --- locating / running pandoc ------------------------------------------------------
_pandoc_path: Optional[str] = None
_pandoc_version: Optional[str] = None
_texmath_version: Optional[str] = None
_api_version: Optional[list] = None
_mathml_flag: Optional[str] = None


def _candidate_paths():
    env = os.environ.get("EQUATION_TOOL_PANDOC")
    if env:
        yield env
    # next to the frozen executable / this package (lets a copy of pandoc.exe be bundled)
    base = os.path.dirname(sys.executable) if getattr(sys, "frozen", False) else os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    yield os.path.join(base, "pandoc.exe")
    yield os.path.join(base, "pandoc", "pandoc.exe")
    # PATH, but not the current directory: on Windows shutil.which() looks there first, which
    # would run a pandoc.exe lying in whatever folder the CLI is started from.
    exe = "pandoc.exe" if sys.platform == "win32" else "pandoc"
    for d in os.environ.get("PATH", "").split(os.pathsep):
        d = d.strip().strip('"')
        if os.path.isabs(d):
            yield os.path.join(d, exe)
    for root in (os.environ.get("LOCALAPPDATA"), os.environ.get("ProgramFiles"), os.environ.get("ProgramFiles(x86)")):
        if root:
            yield os.path.join(root, "Pandoc", "pandoc.exe")


def find_pandoc() -> str:
    global _pandoc_path
    if _pandoc_path is None:
        env = os.environ.get("EQUATION_TOOL_PANDOC")
        if env and not os.path.isfile(env):  # a typo must not silently select some other pandoc
            raise PandocNotFound("EQUATION_TOOL_PANDOC is set to %r, which is not an existing file." % env)
        for p in _candidate_paths():
            if p and os.path.isfile(p):
                _pandoc_path = p
                break
        else:
            raise PandocNotFound(
                "pandoc.exe was not found. Install it with  winget install JohnMacFarlane.Pandoc\n"
                "(or set EQUATION_TOOL_PANDOC to the full path of pandoc.exe).")
    return _pandoc_path


def _shim_target(path: str) -> Optional[str]:
    """The executable behind a Scoop shim: ``<name>.exe`` is a generic launcher and the
    ``<name>.shim`` file next to it holds ``path = "<real exe>"`` (unquoted in older Scoop)."""
    try:
        with open(os.path.splitext(path)[0] + ".shim", encoding="utf-8-sig") as f:
            for line in f:
                key, sep, value = line.partition("=")
                if sep and key.strip() == "path":
                    target = value.strip().strip('"')
                    return target if os.path.isfile(target) else None
    except OSError:
        pass
    return None


def pandoc_binary() -> str:
    """The pandoc executable itself; `find_pandoc()` (what gets *run*) may be a launcher."""
    path = find_pandoc()
    return _shim_target(path) or path


def pandoc_version() -> str:
    global _pandoc_version
    if _pandoc_version is None:
        out, _ = _run(["--version"], b"")
        _pandoc_version = out.decode("utf-8", "replace").splitlines()[0]
    return _pandoc_version


def _scan_texmath_version(path: str) -> Optional[str]:
    """The ``texmath-<version>`` Haskell package id embedded in a (statically linked) pandoc."""
    import mmap
    try:
        with open(path, "rb") as f, mmap.mmap(f.fileno(), 0, access=mmap.ACCESS_READ) as mm:
            m = re.search(rb"texmath-(\d+(?:\.\d+)+)", mm)
            return m.group(1).decode() if m else None  # the match reads the mmap: decode before it closes
    except (OSError, ValueError):  # unreadable or empty file
        return None


def texmath_version() -> Optional[str]:
    """Version of the texmath library linked into pandoc (pandoc --version does not report it).

    pandoc is statically linked, so the package id is embedded in the executable; a newer
    texmath only arrives with a newer pandoc.  None when the executable carries no package
    id (a launcher we do not know how to follow, or an unusual build).
    """
    global _texmath_version
    if _texmath_version is None:
        _texmath_version = _scan_texmath_version(pandoc_binary()) or ""
    return _texmath_version or None


def engine_description() -> str:
    """One line for the status bar: ``pandoc 3.11  (texmath 0.13.2.2)``."""
    return "%s  (texmath %s)" % (pandoc_version(), texmath_version() or "version unknown")


def engine_report() -> str:
    """`engine_description()` plus the executable in use (and the binary behind a shim)."""
    command, binary = find_pandoc(), pandoc_binary()
    lines = [engine_description(), command if binary == command else "%s  ->  %s" % (command, binary)]
    if not texmath_version():
        lines.append("texmath version unknown: %s carries no texmath package id (a launcher?). "
                     "Set EQUATION_TOOL_PANDOC to the real pandoc.exe to inspect it." % os.path.basename(binary))
    return "\n".join(lines)


def _run(args: List[str], stdin: bytes, timeout: float = 60) -> "tuple[bytes, str]":
    try:
        proc = subprocess.run([find_pandoc(), *args], input=stdin, capture_output=True,
                              timeout=timeout, creationflags=_CREATE_NO_WINDOW)
    except subprocess.TimeoutExpired:
        raise ConversionError("pandoc did not finish within %d s" % timeout)
    except OSError as e:  # e.g. pandoc.exe removed or replaced (winget upgrade) while the app runs
        raise ConversionError("Could not run %s: %s" % (find_pandoc(), e))
    err = proc.stderr.decode("utf-8", "replace").replace("\r\n", "\n")
    if proc.returncode != 0:
        raise ConversionError("pandoc failed (exit %d):\n%s" % (proc.returncode, err.strip()))
    return proc.stdout, err


def _api():
    global _api_version
    if _api_version is None:
        out, _ = _run(["-f", "markdown", "-t", "json"], b"")
        _api_version = json.loads(out)["pandoc-api-version"]
    return _api_version


def _mathml_flags() -> List[str]:
    global _mathml_flag
    if _mathml_flag is None:
        try:
            _run(["-f", "markdown", "-t", "html", "--math-method=mathml"], b"")
            _mathml_flag = "--math-method=mathml"
        except ConversionError:
            _mathml_flag = "--mathml"  # pandoc < 3.7 (deprecated spelling)
    return [_mathml_flag]


# The TeX in the message keeps its line breaks (continuation lines indented by two spaces),
# so the part before ", rendering as TeX:" may span several lines.
_TEXMATH_WARNING = re.compile(r"\[WARNING\] Could not convert TeX math [\s\S]*?, rendering as TeX:\n((?:  .*\n?)*)")

# for writers with text output: pandoc writes CRLF on Windows by default
_LF = "--eol=lf"


def _check_texmath_warning(stderr: str) -> None:
    """Raise if texmath rejected the TeX (pandoc then writes the TeX source as text instead)."""
    m = _TEXMATH_WARNING.search(stderr)
    if m:
        detail = "\n".join(line[2:] for line in m.group(1).rstrip("\n").splitlines())
        raise ConversionError("texmath could not parse this LaTeX:\n" + detail)
    if "Could not convert TeX math" in stderr:  # message layout changed in some pandoc release
        raise ConversionError("texmath could not parse this LaTeX:\n" + stderr.strip())


# --- reading --------------------------------------------------------------------------
def _collect_math(node, out: List[Equation]) -> None:
    if isinstance(node, dict):
        if node.get("t") == "Math":
            kind, tex = node["c"]
            out.append(Equation(tex.strip(), kind["t"] == "DisplayMath"))
            return
        for v in node.values():
            _collect_math(v, out)
    elif isinstance(node, list):
        for v in node:
            _collect_math(v, out)


def _read_json(fmt: str, source: bytes) -> List[Equation]:
    out, _ = _run(["-f", fmt, "-t", "json"], source)
    eqs: List[Equation] = []
    _collect_math(json.loads(out)["blocks"], eqs)
    return eqs


# environments that pandoc's LaTeX reader turns into maths (checked against pandoc 3.11)
_PANDOC_MATH_ENVS = frozenset(
    "equation equation* displaymath math align align* alignat alignat* flalign flalign* eqnarray eqnarray* "
    "gather gather* multline multline* dmath dmath* dgroup dgroup* darray darray* subequations".split())
# spaces and comments, which LaTeX skips between a command and its argument
_TEX_GAP = r"(?:\s|%[^\n]*\n)*"
_LATEX_TOKEN = re.compile(r"\\begin" + _TEX_GAP + r"\{([^{}]*)\}|\\[a-zA-Z@]+|\\.|%[^\n]*|[{}$]", re.S)
# commands whose argument is text even inside maths, so that it may hold nested $...$
_TEXT_IN_MATH = frozenset(
    "\\text \\mbox \\hbox \\makebox \\fbox \\framebox \\raisebox \\parbox \\textrm \\textit \\textbf \\textsf "
    "\\texttt \\textnormal \\textup \\textsl \\textsc \\textmd \\textcolor \\colorbox \\fcolorbox".split())
# what may stand between a command and its next argument: spaces, [options], TeX's "to 3cm"
_ARG_GAP = re.compile(r"\s*(?:\[[^\[\]{}]*\]\s*)*(?:(?:to|spread)\b[^{}\\$%\[\]]*)?")


_MACRO_DEF = re.compile(r"\\(?:(?:re|provide)?newcommand|DeclareRobustCommand)\*?" + _TEX_GAP +
                        r"(?:\{\s*(\\[a-zA-Z@]+)\s*\}|(\\[a-zA-Z@]+))|\\[gex]?def\s*(\\[a-zA-Z@]+)")


def _text_macros(text: str) -> frozenset:
    """`_TEXT_IN_MATH` plus the macros that `text` defines with a body using one of them
    (``\\newcommand{\\note}[1]{\\mbox{#1}}``)."""
    bodies = {}
    for m in _MACRO_DEF.finditer(text):
        start = text.find("{", m.end())  # after [n] [default] or \def's parameter text
        if start < 0:
            continue
        depth, i = 0, start
        for i in range(start, len(text)):
            depth += {"{": 1, "}": -1}.get(text[i], 0) if text[i - 1:i] != "\\" else 0
            if not depth:
                break
        bodies[m.group(1) or m.group(2) or m.group(3)] = set(re.findall(r"\\[a-zA-Z@]+", text[start:i + 1]))
    commands = set(_TEXT_IN_MATH)
    while True:  # a macro built from another text macro is one too
        more = {name for name, used in bodies.items() if name not in commands and used & commands}
        if not more:
            return frozenset(commands)
        commands |= more


def _has_math_delimiters(text: str) -> bool:
    """True if `text` has $, \\(, \\[ or a maths environment outside every text-command argument.

    In math-mode TeX those can only occur inside the argument of \\text, \\mbox, ... (text in
    the formula), while prose with equations always has one elsewhere - at the top level, or
    in a group such as ``\\emph{for $n$}`` / ``{\\bf ... $x$ ...}``.  So input without them is a
    single bare equation, even when pandoc would find maths nested in it (``\\mbox{for all $n$}``).
    """
    text_commands = _text_macros(text)
    owners: List[Optional[str]] = []  # per open brace group: the command it is an argument of
    in_text = 0  # open groups that are text-command arguments
    owner, end = None, 0  # command whose argument may start next, end of the previous token
    for m in _LATEX_TOKEN.finditer(text):
        tok = m.group()
        if owner is not None and not _ARG_GAP.fullmatch(text, end, m.start()):
            owner = None  # something else came between the command and this token
        end = m.end()
        if tok == "{":
            owners.append(owner)
            in_text += owner in text_commands
            owner = None
        elif tok == "}":
            if owners:
                owner = owners.pop()  # \raisebox{1pt}{$x$}: the next group is the same command's
                in_text -= owner in text_commands
        elif tok.startswith("%"):
            pass
        elif not in_text and (tok in ("$", "\\(", "\\[") or m.group(1) in _PANDOC_MATH_ENVS):
            return True
        else:
            owner = tok if tok.startswith("\\") and tok[1:2].isalpha() else None
    return False


def read_latex(text: str, default_display: bool = True) -> List[Equation]:
    """Extract every equation from LaTeX source.

    Delimited maths ($...$, \\(...\\), $$...$$, \\[...\\], equation/align/gather/
    multline/eqnarray/... environments, several of them, with prose in between)
    are recognised by pandoc's LaTeX reader; \\newcommand definitions in the
    input are expanded.  Input with no delimiters at all is taken as one bare
    equation in the `default_display` mode (texmath handles its macros itself).
    """
    if not text.strip():
        raise ConversionError("The input is empty.")
    if _has_math_delimiters(text):
        eqs = _read_json("latex", text.encode("utf-8"))
        if eqs:
            return eqs
    return [Equation(text.strip(), default_display)]


_XML_ENTITIES = ("lt", "gt", "amp", "quot", "apos")
_NAMED_ENTITY = re.compile(r"&([A-Za-z][A-Za-z0-9]*);")
# CDATA sections and comments: literal text that the markup clean-ups below must not touch
_XML_LITERAL = re.compile(r"(<!\[CDATA\[[\s\S]*?\]\]>|<!--[\s\S]*?-->)")
# group 1: a literal (kept); otherwise an XML declaration or a DOCTYPE, whose internal subset
# [...] may hold comments and quoted strings with any characters
_XML_PROLOG = re.compile(_XML_LITERAL.pattern + r"|<\?xml\s[^>]*\?>"
                         r"|<!DOCTYPE[^\[>]*(?:\[(?:<!--[\s\S]*?-->|\"[^\"]*\"|'[^']*'|[^\]\"'])*\]\s*)?>", re.I)


def _outside_literals(text: str, fix) -> str:
    """Apply `fix` to the markup of `text`, leaving CDATA sections and comments alone."""
    parts = _XML_LITERAL.split(text)
    return "".join(part if i % 2 else fix(part) for i, part in enumerate(parts))


def _strip_xml_prolog(text: str) -> str:
    """Drop a BOM and every XML declaration / DOCTYPE: several pasted documents carry one
    each, and inside the wrapper element we parse them in, any of them is a syntax error."""
    return _XML_PROLOG.sub(lambda m: m.group(1) or "", text.lstrip("\ufeff"))


def _named_entities_to_numeric(text: str) -> str:
    """``&InvisibleTimes;``, ``&nbsp;``... (MathML / HTML names, common in exported MathML)
    as numeric references, which a plain XML parser understands."""
    def numeric(m):
        value = None if m.group(1) in _XML_ENTITIES else html.entities.html5.get(m.group(1) + ";")
        return "".join("&#x%X;" % ord(c) for c in value) if value else m.group(0)
    return _outside_literals(text, lambda s: _NAMED_ENTITY.sub(numeric, s))


def _local(name: str) -> str:
    """``{namespace}tag`` -> ``tag``."""
    return name.rsplit("}", 1)[-1]


def _outermost(root: ET.Element, match) -> List[ET.Element]:
    """Descendants of `root` satisfying `match`, in document order, not descending into a match."""
    found: List[ET.Element] = []

    def walk(el):
        for child in el:
            if match(child):
                found.append(child)
            else:
                walk(child)

    walk(root)
    return found


# characters that make up relation symbols (used to spot an equation array's alignment column):
# arrows, and the relations among the mathematical operators (not ∪ ⊕ ⊗ ... between them)
_RELATION_CHARS = frozenset("=<>:∈∉∊∋∌∍∝∣∤∥∦⋈⋔").union(map(chr, (
    *range(0x2190, 0x2200), *range(0x27F0, 0x2800), *range(0x2900, 0x2980),  # arrows
    *range(0x2236, 0x223E), *range(0x2241, 0x228C), *range(0x228F, 0x2293),  # ∶ ∼, ≁ ... ⊋, ⊏ ... ⊒
    *range(0x22A2, 0x22B9), *range(0x22D0, 0x22D2), *range(0x22D6, 0x22EE),  # ⊢ ... ⊸, ⋐ ⋑, ⋖ ... ⋭
    *range(0x22F2, 0x2300), *range(0x2A66, 0x2AFB)))).difference(          # ⋲ ... ⋿, ⩦ ... ⫺
    "⊤∸∺⩱⩲⫵⫶")  # an ordinary symbol and binary operators inside those ranges


def _leading_mo(el: ET.Element, ns: str) -> Optional[ET.Element]:
    """The <mo> an element starts with: itself, or the base of an over/under-decorated operator
    (``\\overset{!}{=}`` is an <mover> over <mo>=</mo>)."""
    while el.tag in (ns + "mover", ns + "munder", ns + "munderover") and len(el):
        el = el[0]
    return el if el.tag == ns + "mo" else None


def _is_relation(mo: ET.Element) -> bool:
    text = (mo.text or "").strip()
    return bool(text) and set(text) <= _RELATION_CHARS


def _is_empty(el: ET.Element) -> bool:
    """No text and nothing but <mrow>s: an empty table cell (not ``\\sqrt{}``, not spacing)."""
    return not "".join(el.itertext()).strip() and all(_local(e.tag) in ("mtd", "mrow") for e in el.iter())


def _cell_start(cell: ET.Element, ns: str) -> Optional[str]:
    """How the right-hand cell of an aligned block starts: "empty", "relation" (``&= b``), "operator"
    (a continuation line, ``&+ c``), "space" (spacing followed by anything else, ``&\\quad x``), or
    None for anything else (a table of values).  Spacing before an operator is looked past
    (``&\\quad = b``; Word exports ``&\\quad + c`` as ``<mo> </mo><mo>+</mo>``)."""
    if _is_empty(cell):
        return "empty"
    spaced = False
    for child in cell:
        if child.tag == ns + "mspace" or _is_empty(child) or (child.tag == ns + "mo" and not (child.text or "").strip()):
            spaced = True
            continue
        mo = _leading_mo(child, ns)
        if mo is not None:
            return "relation" if _is_relation(mo) else "operator"
        break
    return "space" if spaced else None


def _mark_equation_array_alignment(math: ET.Element) -> bool:
    """Word/PowerPoint export an equation array as an <mtable> without any alignment
    attributes, which texmath turns into ``\\begin{matrix}``.  When the table has the
    shape of an aligned block (every row = 2 cells, the second one empty or starting with an
    operator or a space - a continuation line such as ``&+ c`` - and at least one of them
    starting with a relation), add ``columnalign`` so that texmath emits ``\\begin{aligned}``.
    Returns True if changed."""
    ns = math.tag[:math.tag.index("}") + 1] if math.tag.startswith("{") else ""
    node = math
    while len(node) == 1 and node[0].tag in (ns + "mrow", ns + "semantics", ns + "mstyle"):
        node = node[0]
    tables = [c for c in node if c.tag == ns + "mtable"]
    if len(node) != 1 or not tables:
        return False
    table = tables[0]
    if table.get("columnalign"):
        return False
    rows = [r for r in table if r.tag == ns + "mtr"]
    if not rows:
        return False
    relations = 0
    for row in rows:
        cells = [c for c in row if c.tag == ns + "mtd"]
        if len(cells) != 2 or any(c.get("columnalign") for c in cells):
            return False
        start = _cell_start(cells[1], ns)
        if start is None:
            return False
        relations += start == "relation"
    if not relations:
        return False
    for row in rows:
        cells = [c for c in row if c.tag == ns + "mtd"]
        cells[0].set("columnalign", "right")
        cells[1].set("columnalign", "left")
    return True


def _strip_namespaces(el: ET.Element) -> None:
    for e in el.iter():
        if isinstance(e.tag, str):
            e.tag = _local(e.tag)
        for key in [k for k in e.attrib if k.startswith("{")]:
            e.attrib[_local(key)] = e.attrib.pop(key)


def _serialize_math(math: ET.Element) -> bytes:
    math.tail = None  # tostring() would include the text that follows the element
    return ET.tostring(math, encoding="unicode").encode("utf-8")


def read_mathml(text: str, fix_office_alignment: bool = True) -> List[Equation]:
    if not text.strip():
        raise ConversionError("The input is empty.")
    body = _named_entities_to_numeric(_strip_xml_prolog(text))
    try:
        # the mml prefix is declared so that a pasted <mml:math> fragment parses without its declaration
        root = ET.fromstring('<r xmlns:mml="%s">%s</r>' % (MATHML_NS, body))
    except ET.ParseError as e:
        raise ConversionError("The MathML is not well-formed XML: %s" % e)
    maths = _outermost(root, lambda el: isinstance(el.tag, str) and _local(el.tag) == "math")
    if not maths:
        raise ConversionError("No <math> element found in the MathML input.")
    for math in maths:
        _strip_namespaces(math)  # pandoc only recognises a bare <math>, not <mml:math>
        if fix_office_alignment:
            _mark_equation_array_alignment(math)
    eqs = _read_json("html", b"".join(_serialize_math(m) for m in maths))
    if len(eqs) < len(maths):  # pandoc turns a <math> that texmath rejects into plain text, silently
        if len(maths) == 1:
            raise ConversionError("texmath could not interpret this MathML (unsupported or malformed elements).")
        bad = [str(i) for i, m in enumerate(maths, 1) if not _read_json("html", _serialize_math(m))]
        raise ConversionError("texmath could not interpret <math> element %s of the %d in the input "
                              "(unsupported or malformed elements)." % (", ".join(bad) or "?", len(maths)))
    _check_not_empty(eqs, "MathML")
    return eqs


_DOCX_CONTENT_TYPES = (
    '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
    '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
    '<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>'
    '<Default Extension="xml" ContentType="application/xml"/>'
    '<Override PartName="/word/document.xml" '
    'ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml"/>'
    '</Types>')
_DOCX_RELS = (
    '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
    '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
    '<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" '
    'Target="word/document.xml"/></Relationships>')


def _parse_omml_fragments(text: str) -> List[ET.Element]:
    """Return the top-level <m:oMathPara>/<m:oMath> elements found in arbitrary OMML-ish XML."""
    body = _strip_xml_prolog(text)
    # Wrap so that undeclared m:/w: prefixes (common when OMML is pasted from a snippet) resolve.
    wrapped = ('<r xmlns:m="%s" xmlns:w="%s" xmlns:mc="%s" '
               'xmlns:w14="http://schemas.microsoft.com/office/word/2010/wordml" '
               'xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships">%s</r>') % (
                   OMML_NS, WML_NS, MC_NS, body)
    try:
        root = ET.fromstring(wrapped)
    except ET.ParseError as e:
        raise ConversionError("The OMML is not well-formed XML: %s" % e)
    tags = ("{%s}oMathPara" % OMML_NS, "{%s}oMath" % OMML_NS)
    alternatives, choice = "{%s}AlternateContent" % MC_NS, "{%s}Choice" % MC_NS
    found: List[ET.Element] = []

    def walk(el):
        for child in el:
            if child.tag in tags:
                found.append(child)  # an oMath inside an oMathPara belongs to it
            elif child.tag == alternatives:
                # the same content once per branch (e.g. an equation in a text box: DrawingML choice +
                # VML fallback, possibly several choices): read the first choice, else the fallback
                branches = list(child)
                branch = next((b for b in branches if b.tag == choice), branches[0] if branches else None)
                if branch is not None:
                    walk(branch)
            else:
                walk(child)

    walk(root)
    if not found:
        raise ConversionError("No <m:oMath> or <m:oMathPara> element found in the OMML input.")
    return found


def _check_not_empty(eqs: List[Equation], what: str) -> None:
    """texmath skips elements it does not know (OMML) and yields an empty equation for an empty one:
    report that instead of converting it to an empty result."""
    empty = [str(i) for i, e in enumerate(eqs, 1) if not e.tex]
    if empty:
        where = "this %s" % what if len(eqs) == 1 else "%s equation %s of the %d in the input" % (
            what, ", ".join(empty), len(eqs))
        raise ConversionError("texmath could not interpret %s: it is empty or holds only elements "
                              "texmath does not support." % where)


def read_omml(text: str) -> List[Equation]:
    if not text.strip():
        raise ConversionError("The input is empty.")
    fragments = _parse_omml_fragments(text)
    for f in fragments:
        f.tail = None  # tostring() would copy the text that follows the element into the paragraph
    paras = "".join("<w:p>%s</w:p>" % ET.tostring(f, encoding="unicode") for f in fragments)
    document = ('<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
                '<w:document xmlns:w="%s" xmlns:m="%s"><w:body>%s</w:body></w:document>') % (WML_NS, OMML_NS, paras)
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("[Content_Types].xml", _DOCX_CONTENT_TYPES)
        z.writestr("_rels/.rels", _DOCX_RELS)
        z.writestr("word/document.xml", document)
    eqs = _read_json("docx", buf.getvalue())
    if not eqs:
        raise ConversionError("texmath could not interpret this OMML.")
    _check_not_empty(eqs, "OMML")
    return eqs


def read_equations(text: str, fmt: str, default_display: bool = True) -> List[Equation]:
    if fmt == "latex":
        return read_latex(text, default_display)
    if fmt == "mathml":
        return read_mathml(text)
    if fmt == "omml":
        return read_omml(text)
    raise ValueError("unknown input format %r" % fmt)


# --- writing --------------------------------------------------------------------------
def _doc_json(eq: Equation) -> bytes:
    doc = {"pandoc-api-version": _api(), "meta": {},
           "blocks": [{"t": "Para", "c": [{"t": "Math", "c": [{"t": "DisplayMath" if eq.display else "InlineMath"}, eq.tex]}]}]}
    return json.dumps(doc).encode("utf-8")


def _pretty(el: ET.Element) -> str:
    if hasattr(ET, "indent"):
        ET.indent(el, space="  ")
    return ET.tostring(el, encoding="unicode")


_MATH_RE = re.compile(r"<math[\s>][\s\S]*?</math>")


_PAIRS = ("right", "left")
_EQNARRAY = ["right", "center", "left"]


def _table_shape(rows: List[ET.Element]) -> Optional[str]:
    """"pairs" for right/left cell pairs (align, aligned, split, alignat), "eqnarray" for eqnarray's
    right/center/left, None for anything else (matrix, cases, gather...).  Rows may be short (a
    trailing \\\\, a line without &): only the cells they have must fit the pattern."""
    aligns = [[c.get("columnalign") for c in r] for r in rows]
    width = max(map(len, aligns))
    if width >= 2 and all(a == _PAIRS[i % 2] for row in aligns for i, a in enumerate(row)):
        return "pairs"
    if width == 3 and all(row == _EQNARRAY[:len(row)] for row in aligns):
        return "eqnarray"
    return None


def _reads_as_alignment(rows: List[ET.Element], shape: str, ns: str) -> bool:
    """For a table that comes from `array`: True if its cells read like an aligned block - every
    right-hand cell (pairs) empty or starting with an operator or a space, every middle cell
    (eqnarray) empty or a relation, and at least one relation - rather than a table of values."""
    relations = 0
    for row in rows:
        cells = list(row)
        for cell in (cells[1::2] if shape == "pairs" else cells[1:2]):
            start = _cell_start(cell, ns)
            if start is None or (shape == "eqnarray" and start not in ("empty", "relation")):
                return False
            relations += start == "relation"
    return relations > 0


def _office_equation_arrays(root: ET.Element, arrays: Optional[set] = frozenset()) -> None:
    """Mark aligned blocks so that Word / PowerPoint paste them as an equation array.

    Office turns a pasted <mtable> into a matrix with centred columns - the = signs no longer line
    up - unless it has ``columnspacing="0em"``: that is how Word itself exports an equation array
    (right/left cell pairs, as texmath writes them, in a table without column gaps), and such a
    table becomes an <m:eqArr> aligned at the & points.  Checked with Word / PowerPoint 16.
    Short rows are padded to the table's width and a trailing empty row (a final \\\\) is dropped.
    eqnarray's relation column is merged into the right-hand side (``a & = b``): as a third cell it
    would open a new alignment column.

    `arrays` holds the indices (document order) of the tables that come from an `array`
    environment, None if unknown: such a table is marked only if it reads like an alignment.
    """
    ns = "{%s}" % MATHML_NS
    for index, table in enumerate(list(root.iter(ns + "mtable"))):
        if any(table.get(a, "none") != "none" for a in ("frame", "rowlines", "columnlines")):
            continue  # an array with rules is not an alignment (texmath 0.13 writes none of these)
        rows = list(table)
        if not rows or any(r.tag != ns + "mtr" or any(c.tag != ns + "mtd" for c in r) for r in rows):
            continue  # labelled rows (mlabeledtr) or other content
        shape = _table_shape(rows)
        if shape is None:
            continue
        if (arrays is None or index in arrays) and not _reads_as_alignment(rows, shape, ns):
            continue
        while len(rows) > 1 and all(_is_empty(c) for c in rows[-1]):
            table.remove(rows.pop())
        pattern = _EQNARRAY if shape == "eqnarray" else _PAIRS * ((max(len(r) for r in rows) + 1) // 2)
        for row in rows:
            for align in pattern[len(row):]:
                ET.SubElement(row, ns + "mtd", columnalign=align)
            if shape == "eqnarray":
                _, relation, rhs = list(row)
                rhs[:0] = list(relation)
                row.remove(relation)
        table.set("columnspacing", "0em")


_ARRAY_BEGIN = re.compile(r"\\begin%s\{(?:array|subarray|darray)\}%s(?:\[[^\[\]]*\]%s)?\{" % ((_TEX_GAP,) * 3))


def _centre_array_columns(tex: str) -> str:
    """`tex` with the l / r columns of every array environment made c (control words such as
    \\linewidth in the column spec are left alone)."""
    out, last = [], 0
    for m in _ARRAY_BEGIN.finditer(tex):
        depth, i = 1, m.end()
        while i < len(tex) and depth:
            depth += {"{": 1, "}": -1}.get(tex[i], 0)
            i += 1
        spec = re.sub(r"\\[a-zA-Z@]+|[lr]", lambda t: "c" if len(t.group()) == 1 else t.group(), tex[m.end():i])
        out.append(tex[last:m.end()] + spec)
        last = i
    return "".join(out) + tex[last:]


def _array_tables(eq: Equation, root: ET.Element) -> Optional[set]:
    """Indices (document order) of the <mtable>s of `root` (texmath's MathML for `eq`) that come from
    an array environment, which texmath renders exactly like aligned: the TeX is converted once more
    with the arrays' columns centred, and the tables whose alignment changed are the arrays.
    None when that cannot be told."""
    if not _ARRAY_BEGIN.search(eq.tex):
        return set()
    try:
        centred = ET.fromstring(write_mathml(Equation(_centre_array_columns(eq.tex), eq.display)))
    except (ConversionError, ET.ParseError):
        return None

    def alignments(tree):
        return [[[c.get("columnalign") for c in r] for r in t] for t in tree.iter("{%s}mtable" % MATHML_NS)]

    before, after = alignments(root), alignments(centred)
    if len(before) != len(after):
        return None
    return {i for i, (a, b) in enumerate(zip(before, after)) if a != b}


def write_mathml(eq: Equation, annotate: bool = False, for_office: bool = False) -> str:
    """MathML for the equation.  With `annotate`, the TeX source is kept in a <semantics>/<annotation>;
    `for_office` shapes aligned blocks for Word / PowerPoint (see `_office_equation_arrays`)."""
    out, err = _run(["-f", "json", "-t", "html", _LF, *_mathml_flags()], _doc_json(eq))
    _check_texmath_warning(err)
    m = _MATH_RE.search(out.decode("utf-8"))
    if not m:
        raise ConversionError("pandoc produced no MathML:\n" + err.strip())
    root = ET.fromstring(m.group(0))
    sem = root.find("{%s}semantics" % MATHML_NS)
    if sem is not None and not annotate:
        root.remove(sem)
        for child in list(sem):
            if child.tag != "{%s}annotation" % MATHML_NS:
                root.append(child)
    if for_office:
        _office_equation_arrays(root, _array_tables(eq, root))
    return _pretty(root)


def write_omml(eq: Equation) -> str:
    out, err = _run(["-f", "json", "-t", "docx", "-o", "-"], _doc_json(eq))
    _check_texmath_warning(err)
    with zipfile.ZipFile(io.BytesIO(out)) as z:
        document = ET.fromstring(z.read("word/document.xml"))
    para_tag, math_tag = "{%s}oMathPara" % OMML_NS, "{%s}oMath" % OMML_NS
    for el in document.iter():
        if el.tag in (para_tag, math_tag):
            return _pretty(el)
    raise ConversionError("pandoc produced no OMML:\n" + err.strip())


def write_eqn(eq: Equation) -> str:
    """groff eqn source (body only, without the .EQ/.EN macros)."""
    out, err = _run(["-f", "json", "-t", "ms", _LF], _doc_json(eq))
    _check_texmath_warning(err)
    text = out.decode("utf-8")
    if eq.display:
        m = re.search(r"^\.EQ\n([\s\S]*?)\n\.EN", text, re.M)
        if m:
            return m.group(1)
    lines = [ln for ln in text.splitlines() if not ln.startswith(".")]
    body = "\n".join(lines).strip()
    if body.startswith("@") and body.endswith("@"):
        body = body[1:-1]
    if not body:
        raise ConversionError("pandoc produced no eqn output:\n" + err.strip())
    return body


# pandoc writes a \label as a Typst label after the closing $:  $ x = 1 $<eq:a>, or, when the name is not
# valid label syntax (eq/loss, eq(1), a space...):  $ x = 1 $#label("eq/loss")
_TYPST_MATH = re.compile(r'^\$([\s\S]*)\$\s*(?:<[^<>\s]+>|#label\("(?:[^"\\]|\\.)*"\))?$')


def write_typst(eq: Equation) -> str:
    """Typst math (body only, without the $ delimiters or a trailing <label>)."""
    out, err = _run(["-f", "json", "-t", "typst", _LF], _doc_json(eq))
    _check_texmath_warning(err)
    text = out.decode("utf-8").strip()
    m = _TYPST_MATH.match(text)
    if m:
        text = m.group(1).strip()
    if not text:
        raise ConversionError("pandoc produced no Typst output:\n" + err.strip())
    return text


def write_equation(eq: Equation, fmt: str, **opts) -> str:
    if fmt == "mathml":
        return write_mathml(eq, annotate=opts.get("annotate", False), for_office=opts.get("for_office", False))
    if fmt == "omml":
        return write_omml(eq)
    if fmt == "eqn":
        return write_eqn(eq)
    if fmt == "typst":
        return write_typst(eq)
    raise ValueError("unknown output format %r" % fmt)
