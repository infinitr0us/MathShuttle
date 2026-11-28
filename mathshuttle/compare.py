"""Compare a returned Word document with the equations exported from a LaTeX paper."""
from __future__ import annotations

import difflib
import io
import re
import zipfile
import xml.etree.ElementTree as ET
from collections import Counter, deque
from dataclasses import dataclass, field
from itertools import count
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
    note: str = ""

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
    originals: list[Original] = field(default_factory=list)


@dataclass
class _Returned:
    bookmark: Optional[int]
    display: bool
    revisions: list[Revision]
    comments: list[Comment]
    accepted: str = ""
    rejected: str = ""
    unreadable: bool = False
    move: str = ""
    picture: bool = False
    conflicting_books: tuple = ()


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
        if not at_boundary:
            # Word keeps a retyped selection inside its original bookmark.
            old = ET.Element(M + "oMath")
            old.append(array)
            new = ET.Element(M + "oMath")
            new.extend(neighbours)
            return [(new, old)]
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

    def queue(math, accept, outer, display, numbers):
        if any(e.tag in (DELETED if accept else INSERTED) for e in outer):
            return None
        view = wordmath.revision_view(math, accept)
        wordmath.strip_numbers(view, numbers)
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

        def record(math, old, display, outer, source):
            seen_books = set(bookmarks.values())
            anchored = dict(ranges)
            for e in source.iter():
                marker(e)
                if e.tag in (W + "commentRangeStart", W + "commentReference"):
                    anchored[e.get(W + "id")] = None
                if e.tag == W + "bookmarkStart":
                    seen_books.update(bookmarks.values())
            book = next(iter(seen_books)) if len(seen_books) == 1 else None
            elements = (*outer, *source.iter())
            revisions = list(dict.fromkeys(_revision(e) for e in elements if _is_revision(e)))
            eq = _Returned(book, display, revisions, [comments[k] for k in anchored if k in comments])
            if len(seen_books) > 1:
                eq.conflicting_books = tuple(seen_books)
            eq.move = next((e.tag for e in elements if e.tag in (W + "moveFrom", W + "moveTo")), "")
            original = known.get(book)
            if math.tag == W + "drawing":
                if not original or not original.picture:
                    return
                eq.picture = True
                eq.accepted = "" if any(e.tag in DELETED for e in outer) else original.body
                occurrences.append(eq)
                requests.append((None, None))
                return
            occurrences.append(eq)
            mode = original.display if original else display
            numbers = original.numbers if original else ()
            accepted = queue(math, True, outer, mode, numbers)
            rejected = queue(old, False, outer, mode, numbers) if revisions or old is not math else accepted
            requests.append((accepted, rejected))

        def walk(el, display=False, outer=(), enclosing_books=()):
            marker(el)
            if el.tag == "{%s}AlternateContent" % MC_NS:
                branch = next((b for b in el if b.tag == "{%s}Choice" % MC_NS), next(iter(el), None))
                if branch is not None:
                    walk(branch, display, outer, enclosing_books)
                return
            if el.tag in REVISIONS:
                outer = (*outer, el)
            if el.tag == W + "p":
                enclosing_books = tuple(bookmarks.values())
            if el.tag == W + "drawing" and any(known[n].picture for n in bookmarks.values() if n in known):
                record(el, el, display, outer, el)
                return
            if el.tag == M + "oMathPara":
                display = True
                # Word's aligned Shift+Enter lines form one equation.
                if len(el.findall(M + "oMath")) > 1 and el.find(".//" + M + "aln") is not None:
                    pieces = split_math_paragraph(el)
                    if len(pieces) == 1:
                        record(pieces[0], pieces[0], display, outer, el)
                        return
            if el.tag == M + "oMath":
                book = next(reversed(bookmarks.values()), None)
                for math, old in _pieces(el, book is None or book in enclosing_books):
                    record(math, old, display, outer, el if old is not math else math)
                return
            for child in el:
                walk(child, display, outer, enclosing_books)

        walk(documents._parse(z.read(part)))
    values = wordmarks.read_maths(fragments)
    for eq, (a, b) in zip(occurrences, requests):
        if not eq.picture:
            eq.unreadable = any(i is not None and values[i] is None for i in (a, b))
            eq.accepted = (values[a] or "").strip() if a is not None else ""
            eq.rejected = (values[b] or "").strip() if b is not None else ""
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
    unplaced = {j for j, eq in enumerate(returned) if eq.conflicting_books}
    conflicting = {n for j in unplaced for n in returned[j].conflicting_books}
    uncertain = {i for i, eq in enumerate(originals) if eq.n in conflicting}
    pairs = {}
    for i, original in enumerate(originals):
        group = [j for j in groups.get(original.n, []) if returned[j].picture == original.picture
                 and returned[j].move != W + "moveTo"]
        choices = group if len(group) == 1 else [j for j in group if keys[j] or not returned[j].accepted]
        if choices:
            scores = {j: _ratio(original.key, keys[j]) for j in choices}
            best = max(scores, key=scores.get)
            if sum(v == scores[best] for v in scores.values()) == 1:
                pairs[best] = i
            else:
                uncertain.add(i)
                unplaced.update(choices)
    used = set(pairs.values())
    anchors = [(-1, -1), *sorted(pairs.items()), (len(returned), len(originals))]
    # Crossed bookmarks still identify their equations. Only align the gaps whose bounds agree.
    upper = len(originals)
    ceilings = []
    for _, i in reversed(anchors[1:]):
        upper = min(i, upper)
        ceilings.append(upper)
    lower = -1
    for (start, i), (end, _), upper in zip(anchors, anchors[1:], reversed(ceilings)):
        lower = max(lower, i)
        a = [i for i in range(lower + 1, upper) if i not in used and i not in uncertain and not originals[i].picture]
        b = [j for j in range(start + 1, end) if keys[j] and j not in unplaced and not returned[j].picture]
        _align_gap(originals, keys, a, b, pairs, uncertain, unplaced)
        if any(returned[j].unreadable for j in range(start + 1, end)):
            uncertain.update(set(a) - set(pairs.values()))
    return pairs, uncertain, unplaced


