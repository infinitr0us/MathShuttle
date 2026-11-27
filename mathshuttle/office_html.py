"""The equations inside the HTML that Word copies (macOS ``public.html``, Windows ``HTML Format``).

Word copies a selection as HTML too, with each equation as OMML inside a conditional comment::

    <!--[if gte msEquation 12]><m:oMathPara><m:oMath><m:sSup>...<i style='...'><span lang=EN-CA
    style='...'><m:r>x</m:r></span></i>...</m:oMath></m:oMathPara><![endif]--><![if !msEquation]>
    <img ...><![endif]>

That OMML is not well-formed as it stands: HTML formatting tags (``<span>``, ``<i>``, ``<br>``) sit
between the OMML elements, a run's text is not wrapped in ``<m:t>``, and the text is HTML (entities,
line breaks from wrapping).  :func:`omml_from_html` turns it back into OMML that
:func:`.pandoc_engine.read_omml` reads.

This is the fallback for what Word copies without MathML: a selection of text with equations in it,
and (Mac Word 16.115) a math paragraph of several aligned lines, for which it announces MathML but
delivers none.  The formatting the HTML tags carried (font, colour) is not needed for the LaTeX; the
OMML's own run properties (``m:sty``, ``m:scr``, ``m:aln``) are kept.
"""
from __future__ import annotations

import html
import re
from typing import List, Optional

_BLOCK = re.compile(r"<!--\[if gte msEquation 12\]>(.*?)<!\[endif\]-->", re.S | re.I)
# a start, end or empty tag; quoted attribute values may hold ">"
_TAG = re.compile(r"<(/?)([A-Za-z][\w:.-]*)((?:[^>\"']|\"[^\"]*\"|'[^']*')*)>", re.S)
_RUN_TEXT = re.compile(r"(<m:r>(?:<m:rPr>.*?</m:rPr>)?)([^<]+)(</m:r>)", re.S)


def _text(raw: str) -> str:
    """HTML text -> the characters, XML-escaped; a line break from the HTML's wrapping is a space."""
    text = re.sub(r"[ \t]*\r?\n[ \t\r\n]*", " ", raw)
    return html.escape(html.unescape(text), quote=False)


def clean_block(block: str) -> str:
    """One conditional-comment block of Word's HTML -> OMML (``m:`` prefixes, namespace undeclared)."""
    pieces: List[str] = []
    pos = 0
    for m in _TAG.finditer(block):
        pieces.append(_text(block[pos:m.start()]))
        if m.group(2).lower().startswith("m:"):
            pieces.append(m.group(0))
        pos = m.end()
    pieces.append(_text(block[pos:]))
    omml = "".join(pieces)
    # a run left with only (ASCII) white space held a <br> or the HTML's line wrapping (a run with
    # properties, e.g. an alignment mark, stays)
    omml = re.sub(r"<m:r>[ \t\r\n]*</m:r>", "", omml)
    return _RUN_TEXT.sub(lambda m: '%s<m:t xml:space="preserve">%s</m:t>%s' % m.groups(), omml)


def omml_from_html(text: Optional[str]) -> Optional[str]:
    """The OMML of every equation in Word's HTML, one fragment after the other; None if there is none."""
    if not text:
        return None
    blocks = [clean_block(b) for b in _BLOCK.findall(text)]
    blocks = [b for b in blocks if re.search(r"<m:oMath\b", b)]
    return "\n".join(blocks) if blocks else None
