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
# an HTML attribute: name, then a double-quoted, single-quoted or unquoted value (which may hold "/")
_ATTR = re.compile(r"([A-Za-z_][\w:.-]*)(\s*=\s*(?:\"([^\"]*)\"|'([^']*)'|([^\s\"'=<>`]+)))?", re.S)
# the OMML element names (ECMA-376 part 1, 22.1), to spell an element as OMML does whatever the HTML did
_OMML_NAMES = {n.lower(): n for n in (
    "acc accPr aln alnScr argPr argSz bar barPr baseJc begChr borderBox borderBoxPr box boxPr brk brkBin "
    "brkBinSub cGp cGpRule chr count cSp ctrlPr d defJc deg degHide den diff dispDef dPr e endChr eqArr eqArrPr "
    "f fName fPr func funcPr groupChr groupChrPr grow hideBot hideLeft hideRight hideTop interSp intLim intraSp "
    "jc lim limLoc limLow limLowPr limUpp limUppPr lit lMargin m mathFont mathPr maxDist mc mcJc mcPr mcs mPr "
    "mr nary naryLim naryPr noBreak nor num objDist oMath oMathPara oMathParaPr opEmu phant phantPr plcHide pos "
    "postSp preSp r rad radPr rMargin rPr rSp rSpRule scr sepChr show shp smallFrac sPre sPrePr sSub sSubPr "
    "sSubSup sSubSupPr sSup sSupPr strikeBLTR strikeH strikeTLBR strikeV sty sub subHide sup supHide t transp "
    "type vertJc wrapIndent wrapRight zeroAsc zeroDesc zeroWid").split()}
# elements whose content is maths (runs and structures): stray text there becomes a run
_CONTENT = frozenset("m:oMath m:e m:num m:den m:sub m:sup m:deg m:lim m:fName".split())


def _omml_name(name: str) -> str:
    """m:oMath, m:r... as OMML spells them; an unknown m: name as the HTML spelled it."""
    local = name.split(":", 1)[1]
    return "m:" + _OMML_NAMES.get(local.lower(), local)


def cf_html_fragment(raw: bytes) -> bytes:
    """The fragment of Windows "HTML Format" data (the copied selection, between the byte offsets of
    its header's StartFragment / EndFragment), or the data as it is if it has no usable header.  The
    header is the run of "Name:value" lines before the HTML, starting with Version; the fragment
    starts after it."""
    fields = {}
    pos = 0
    while pos < min(len(raw), 65536):
        end = raw.find(b"\n", pos)
        line = raw[pos:end if end >= 0 else len(raw)].rstrip(b"\r")
        m = re.match(rb"([A-Za-z]+):(.*)$", line)
        if not m or (not fields and m.group(1) != b"Version"):
            break
        fields[m.group(1).decode("ascii")] = m.group(2).strip()
        pos = end + 1 if end >= 0 else len(raw)
    try:
        a, b = int(fields["StartFragment"]), int(fields["EndFragment"])
    except (KeyError, ValueError):
        return raw
    return raw[a:b] if pos <= a <= b <= len(raw) else raw  # 0 / 0: offsets not filled in


_ILLEGAL_XML = re.compile("[\x00-\x08\x0e-\x1f]")


def _xml_text(text: str) -> str:
    """Decoded text that XML can hold: a vertical tab or form feed (a soft line break) is a space,
    other control characters go."""
    return _ILLEGAL_XML.sub("", text.replace("\x0b", " ").replace("\x0c", " "))


def _attributes(raw: str) -> "tuple[str, bool]":
    """The OMML attributes (m:val...) of an HTML tag as XML attributes: values decoded, quoted and
    escaped (an attribute without a value gets an empty one), each name once; other attributes (HTML's
    class, Office's o: / w16se: ...) go, their prefixes would not be declared.  And whether the tag
    closes itself (a "/" after the last attribute; an unquoted value may be "/")."""
    out, seen = [], set()
    last = 0
    for m in _ATTR.finditer(raw):
        if raw[last:m.start()].strip():
            break  # not attribute syntax: what follows is not an attribute either
        last = m.end()
        prefix, _, local = m.group(1).partition(":")
        if prefix.lower() != "m" or not re.match(r"[A-Za-z]+$", local) or local.lower() in seen:
            continue
        seen.add(local.lower())
        value = next((v for v in m.group(3, 4, 5) if v is not None), "")
        out.append(' m:%s="%s"' % (local.lower(), html.escape(_xml_text(html.unescape(value)), quote=True)))
    return "".join(out), raw[last:].strip() == "/"