def _align_gap(originals, keys, a, b, pairs, uncertain, unplaced):
    before, after = [originals[i].key for i in a], [keys[j] for j in b]
    counts = Counter(after)
    doubtful = {value for value, count in Counter(before).items()
                if count > 1 and counts[value] != count and any(_ratio(value, key) >= 0.5 for key in after)}
    if doubtful:
        # A repeated equation (x = 1 twice) added, deleted or changed once: which occurrence it was is
        # not known. Only those are left undecided; the others are still aligned.
        known = set(before) - doubtful
        out_a = {i for i in a if originals[i].key in doubtful}
        out_b = {j for j in b if keys[j] in doubtful
                 or (keys[j] not in known and any(_ratio(value, keys[j]) >= 0.5 for value in doubtful))}
        uncertain.update(out_a)
        unplaced.update(out_b)
        a = [i for i in a if i not in out_a]
        b = [j for j in b if j not in out_b]
        before, after = [originals[i].key for i in a], [keys[j] for j in b]
    matcher = difflib.SequenceMatcher(None, before, after, autojunk=False)
    for tag, i1, i2, j1, j2 in matcher.get_opcodes():
        if tag == "equal":
            pairs.update(zip(b[j1:j2], a[i1:i2]))
        elif tag == "replace":
            left, right = a[i1:i2], b[j1:j2]
            scores = {(i, j): _ratio(originals[i].key, keys[j]) for i in left for j in right}
            for (i, j), score in scores.items():
                if score < 0.5:
                    continue
                if (all(score > scores[k, j] for k in left if k != i)
                        and all(score > scores[i, k] for k in right if k != j)):
                    pairs[j] = i
            uncertain.update(set(left) - set(pairs.values()))
            unplaced.update(set(right) - pairs.keys())


