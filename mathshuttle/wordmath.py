"""The accepted and rejected views of Word maths, without right-margin numbers."""
from __future__ import annotations

import re
import xml.etree.ElementTree as ET

from .pandoc_engine import OMML_NS, WML_NS, _separator

M = "{%s}" % OMML_NS
W = "{%s}" % WML_NS
INSERTED = (W + "ins", W + "moveTo")
DELETED = (W + "del", W + "moveFrom")
REVISIONS = INSERTED + DELETED


def empty(view):
    return not any(c.tag.startswith(M) and not c.tag.endswith("Pr") for m in view.iter(M + "oMath") for c in m)


def revision_view(el, accept=True):
    """Copy the selected revision content into ordinary math runs for texmath."""
    dropped = DELETED if accept else INSERTED

    def copy(node):
        if node.tag in dropped:
            return []
        new = ET.Element(node.tag, node.attrib)
        new.text, new.tail = node.text, node.tail
        for child in node:
            new.extend(copy(child))
        if node.tag in REVISIONS:
            return list(new)
        if node.tag == M + "r" and new.find(M + "t") is not None and not any(t.text for t in new.iter(M + "t")):
            return []
        # When Word deletes a whole fraction, script or array its empty shell stays.
        if (node.tag.startswith(M) and node.tag not in (M + "oMath", M + "oMathPara")
                and new.find(".//" + M + "t") is None and node.find(".//" + M + "t") is not None):
            return []
        return [new]

    copied = copy(el)
    return copied[0] if copied else ET.Element(el.tag)


def strip_numbers(math):
    """Remove trailing number marks from equation-array rows, including marks split across runs."""
    numbers = []
    for formula in math.iter(M + "oMath"):
        for array in formula.findall(M + "eqArr"):
            rows = array.findall(M + "e")
            marked = False
            for row in rows:
                texts = []
                for run in reversed(row):
                    if run.tag != M + "r":
                        break
                    texts[:0] = list(run.iter(M + "t"))
                value = "".join(t.text or "" for t in texts)
                mark = re.search(r"#(\([^#]*\))$", value)
                if not mark and array.find(M + "eqArrPr/" + M + "maxDist") is not None:
                    # A starred LaTeX tag is printed without parentheses.
                    mark = re.search(r"#([^#]+)$", value)
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
                formula[index:index] = [c for c in rows[0] if c.findtext(M + "t") != "&"]
            elif not any("&" in (t.text or "") for row in rows for t in row.findall(M + "r/" + M + "t")):
                for row in rows:
                    row.insert(0, _separator())
    return numbers
