"""Compare a returned Word document with the equations exported from a LaTeX paper."""
from __future__ import annotations

import difflib
import io
import re
import zipfile
import xml.etree.ElementTree as ET
from collections import deque
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

from . import compat, documents, wordmarks, wordmath
from .pandoc_engine import MC_NS, ConversionError, split_math_paragraph
from .texsource import Original
from .wordmath import DELETED, INSERTED, M, REVISIONS, W


@dataclass(frozen=True)
class Revision:
    author: str
    date: str


@dataclass(frozen=True)
class Comment:
    author: str
    text: str


@dataclass
class CompareRow:
    status: str
    original: Optional[Original]
    returned: str = ""
    display: bool = False
    revisions: list[Revision] = field(default_factory=list)
    comments: list[Comment] = field(default_factory=list)
    after: Optional[int] = None  # preceding original in the returned document

    @property
    def tracked(self):
        return bool(self.revisions)


@dataclass
class CompareResult:
    returned_docx: Path
    paper_tex: Path
    rows: list[CompareRow]
    macros: Optional[compat.Macros] = None
    notes: list[str] = field(default_factory=list)


@dataclass
class _Returned:
    bookmark: Optional[int]
    display: bool
    revisions: list[Revision]
    comments: list[Comment]
    accepted: str = ""
    rejected: str = ""


def _pieces(math, at_boundary):
    """Word can put a new formula beside the fully deleted numbered array in one oMath."""
    for i, array in enumerate(math):
        if array.tag != M + "eqArr" or array.find(M + "eqArrPr/" + M + "ctrlPr/" + W + "del") is None:
            continue
        if any(t.text for t in wordmath.revision_view(array).iter(M + "t")):
            continue
        neighbours = list(math[:i]) + list(math[i + 1:])
        if not neighbours or any(e.tag in REVISIONS for n in neighbours for e in n.iter()):
            continue
        if not at_boundary or (len(neighbours) == 1 and neighbours[0].tag == M + "r"):
            # Retyping the selection leaves the replacement as a linear run without w:ins.
            old = ET.Element(M + "oMath")
            old.append(array)
            return [(math, old)]
        if any(n.tag != M + "r" for n in neighbours):
            # A separate structured equation typed at the paragraph boundary.
            parts = []
            for group in (math[:i], [array], math[i + 1:]):
                if group:
                    piece = ET.Element(M + "oMath")
                    piece.extend(group)
                    parts.append((piece, piece))
            return parts
    return [(math, math)]


def _revision(el):
    return Revision(el.get(W + "author", ""), el.get(W + "date", ""))


def _is_revision(el):
    return el.tag in REVISIONS or (el.tag.startswith(W) and el.tag.endswith("PrChange"))


def _read_returned(z, originals):
    if "word/document.xml" not in z.namelist():
        raise ConversionError("The returned file has no Word document part.")
    comments = {}
    for _, kind, target in documents._targets(z, "word/document.xml"):
        if kind.endswith("/comments") and target in z.namelist():
            for c in documents._parse(z.read(target)).iter(W + "comment"):
                paras = ["".join(t.text or "" for t in p.iter(W + "t")) for p in c.iter(W + "p")]
                comments[c.get(W + "id")] = Comment(c.get(W + "author", ""), "\n".join(paras))
    known = {o.n: o for o in originals}
    occurrences, fragments, requests = [], [], []

    def queue(math, accept, outer, display):
        if any(e.tag in (DELETED if accept else INSERTED) for e in outer):
            return None
        view = wordmath.revision_view(math, accept)
        wordmath.strip_numbers(view)
        if wordmath.empty(view):
            return None
        if display and view.tag == M + "oMath":
            para = ET.Element(M + "oMathPara")
            para.append(view)
            view = para
        fragments.append(view)
        return len(fragments) - 1

    for part in wordmarks.PARTS:
        if part not in z.namelist():
            continue
        bookmarks, ranges = {}, {}

        def marker(el):
            ident = el.get(W + "id")
            if el.tag == W + "bookmarkStart":
                found = re.fullmatch(r"_MSEq(\d+)", el.get(W + "name", ""))
                if found:
                    bookmarks[ident] = int(found[1])
            elif el.tag == W + "bookmarkEnd":
                bookmarks.pop(ident, None)
            elif el.tag == W + "commentRangeStart":
                ranges[ident] = None
            elif el.tag == W + "commentRangeEnd":
                ranges.pop(ident, None)

        def record(math, old, display, outer, paragraph_revisions):
            book = next(reversed(bookmarks.values()), None)
            anchored = dict(ranges)
            for e in math.iter():
                marker(e)
                if e.tag in (W + "commentRangeStart", W + "commentReference"):
                    anchored[e.get(W + "id")] = None
                if book is None and bookmarks:
                    book = next(reversed(bookmarks.values()))
            revisions = list(dict.fromkeys(_revision(e) for e in (*outer, *math.iter()) if _is_revision(e)))
            for r in paragraph_revisions:
                if r not in revisions:
                    revisions.append(r)
            occurrences.append(_Returned(book, display, revisions, [comments[k] for k in anchored if k in comments]))
            mode = known[book].display if book in known else display
            accepted = queue(math, True, outer, mode)
            rejected = queue(old, False, outer, mode) if revisions or old is not math else accepted
            requests.append((accepted, rejected))

        def walk(el, display=False, outer=(), paragraph_revisions=(), enclosing_books=()):
            marker(el)
            if el.tag == "{%s}AlternateContent" % MC_NS:
                branch = next((b for b in el if b.tag == "{%s}Choice" % MC_NS), next(iter(el), None))
                if branch is not None:
                    walk(branch, display, outer, paragraph_revisions, enclosing_books)
                return
            if el.tag in REVISIONS:
                outer = (*outer, el)
            if el.tag == W + "p":
                enclosing_books = tuple(bookmarks.values())
                props = el.find(W + "pPr/" + W + "rPr")
                paragraph_revisions = tuple(_revision(r) for r in props if _is_revision(r)) if props is not None else ()
            if el.tag == M + "oMathPara":
                display = True
                # Word's aligned Shift+Enter lines form one equation.
                if len(el.findall(M + "oMath")) > 1 and el.find(".//" + M + "aln") is not None:
                    pieces = split_math_paragraph(el)
                    if len(pieces) == 1:
                        record(pieces[0], pieces[0], display, outer, paragraph_revisions)
                        return
            if el.tag == M + "oMath":
                book = next(reversed(bookmarks.values()), None)
                for math, old in _pieces(el, book is None or book in enclosing_books):
                    record(math, old, display, outer, paragraph_revisions)
                return
            for child in el:
                walk(child, display, outer, paragraph_revisions, enclosing_books)

        walk(documents._parse(z.read(part)))
    values = wordmarks.read_maths(fragments)
    if any(tex is None for tex in values):
        raise ConversionError("An equation in the returned Word document could not be read.")
    for eq, (a, b) in zip(occurrences, requests):
        eq.accepted = values[a] if a is not None else ""
        eq.rejected = values[b] if b is not None else ""
    return occurrences


