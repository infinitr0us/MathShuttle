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


def empty(view):
    return not any((t.text or "").strip() for t in view.iter(M + "t"))


def _run_style(run):
    """Only join ordinary text runs. Breaks and alignment points have their own positions."""
    if (run.tag != M + "r" or not len(run) or run[-1].tag != M + "t" or run[-1].text == "&"
            or any(p.tag not in (M + "rPr", W + "rPr") for p in run[:-1])
            or any(e.tag in REVISIONS + (M + "brk", M + "aln") or e.tag.endswith("PrChange") for e in run.iter())):
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
            style = _run_style(child)
            children = copy(child, hidden)
            if style is not None and style == previous and children:
                new[-1][-1].text = (new[-1][-1].text or "") + (children[0][-1].text or "")
            else:
                new.extend(children)
            previous = style if children else None
        if node.tag in REVISIONS:
            return list(new)
        if (node.tag == M + "r" and not any(t.text for t in new.iter(M + "t"))
                and node.find(".//" + M + "t") is not None):
            return []
        return [new]

    copied = copy(el)
    return copied[0] if copied else ET.Element(el.tag)


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
                    if run.tag != M + "r":
                        break
                    texts[:0] = list(run.iter(M + "t"))
                value = "".join(t.text or "" for t in texts)
                mark = re.search(r"#(\([A-Za-z0-9.'\u2032\u2033-]{1,32}\))\Z", value)
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
                formula[index:index] = [c for c in rows[0] if not (
                    c.tag == M + "r" and not c.attrib and len(c) == 1 and not c[0].attrib
                    and c.findtext(M + "t") == "&")]
    return numbers
