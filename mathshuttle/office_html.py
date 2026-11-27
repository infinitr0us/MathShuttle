"""The equations inside the HTML that Word copies (macOS ``public.html``, Windows ``HTML Format``).

Word copies a selection as HTML too, with each equation as OMML inside a conditional comment::

    <!--[if gte msEquation 12]><m:oMathPara><m:oMath><m:sSup>...<i style='...'><span lang=EN-CA
    style='...'><m:r>x</m:r></span></i>...</m:oMath></m:oMathPara><![endif]--><![if !msEquation]>
    <img ...><![endif]>

That OMML is not well-formed as it stands: HTML formatting tags (``<span>``, ``<i>``, ``<br>``) sit
between the OMML elements, a run's text is not wrapped in ``<m:t>``, and both text and attribute
values are HTML (entities, unquoted values, line breaks from wrapping).  :func:`omml_from_html`
turns it back into OMML that :func:`.pandoc_engine.read_omml` reads.

This is the fallback for what Word copies without MathML: a selection of text with equations in it
(Word on Windows and macOS), and a math paragraph of several aligned lines (Mac Word 16.115, which
announces MathML but delivers none).  The formatting the HTML tags carried (font, colour) is not
needed for the LaTeX; the OMML's own properties (``m:sty``, ``m:scr``, ``m:aln``...) are kept.
"""
from __future__ import annotations

import html
import re
from typing import List, Optional

_BLOCK = re.compile(r"<!--\[if gte msEquation 12\]>(.*?)<!\[endif\]-->", re.S | re.I)
_FRAGMENT = re.compile(r"<!--\s*StartFragment\s*-->(.*?)<!--\s*EndFragment\s*-->", re.S | re.I)
# a start, end or empty tag; quoted attribute values may hold ">"
_TAG = re.compile(r"<(/?)([A-Za-z][\w:.-]*)((?:[^>\"']|\"[^\"]*\"|'[^']*')*)>", re.S)
# an HTML attribute: name, and a double-quoted, single-quoted or unquoted value
_ATTR = re.compile(r"([A-Za-z_][\w:.-]*)(?:\s*=\s*(?:\"([^\"]*)\"|'([^']*)'|([^\s\"'=<>`]+)))?", re.S)


def cf_html_fragment(raw: bytes) -> bytes:
    """The fragment of Windows "HTML Format" data (the copied selection, between the byte offsets of
    its header's StartFragment / EndFragment), or the data as it is if the header does not say."""
    head = raw[:600]
    start, end = re.search(rb"StartFragment:\s*(\d+)", head), re.search(rb"EndFragment:\s*(\d+)", head)
    if start and end:
        a, b = int(start.group(1)), int(end.group(1))
        if 0 <= a <= b <= len(raw):
            return raw[a:b]
    return raw


def _attributes(raw: str) -> "tuple[str, bool]":
    """HTML attributes -> XML attributes (values decoded, then quoted and escaped); and whether the tag
    closes itself."""
    raw = raw.strip()
    empty = raw.endswith("/")
    if empty:
        raw = raw[:-1]
    out = []
    for m in _ATTR.finditer(raw):
        value = next((v for v in m.group(2, 3, 4) if v is not None), None)
        if value is not None:
            out.append(' %s="%s"' % (m.group(1), html.escape(html.unescape(value), quote=True)))
    return "".join(out), empty


def _run_text(raw: str) -> str:
    """The text of a run as HTML gives it: a line break with the white space around it (the HTML's
    wrapping, or the indentation after a <br>) is a space between words and nothing at either end."""
    text = re.sub(r"^[ \t]*(?:\r?\n[ \t]*)+", "", raw)
    text = re.sub(r"(?:[ \t]*\r?\n)+[ \t]*$", "", text)
    return html.unescape(re.sub(r"[ \t]*(?:\r?\n[ \t]*)+", " ", text))


_TEXT_ELEMENT = re.compile(r"<m:t[\s>/]", re.I)


class _Run:
    """An <m:r> being read: its start tag, its pieces (OMML elements, and its own text, which goes into
    an <m:t>), and whether the HTML put a line break (<br>) into it."""

    def __init__(self, start_tag: str):
        self.start_tag = start_tag
        self.pieces: List[str] = []
        self.text: List[str] = []
        self.line_break = False

    def flush_text(self) -> None:
        text = _run_text("".join(self.text))
        self.text = []
        if text:
            self.pieces.append('<m:t xml:space="preserve">%s</m:t>' % html.escape(text, quote=False))

    def xml(self) -> str:
        self.flush_text()
        if not any(_TEXT_ELEMENT.match(p) for p in self.pieces) and (self.line_break or not self.pieces):
            return ""  # empty, or the run that held Word's <br> between two lines of a math paragraph
        return self.start_tag + "".join(self.pieces) + "</m:r>"


def clean_block(block: str) -> str:
    """One conditional-comment block of Word's HTML -> OMML (``m:`` prefixes, namespace undeclared)."""
    out: List[str] = []
    stack: List["tuple[str, str]"] = []  # (lower-case name, spelling) of the open OMML elements
    run: Optional[_Run] = None           # the <m:r> being read
    in_text = False                      # inside an <m:t> of that run

    def emit(piece: str) -> None:
        (run.pieces if run is not None else out).append(piece)

    def text(raw: str) -> None:
        if not raw:
            return
        if in_text:
            emit(html.escape(_run_text(raw), quote=False))
        elif run is not None:
            run.text.append(raw)
        elif raw.strip():  # text outside a run is not OMML: kept as text, which texmath ignores
            emit(html.escape(html.unescape(raw), quote=False))

    def close_top() -> None:
        nonlocal run, in_text
        lower, spelled = stack.pop()
        if lower == "m:r":
            finished, run = run, None
            if finished is not None:
                emit(finished.xml())
            return
        if lower == "m:t":
            in_text = False
        emit("</%s>" % spelled)

    pos = 0
    for m in _TAG.finditer(block):
        text(block[pos:m.start()])
        pos = m.end()
        closing, name, raw_attrs = m.group(1), m.group(2), m.group(3)
        lower = name.lower()
        if not lower.startswith("m:"):
            if lower == "br" and run is not None:
                run.line_break = True
            continue  # HTML formatting
        if closing:
            if any(open_name == lower for open_name, _ in stack):
                while stack:
                    top = stack[-1][0]
                    close_top()
                    if top == lower:
                        break
            continue  # (an end tag without its start tag is dropped)
        attrs, empty = _attributes(raw_attrs)
        if lower == "m:r":
            if empty:
                continue
            while run is not None:  # a run inside a run is not OMML: the open one ends here
                close_top()
            run = _Run("<%s%s>" % (name, attrs))
            stack.append((lower, name))
            continue
        if run is not None and not in_text:
            run.flush_text()  # the run's own text before this element
        if empty:
            emit("<%s%s/>" % (name, attrs))
            continue
        emit("<%s%s>" % (name, attrs))
        stack.append((lower, name))
        if lower == "m:t":
            in_text = True
    text(block[pos:])
    while stack:  # elements the HTML left open
        close_top()
    return "".join(out)


def omml_from_html(text: Optional[str]) -> Optional[str]:
    """The OMML of every equation in Word's HTML (only in the copied fragment when the HTML marks it),
    one fragment after the other; None if there is none."""
    if not text:
        return None
    fragment = _FRAGMENT.search(text)
    if fragment:
        text = fragment.group(1)
    blocks = [clean_block(b) for b in _BLOCK.findall(text)]
    blocks = [b for b in blocks if re.search(r"<m:oMath\b", b, re.I)]
    return "\n".join(blocks) if blocks else None