def _collapse(text: str) -> str:
    """A run's text as HTML shows it.  White space with a line break in it is one space between words;
    at either end of the run it is the HTML's own layout (the indentation after a <br>, a line wrapped
    between two tags) and goes, unless a space came before the line break (a line wrapped after a
    space the text had)."""
    def white(m) -> str:
        s = m.group(0)
        if "\n" not in s:
            return s
        at_edge = m.start() == 0 or m.end() == len(text)
        return " " if not at_edge or s.split("\n", 1)[0].rstrip("\r") else ""
    return re.sub(r"[ \t\r\n]+", white, text)


class _Run:
    """An <m:r> being read: its start tag, its elements other than text (m:rPr), its text (each HTML
    text node decoded on its own, in order, and whether it was inside an <m:t>), whether it held a
    <br>."""

    def __init__(self, start_tag: str):
        self.start_tag = start_tag
        self.parts: List[str] = []
        self.text: "List[tuple[bool, str]]" = []
        self.line_break = False

    def xml(self) -> str:
        pieces = self.text
        if any(in_t for in_t, _ in pieces):  # with an <m:t>, white space between the run's tags is layout
            pieces = [(in_t, p) for in_t, p in pieces if in_t or p.strip(" \t\r\n")]
        text = _xml_text(_collapse("".join(p for _, p in pieces)))
        if self.line_break and not text.strip(" \t\r\n"):
            return ""  # the run that held Word's <br> between two lines of a math paragraph
        if not text and not self.parts:
            return ""
        t = '<m:t xml:space="preserve">%s</m:t>' % html.escape(text, quote=False) if text else ""
        return self.start_tag + "".join(self.parts) + t + "</m:r>"  # one <m:t>: a run is one piece of text


def clean_block(block: str) -> str:
    """One conditional-comment block of Word's HTML -> OMML (``m:`` prefixes, namespace undeclared)."""
    block = re.sub(r"<!--.*?-->", "", block, flags=re.S)  # comments are not content
    out: List[str] = []
    stack: List[str] = []      # the OMML elements open, as spelled in the output (m:oMath ...)
    run: Optional[_Run] = None  # the <m:r> being read; its content goes into it, not into `out`

    def emit(piece: str) -> None:
        (run.parts if run is not None else out).append(piece)

    def text(raw: str) -> None:
        if not raw:
            return
        top = stack[-1] if stack else ""
        if run is not None and top in ("m:r", "m:t"):
            run.text.append((top == "m:t", html.unescape(raw)))  # each node on its own: "&" + "lt;" is not "<"
        elif top in _CONTENT and raw.strip():  # text outside any run, in maths: a run of its own
            out.append('<m:r><m:t xml:space="preserve">%s</m:t></m:r>'
                       % html.escape(_xml_text(_collapse(html.unescape(raw))).strip(), quote=False))
        # anything else (white space between elements, text inside properties) is not maths

    def close_top() -> None:
        nonlocal run
        name = stack.pop()
        if name == "m:r":
            finished, run = run, None
            if finished is not None:
                out.append(finished.xml())
        elif name != "m:t" or run is None:  # a run's <m:t> is written by the run
            emit("</%s>" % name)

    pos = 0
    for m in _TAG.finditer(block):
        text(block[pos:m.start()])
        pos = m.end()
        closing, raw_name, raw_attrs = m.group(1), m.group(2), m.group(3)
        if not raw_name.lower().startswith("m:"):
            if raw_name.lower() == "br" and run is not None:
                run.line_break = True
            continue  # HTML formatting
        name = _omml_name(raw_name)
        if closing:
            if name in stack:
                while stack:
                    top = stack[-1]
                    close_top()
                    if top == name:
                        break
            continue  # (an end tag without its start tag is dropped)
        attrs, empty = _attributes(raw_attrs)
        if run is not None and stack[-1] == "m:r" and name not in ("m:rPr", "m:t"):
            close_top()  # a structure inside a run is not OMML: the run ended before it (</m:r> missing)
        if name == "m:r":
            if not empty:
                while run is not None:  # a run inside a run: the open one ends here
                    close_top()
                run = _Run("<m:r%s>" % attrs)
                stack.append(name)
            continue
        if name == "m:t" and run is not None:
            if not empty:
                stack.append(name)  # its text goes into the run's text
            continue
        emit("<%s%s/>" % (name, attrs) if empty else "<%s%s>" % (name, attrs))
        if not empty:
            stack.append(name)
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
    else:  # a start marker without the end one: what follows it
        start = re.search(r"<!--\s*StartFragment\s*-->", text, re.I)
        text = text[start.end():] if start else text
    blocks = [clean_block(b) for b in _BLOCK.findall(text)]
    blocks = [b for b in blocks if re.search(r"<m:oMath\b", b)]
    return "\n".join(blocks) if blocks else None