def _ratio(a, b):
    return difflib.SequenceMatcher(None, a, b, autojunk=False).ratio()


def _match(originals, returned):
    """Match bookmark ranges first, then sequence anchors and similar equations in the gaps."""
    keys = [wordmarks.key(e.rejected) for e in returned]
    groups = {}
    for j, eq in enumerate(returned):
        if eq.bookmark is not None:
            groups.setdefault(eq.bookmark, []).append(j)
    pairs = {}
    for i, original in enumerate(originals):
        group = groups.get(original.n, [])
        choices = group if len(group) == 1 else [j for j in group if keys[j] or not returned[j].accepted]
        if choices:
            best = max(choices, key=lambda j: _ratio(original.key, keys[j]))
            pairs[best] = i
    used = set(pairs.values())
    a = [i for i in range(len(originals)) if i not in used]
    b = [j for j, e in enumerate(returned) if e.bookmark is None and keys[j]]
    matcher = difflib.SequenceMatcher(None, [originals[i].key for i in a], [keys[j] for j in b], autojunk=False)
    for tag, i1, i2, j1, j2 in matcher.get_opcodes():
        if tag == "equal":
            pairs.update(zip(b[j1:j2], a[i1:i2]))
        elif tag == "replace":
            left, right = a[i1:i2], b[j1:j2]
            if len(left) == len(right):
                pairs.update((j, i) for i, j in zip(left, right) if _ratio(originals[i].key, keys[j]) >= 0.5)
            else:
                # Use the best remaining pairing, retaining order for repeated, equally similar maths.
                candidates = sorted(((-_ratio(originals[i].key, keys[j]), abs(ii - jj), i, j)
                                     for ii, i in enumerate(left) for jj, j in enumerate(right)))
                taken = set()
                for negative, _, i, j in candidates:
                    if negative <= -0.5 and i not in taken and j not in pairs:
                        pairs[j] = i
                        taken.add(i)
    # A moved formula without its bookmark can lie on opposite sides of a sequence anchor.
    unused = set(range(len(originals))) - set(pairs.values())
    for j in b:
        if j not in pairs:
            i = next((i for i in sorted(unused) if keys[j] == originals[i].key), None)
            if i is not None:
                pairs[j] = i
                unused.remove(i)
    return pairs


def compare(returned_docx, paper_tex, macros=None) -> CompareResult:
    """Compare accepted Word equations to their export, retaining comments and revision authors."""
    from .wordexport import document_to_word

    paper, docx = Path(paper_tex).resolve(), Path(returned_docx).resolve()
    if not paper.is_file():
        raise ConversionError("Cannot read %s: file not found." % paper)
    try:
        with zipfile.ZipFile(docx) as z:
            originals = wordmarks.manifest(z)
            if originals is None:
                exported = io.BytesIO()
                document_to_word(paper, exported, macros)
                with zipfile.ZipFile(exported) as baseline:
                    originals = wordmarks.manifest(baseline)
            returned = _read_returned(z, originals)
    except ConversionError:
        raise
    except (OSError, KeyError, ValueError, ET.ParseError, zipfile.BadZipFile) as exc:
        raise ConversionError("Could not compare %s: %s" % (docx.name, exc)) from exc
    pairs = _match(originals, returned)
    missing = deque(sorted(set(range(len(originals))) - set(pairs.values())))
    rows, after = [], None
    for j, eq in enumerate(returned):
        original = originals[pairs[j]] if j in pairs else None
        if original:
            while missing and missing[0] < pairs[j]:
                absent = originals[missing.popleft()]
                rows.append(CompareRow("deleted", absent, display=absent.display))
            status = "deleted" if not eq.accepted else (
                "unchanged" if wordmarks.key(eq.accepted) == original.key else "changed")
        elif eq.accepted:
            status = "added"
        else:
            continue
        rows.append(CompareRow(status, original, eq.accepted, eq.display, eq.revisions, eq.comments, after))
        if original:
            after = original.n
    rows.extend(CompareRow("deleted", originals[i], display=originals[i].display) for i in missing)
    return CompareResult(docx, paper, rows, macros)


def write_corrected(result, out_path):
    """Write a separate corrected paper and unified diff. Return any unapplied edits."""
    from .texsource import corrected
    result.notes = corrected(result, Path(out_path))
    return result.notes
