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
_LATEX_TOKEN = re.compile(r"\\begin\s*\{([^{}]*)\}|\\[a-zA-Z@]+|\\.|%[^\n]*|[{}$]", re.S)


def _has_math_delimiters(text: str) -> bool:
    """True if `text` has $, \\(, \\[ or a maths environment outside every brace group.

    None of those can occur at the top level of math-mode TeX, while prose with equations
    always has one - so input without them is a single bare equation, even when pandoc
    would find maths nested in it (``\\mbox{for all $n$}``).
    """
    depth = 0
    for m in _LATEX_TOKEN.finditer(text):
        tok = m.group()
        if tok == "{":
            depth += 1
        elif tok == "}":
            depth = max(depth - 1, 0)
        elif depth == 0 and (tok in ("$", "\\(", "\\[") or m.group(1) in _PANDOC_MATH_ENVS):
            return True
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


_XML_DECLS = re.compile(r"<\?xml\s[^>]*\?>|<!DOCTYPE[^>]*>", re.I)
_XML_ENTITIES = ("lt", "gt", "amp", "quot", "apos")
_NAMED_ENTITY = re.compile(r"&([A-Za-z][A-Za-z0-9]*);")


def _strip_xml_prolog(text: str) -> str:
    """Drop a BOM and every XML declaration / DOCTYPE: several pasted documents carry one
    each, and inside the wrapper element we parse them in, any of them is a syntax error."""
    return _XML_DECLS.sub("", text.lstrip("\ufeff"))


def _named_entities_to_numeric(text: str) -> str:
    """``&InvisibleTimes;``, ``&nbsp;``... (MathML / HTML names, common in exported MathML)
    as numeric references, which a plain XML parser understands."""
    def numeric(m):
        value = None if m.group(1) in _XML_ENTITIES else html.entities.html5.get(m.group(1) + ";")
        return "".join("&#x%X;" % ord(c) for c in value) if value else m.group(0)
    return _NAMED_ENTITY.sub(numeric, text)


def _local(name: str) -> str:
    """``{namespace}tag`` -> ``tag``."""
    return name.rsplit("}", 1)[-1]


def _outermost(root: ET.Element, match, skip=lambda el: False) -> List[ET.Element]:
    """Descendants of `root` satisfying `match`, in document order, not descending into a match
    or into an element satisfying `skip`."""
    found: List[ET.Element] = []

    def walk(el):
        for child in el:
            if match(child):
                found.append(child)
            elif not skip(child):
                walk(child)

    walk(root)
    return found


# characters that make up relation symbols (used to spot an equation array's alignment column)
_RELATION_CHARS = set("=<>:≠≤≥≪≫≈≃≅≡∼≍∝∈∉∋⊂⊆⊃⊇→←↔⇒⇐⇔⟶⟵⟹⟸⟺↦≔≕∣⊥∥≺≻⪯⪰⊢⊨≐≜≝≟⩽⩾≶≷⊏⊑⊐⊒")


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
        if not len(cells[1]):
            continue
        first = cells[1][0]
        if first.tag == ns + "mspace":
            continue
        if first.tag != ns + "mo":
            return False
        text = (first.text or "").strip()
        if text and set(text) <= _RELATION_CHARS:
            relations += 1
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
    # an oMath inside an oMathPara belongs to it; mc:AlternateContent holds the same content twice
    # (e.g. an equation in a text box: DrawingML choice + VML fallback) - read the choice only
    found = _outermost(root, lambda el: el.tag in tags, skip=lambda el: el.tag == "{%s}Fallback" % MC_NS)
    if not found:
        raise ConversionError("No <m:oMath> or <m:oMathPara> element found in the OMML input.")
    return found


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


def _aligned_row(aligns: List[Optional[str]]) -> Optional[str]:
    """"pairs" for right/left cell pairs (align, aligned, split, alignat, array{rl}), "eqnarray" for
    eqnarray's right/center/left, None for anything else (matrix, cases, gather...)."""
    if aligns == ["right", "center", "left"]:
        return "eqnarray"
    if len(aligns) >= 2 and all(a == ("right", "left")[i % 2] for i, a in enumerate(aligns)):
        return "pairs"
    return None


def _office_equation_arrays(root: ET.Element) -> None:
    """Mark aligned blocks so that Word / PowerPoint paste them as an equation array.

    Office turns a pasted <mtable> into a matrix with centred columns - the = signs no longer line
    up - unless it has ``columnspacing="0em"``: that is how Word itself exports an equation array
    (right/left cell pairs, as texmath writes them, in a table without column gaps), and such a
    table becomes an <m:eqArr> aligned at the & points.  Checked with Word / PowerPoint 16.
    eqnarray's relation column is merged into the right-hand side (``a & = b``): as a third cell it
    would open a new alignment column.
    """
    ns = "{%s}" % MATHML_NS
    for table in list(root.iter(ns + "mtable")):
        if any(table.get(a, "none") != "none" for a in ("frame", "rowlines", "columnlines")):
            continue  # an array with rules is not an alignment
        rows = list(table)
        if not rows or any(r.tag != ns + "mtr" or any(c.tag != ns + "mtd" for c in r) for r in rows):
            continue  # labelled rows (mlabeledtr) or other content
        kinds = [_aligned_row([c.get("columnalign") for c in r]) for r in rows]
        if None in kinds:
            continue
        for row, kind in zip(rows, kinds):
            if kind == "eqnarray":
                _, relation, rhs = list(row)
                rhs[:0] = list(relation)
                row.remove(relation)
        table.set("columnspacing", "0em")


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
        _office_equation_arrays(root)
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


# pandoc writes a \label as a Typst label after the closing $:  $ x = 1 $<eq:a>
_TYPST_MATH = re.compile(r"^\$([\s\S]*)\$\s*(?:<[^<>\s]+>)?$")


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


def validate_tex(eq: Equation) -> None:
    """Raise ConversionError if texmath cannot parse the TeX (used before copying to Office)."""
    write_mathml(eq)
