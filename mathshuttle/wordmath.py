"""The accepted and rejected views of Word maths, without right-margin numbers."""
from __future__ import annotations

import re
import xml.etree.ElementTree as ET

from .pandoc_engine import OMML_NS, WML_NS

M = "{%s}" % OMML_NS
W = "{%s}" % WML_NS
INSERTED = (W + "ins", W + "moveTo")
DELETED = (W + "del", W + "moveFrom")
REVISIONS = INSERTED + DELETED


# structures that show something with no text in them: delimiters ({ } of an empty set), a radical...
_VISIBLE = frozenset(M + t for t in ("d", "nary", "rad", "acc", "bar", "groupChr", "borderBox", "f"))


def empty(view):
    return (not any((t.text or "").strip() for t in view.iter(M + "t"))
            and not any(e.tag in _VISIBLE for e in view.iter()))


# Operator names texmath reads from an upright run: two of them side by side are two names (\log\log).
_NAMES = frozenset("arccos arcsin arctan arg cos cosh cot coth csc deg det dim exp gcd hom inf ker lg lim "
                   "liminf limsup ln log max min mod Pr sec sin sinh sup tan tanh".split())


def _run_style(run):
    """Only join upright and plain-text runs, where Word's splitting breaks a name (\\sin as s + in).
    Italic, bold and script runs stay as they are: \\boldsymbol{\\alpha}\\boldsymbol{\\beta} is two symbols.
    Breaks and alignment points have their own positions."""
    if (run.tag != M + "r" or not len(run) or run[-1].tag != M + "t" or run[-1].text == "&"
            or any(p.tag not in (M + "rPr", W + "rPr") for p in run[:-1])
            or any(e.tag in REVISIONS + (M + "brk", M + "aln") or e.tag.endswith("PrChange") for e in run.iter())):
        return None
    props = run.find(M + "rPr")
    sty = props.find(M + "sty") if props is not None else None
    if props is None or props.find(M + "scr") is not None or not (
            props.find(M + "nor") is not None or (sty is not None and sty.get(M + "val") == "p")):
        return None
    return (run.attrib, run[-1].attrib, tuple(ET.tostring(p) for p in run[:-1]))


def revision_view(el, accept=True):
    """Copy the selected revision content into ordinary math runs for texmath."""
    dropped = DELETED if accept else INSERTED

    def copy(node, hidden=False):
        hidden = hidden or node.tag in dropped
        if hidden and node.tag in (M + "r", M + "t", W + "r", W + "t", W + "delText"):
            return []
        new = ET.Element(node.tag, node.attrib)
        new.text, new.tail = node.text, node.tail
        previous = None
        for child in node:
            children = copy(child, hidden)
            # the style of the run as it is in this view: a tracked "h" typed after "sin" joins it
            style = _run_style(children[0]) if len(children) == 1 else None
            if (style is not None and style == previous
                    and not (new[-1][-1].text in _NAMES and children[0][-1].text in _NAMES)):
                new[-1][-1].text = (new[-1][-1].text or "") + (children[0][-1].text or "")
            else:
                new.extend(children)
            if children:  # a run this view leaves out does not separate its neighbours
                previous = style
        if node.tag in REVISIONS:
            return list(new)
        if (node.tag == M + "r" and not any(t.text for t in new.iter(M + "t"))
                and node.find(".//" + M + "t") is not None):
            return []
        if (node.tag.startswith(M) and not any((t.text or "").strip() for t in new.iter(M + "t"))
                and any(c.tag in dropped for c in node.iterfind("%sPr/%sctrlPr/*" % (node.tag, M)))):
            # A structure deleted (or inserted) as a whole: Word marks it in its own properties, and
            # accepting (or rejecting) the change removes it, empty slots and all.
            return []
        return [new]

    copied = copy(el)
    return copied[0] if copied else ET.Element(el.tag)


_MARKERS = frozenset(W + t for t in ("bookmarkStart", "bookmarkEnd", "commentRangeStart", "commentRangeEnd",
                                     "proofErr", "permStart", "permEnd"))


def strip_numbers(math, expected=()):
    """Remove trailing number marks from equation-array rows, including marks split across runs."""
    numbers = []
    for formula in math.iter(M + "oMath"):
        for array in formula.findall(M + "eqArr"):
            if array.find(M + "eqArrPr/" + M + "maxDist") is None:
                continue
            rows = array.findall(M + "e")
            marked = False
            for row in rows:
                texts = []
                for run in reversed(row):
                    if run.tag in _MARKERS or (run.tag == W + "r" and run.find(W + "commentReference") is not None):
                        continue  # a comment or bookmark that ends inside the equation
                    if run.tag != M + "r":
                        break
                    texts[:0] = list(run.iter(M + "t"))
                value = "".join(t.text or "" for t in texts)
                mark = re.search(r"#(\([A-Za-z0-9.'\u2032\u2033-]{1,32}\))\Z", value)
                if not mark:
                    # any other tag the export wrote, such as \tag{*}
                    mark = re.search(r"#(\([^#()]{1,32}\))\Z", value)
                    if mark and mark[1] not in expected:
                        mark = None
                if not mark:
                    mark = re.search(r"#([^#()]+)\Z", value)
                    if mark and mark[1] not in expected:
                        mark = None
                if not mark:
                    continue
                numbers.append(mark[1])
                marked = True
                remaining = mark.start()
                for t in texts:
                    old = t.text or ""
                    t.text = old[:remaining]
                    remaining = max(0, remaining - len(old))
                for run in row.findall(M + "r"):
                    if not any(t.text for t in run.iter(M + "t")):
                        row.remove(run)
            if not marked:
                continue
            if len(rows) == 1:
                index = list(formula).index(array)
                formula.remove(array)
                # the alignment points the export wrote (Word 365 adds its fonts, w:rPr, when it
                # saves); a literal \& has maths properties (m:rPr) and stays
                formula[index:index] = [c for c in rows[0] if not (
                    c.tag == M + "r" and c.find(M + "rPr") is None and c.findtext(M + "t") == "&"
                    and all(k.tag in (W + "rPr", M + "t") for k in c))]
    return numbers
