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

import io
import json
import os
import re
import shutil
import subprocess
import sys
import zipfile
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from typing import List, Optional

MATHML_NS = "http://www.w3.org/1998/Math/MathML"
OMML_NS = "http://schemas.openxmlformats.org/officeDocument/2006/math"
WML_NS = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"

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
    found = shutil.which("pandoc")
    if found:
        yield found
    for root in (os.environ.get("LOCALAPPDATA"), os.environ.get("ProgramFiles"), os.environ.get("ProgramFiles(x86)")):
        if root:
            yield os.path.join(root, "Pandoc", "pandoc.exe")


def find_pandoc() -> str:
    global _pandoc_path
    if _pandoc_path is None:
        for p in _candidate_paths():
            if p and os.path.isfile(p):
                _pandoc_path = p
                break
        else:
            raise PandocNotFound(
                "pandoc.exe was not found. Install it with  winget install JohnMacFarlane.Pandoc\n"
                "(or set EQUATION_TOOL_PANDOC to the full path of pandoc.exe).")
    return _pandoc_path


def pandoc_version() -> str:
    out, _ = _run(["--version"], b"")
    return out.decode("utf-8", "replace").splitlines()[0]


def _run(args: List[str], stdin: bytes, timeout: float = 60) -> "tuple[bytes, str]":
    try:
        proc = subprocess.run([find_pandoc(), *args], input=stdin, capture_output=True,
                              timeout=timeout, creationflags=_CREATE_NO_WINDOW)
    except subprocess.TimeoutExpired:
        raise ConversionError("pandoc did not finish within %d s" % timeout)
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


_TEXMATH_WARNING = re.compile(r"\[WARNING\] Could not convert TeX math (.*?), rendering as TeX:\n((?:  .*\n?)*)")


def _check_texmath_warning(stderr: str) -> None:
    m = _TEXMATH_WARNING.search(stderr)
    if m:
        detail = "\n".join(line[2:] for line in m.group(2).rstrip("\n").splitlines())
        raise ConversionError("texmath could not parse this LaTeX:\n" + detail)


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


def read_latex(text: str, default_display: bool = True) -> List[Equation]:
    """Extract every equation from LaTeX source.

    Delimited maths ($...$, \\(...\\), $$...$$, \\[...\\], equation/align/gather/
    multline/eqnarray/... environments, several of them, with prose in between)
    are recognised by pandoc's LaTeX reader; \\newcommand definitions in the
    input are expanded.  Input with no delimiters at all is taken as one bare
    equation in the `default_display` mode.
    """
    if not text.strip():
        raise ConversionError("The input is empty.")
    eqs = _read_json("latex", text.encode("utf-8"))
    if eqs:
        return eqs
    return [Equation(text.strip(), default_display)]


_XML_PROLOG = re.compile(r"^\s*(<\?xml[^>]*\?>\s*)?(<!DOCTYPE[^>]*>\s*)?", re.I)

# characters that make up relation symbols (used to spot an equation array's alignment column)
_RELATION_CHARS = set("=<>:≠≤≥≪≫≈≃≅≡∼≍∝∈∉∋⊂⊆⊃⊇→←↔⇒⇐⇔⟶⟵⟹⟸⟺↦≔≕∣⊥∥≺≻⪯⪰⊢⊨≐≜≝≟⩽⩾≶≷⊏⊑⊐⊒")


def _mark_equation_array_alignment(math: ET.Element) -> bool:
    """Word/PowerPoint export an equation array as an <mtable> without any alignment
    attributes, which texmath turns into ``\\begin{matrix}``.  When the table has the
    shape of an aligned block (every row = 2 cells, second cell starting with a relation),
    add ``columnalign`` so that texmath emits ``\\begin{aligned}``.  Returns True if changed."""
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
    for row in rows:
        cells = [c for c in row if c.tag == ns + "mtd"]
        if len(cells) != 2 or any(c.get("columnalign") for c in cells):
            return False
        first = cells[1][0] if len(cells[1]) else None
        if first is None or first.tag != ns + "mo":
            return False
        text = (first.text or "").strip()
        if not text or not set(text) <= _RELATION_CHARS:
            return False
    for row in rows:
        cells = [c for c in row if c.tag == ns + "mtd"]
        cells[0].set("columnalign", "right")
        cells[1].set("columnalign", "left")
    return True


def read_mathml(text: str, fix_office_alignment: bool = True) -> List[Equation]:
    if not text.strip():
        raise ConversionError("The input is empty.")
    body = _XML_PROLOG.sub("", text, count=1)
    if "<math" not in body:
        raise ConversionError("No <math> element found in the MathML input.")
    try:
        root = ET.fromstring("<r>%s</r>" % body)
    except ET.ParseError as e:
        raise ConversionError("The MathML is not well-formed XML: %s" % e)
    if fix_office_alignment:
        changed = False
        for math in root.iter():
            if math.tag in ("{%s}math" % MATHML_NS, "math"):
                changed |= _mark_equation_array_alignment(math)
        if changed:
            body = "".join(ET.tostring(child, encoding="unicode") for child in root)
    eqs = _read_json("html", body.encode("utf-8"))
    if not eqs:
        raise ConversionError("texmath could not interpret this MathML (unsupported or malformed elements).")
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
    body = _XML_PROLOG.sub("", text, count=1)
    # Wrap so that undeclared m:/w: prefixes (common when OMML is pasted from a snippet) resolve.
    wrapped = ('<r xmlns:m="%s" xmlns:w="%s" xmlns:mc="http://schemas.openxmlformats.org/markup-compatibility/2006" '
               'xmlns:w14="http://schemas.microsoft.com/office/word/2010/wordml" '
               'xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships">%s</r>') % (OMML_NS, WML_NS, body)
    try:
        root = ET.fromstring(wrapped)
    except ET.ParseError as e:
        raise ConversionError("The OMML is not well-formed XML: %s" % e)
    para_tag, math_tag = "{%s}oMathPara" % OMML_NS, "{%s}oMath" % OMML_NS
    found: List[ET.Element] = []

    def walk(el):
        if el.tag in (para_tag, math_tag):
            found.append(el)
            return  # do not descend: oMath inside oMathPara belongs to it
        for child in el:
            walk(child)

    walk(root)
    if not found:
        raise ConversionError("No <m:oMath> or <m:oMathPara> element found in the OMML input.")
    return found


def read_omml(text: str) -> List[Equation]:
    if not text.strip():
        raise ConversionError("The input is empty.")
    fragments = _parse_omml_fragments(text)
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


def write_mathml(eq: Equation, annotate: bool = False) -> str:
    """MathML for the equation.  With `annotate`, the TeX source is kept in a <semantics>/<annotation>."""
    out, err = _run(["-f", "json", "-t", "html", *_mathml_flags()], _doc_json(eq))
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
    out, err = _run(["-f", "json", "-t", "ms"], _doc_json(eq))
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


def write_typst(eq: Equation) -> str:
    """Typst math (body only, without the $ delimiters)."""
    out, err = _run(["-f", "json", "-t", "typst"], _doc_json(eq))
    _check_texmath_warning(err)
    text = out.decode("utf-8").strip()
    if text.startswith("$") and text.endswith("$") and len(text) >= 2:
        text = text[1:-1].strip()
    if not text:
        raise ConversionError("pandoc produced no Typst output:\n" + err.strip())
    return text


def write_equation(eq: Equation, fmt: str, **opts) -> str:
    if fmt == "mathml":
        return write_mathml(eq, annotate=opts.get("annotate", False))
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