def _tracked_moves(originals, returned, pairs):
    """Only join a unique matching departure and arrival of the same formula."""
    departures, arrivals = {}, {}
    for j, eq in enumerate(returned):
        if eq.move == W + "moveFrom" and j in pairs and not eq.accepted:
            key = wordmarks.key(eq.rejected)
            if key and key == originals[pairs[j]].key:
                departures.setdefault(key, []).append(j)
        if eq.move == W + "moveTo" and j not in pairs and eq.accepted and not eq.rejected:
            arrivals.setdefault(wordmarks.key(eq.accepted), []).append(j)
    moved = {}
    for key, old in departures.items():
        new = arrivals.get(key, [])
        if len(old) == len(new) == 1:
            moved[new[0]] = old[0]
    return moved


def compare(returned_docx, paper_tex, macros=None) -> CompareResult:
    """Compare accepted Word equations to their export, retaining comments and revision authors."""
    from .wordexport import document_to_word

    paper, docx = Path(paper_tex).resolve(), Path(returned_docx).resolve()
    if not paper.is_file():
        raise ConversionError("Cannot read %s: file not found." % paper)
    try:
        with zipfile.ZipFile(docx) as z:
            originals = wordmarks.manifest(z, paper.name)
            if originals is None:
                exported = io.BytesIO()
                document_to_word(paper, exported, macros, pictures=False)
                with zipfile.ZipFile(exported) as baseline:
                    originals = wordmarks.manifest(baseline)
                pictures = wordmarks.picture_bookmarks(z)
                numbers = (n for n in count(1) if n not in pictures)
                for original in originals:
                    original.n = next(numbers)
            returned = _read_returned(z, originals)
    except ConversionError:
        raise
    except (OSError, KeyError, ValueError, ET.ParseError, zipfile.BadZipFile) as exc:
        raise ConversionError("Could not compare %s: %s" % (docx.name, exc)) from exc
    pairs, uncertain, unplaced = _match(originals, returned)
    moved = _tracked_moves(originals, returned, pairs)
    departures = set(moved.values())
    for arrival, departure in moved.items():
        pairs[arrival] = pairs[departure]
    missing = deque(sorted(set(range(len(originals))) - set(pairs.values())))
    rows, after = [], None
    location_note = "Cannot tell which occurrence this equation belongs to. Check it in the paper."

    def absent(i):
        eq = originals[i]
        if i in uncertain:
            return CompareRow("unmatched", eq, display=eq.display, note=location_note)
        return CompareRow("deleted", eq, display=eq.display)

    for j, eq in enumerate(returned):
        if j in departures:
            continue
        original = originals[pairs[j]] if j in pairs else None
        note = ""
        if original:
            while missing and missing[0] < pairs[j]:
                rows.append(absent(missing.popleft()))
        if eq.unreadable:
            status = "unreadable"
            note = "This equation could not be read. Check it in Word."
        elif j in unplaced:
            status, note = "unmatched", location_note
        elif j in moved:
            status = "moved"
            previous = next((o for o in originals if o.n == after), None)
            place = previous.numbers[-1] if previous and previous.numbers else "(%d)" % after if after else ""
            note = "moved to after " + place if place else "moved to the start"
        elif original:
            if eq.picture and eq.accepted:
                if not eq.comments:
                    after = original.n
                    continue
                status = "commented"
            else:
                status = "deleted" if not eq.accepted else (
                    "unchanged" if wordmarks.key(eq.accepted) == original.key else "changed")
        elif eq.accepted:
            status = "added"
        else:
            continue
        if status == "changed" and original.macro:
            note = "Equation %d was made by the macro %s: change the macro." % (original.n, original.macro)
        rows.append(CompareRow(status, original, eq.accepted, eq.display, eq.revisions, eq.comments, after, note))
        if original and eq.accepted:
            after = original.n
    rows.extend(absent(i) for i in missing)
    return CompareResult(docx, paper, rows, macros, originals=originals)


def write_corrected(result, out_path):
    """Write a separate corrected paper and unified diff. Return any unapplied edits."""
    from .texsource import corrected
    result.notes = corrected(result, Path(out_path))
    return result.notes
