"""Find pandoc's equations in the paper and the local files it reads."""
from __future__ import annotations

import codecs
import difflib
import hashlib
import os
import re
import tempfile
from collections import defaultdict
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path

from . import compat, documents, latex_wrap
from .pandoc_engine import ConversionError, _LATEX_TOKEN


@dataclass
class Original:
    n: int
    tex: str
    display: bool
    section: str = ""
    file: str = ""
    occurrence: int = 0
    count: int = 0
    labels: list[str] = field(default_factory=list)
    numbers: list[str] = field(default_factory=list)
    key: str = ""
    picture: bool = False
    digest: str = ""
    before: str = ""
    after: str = ""
    macro: str = ""
    unique_context: bool = True

    @property
    def body(self):
        tex = self.tex.strip()
        for opening, closing, _ in _DELIMITERS:
            if tex.startswith(opening) and tex.endswith(closing):
                return tex[len(opening):-len(closing)].strip()
        return tex

    @property
    def number(self):
        return ", ".join(self.numbers)


_TOKEN = re.compile(r"\\[a-zA-Z@]+|\\.|%[^\r\n]*|[^\s]", re.S)
_DELIMITERS = [("$$", "$$", "ddollar"), (r"\[", r"\]", "bracket"),
               ("$", "$", "dollar"), (r"\(", r"\)", "paren")]


def _masked(text):
    """Blank comments, macro definitions and verbatim examples, preserving offsets."""
    plain = compat.without_verbatim(text)
    spans = [m.span() for m in _TOKEN.finditer(plain) if m[0].startswith("%")]
    spans += [d.span for d in compat.parse_definitions(plain)[0]]
    chars = list(plain)
    for a, b in spans:
        chars[a:b] = re.sub(r"[^\r\n]", " ", plain[a:b])
    return "".join(chars)


def _dollar_spans(text):
    """Pair unescaped dollars at the same brace depth, so prose between two formulas never matches."""
    spans, starts = {}, {}
    depth = skip = 0
    for m in _LATEX_TOKEN.finditer(text):
        if m.start() < skip:
            continue
        if m[0] == "$":
            if depth in starts:
                start, width = starts.pop(depth)
            else:
                width = 2 if text.startswith("$$", m.start()) else 1
                starts[depth] = (m.start(), width)
                skip = m.start() + width
                continue
            skip = m.start() + width
            spans[start] = skip
        elif m[0] == "{":
            depth += 1
        elif m[0] == "}":
            depth -= 1
    return spans


@lru_cache(maxsize=512)
def _pattern(tex, display=None):
    """Locate known TeX tokens, tolerating spacing but keeping control words whole."""
    tokens = [m[0] for m in _TOKEN.finditer(tex) if not m[0].startswith("%")]
    body = r"\s*".join(re.escape(t) + (r"(?![A-Za-z@])" if re.fullmatch(r"\\[A-Za-z@]+", t) else "")
                       for t in tokens)
    if display is not None:
        wrapped = [re.escape(a) + r"\s*" + body + r"\s*" + re.escape(b)
                   for a, b, kind in _DELIMITERS if display == (kind in ("bracket", "ddollar"))]
        env = "displaymath" if display else "math"
        wrapped.append(r"\\begin\s*\{" + env + r"\}\s*" + body + r"\s*\\end\s*\{" + env + r"\}")
        if tex.lstrip().startswith(r"\begin"):
            wrapped.append(body)
        body = "(?:" + "|".join(wrapped) + ")"
    return re.compile(body)


def _same_source(tex):
    """Whitespace may vary between math tokens. Keep comment text and spaces in text arguments."""
    words = tuple(" ".join(m[0].split()) if m[0].startswith("%") else m[0] for m in _TOKEN.finditer(tex))
    return words, tuple(" ".join(tex[a:b].split()) for a, b in compat._text_spans(tex))


class SourceTree:
    """Local inputs in reading order, with paths and character positions in each original."""

    def __init__(self, paper):
        self.paper = Path(paper).resolve()
        self.folder = self.paper.parent
        self.text = {}
        self.encoding = {}
        self.digests = {}
        self.mask = {}
        self.dollars = {}
        self._matches = {}
        self.inputs = {}
        self.segments = []
        self._load(self.paper, ())
        self._segments(self.paper, ())
        self.cursor = (0, 0)
        self.claimed = set()

    def _load(self, path, active):
        if path in self.text or len(self.text) >= 300:
            return
        data = path.read_bytes()
        self.digests[path] = hashlib.sha256(data).hexdigest()
        text = documents._source_text(data)
        if data.startswith(codecs.BOM_UTF8):
            encoding = "utf-8-sig"
        elif data.startswith((codecs.BOM_UTF16_LE, codecs.BOM_UTF16_BE)):
            encoding = "utf-16-le" if data.startswith(codecs.BOM_UTF16_LE) else "utf-16-be"
        else:
            encoding = "utf-8" if text.encode("utf-8") == data else "latin-1"
        self.encoding[path] = encoding
        self.text[path] = text
        self.mask[path] = plain = _masked(text)
        self.dollars[path] = _dollar_spans(plain)
        self.inputs[path] = links = []
        for m in documents._READS.finditer(plain):
            if m[1] not in ("input", "include", "subfile"):
                continue
            for name in (m[2].strip(), m[2].strip() + ".tex"):
                target = (self.folder / name).resolve()
                if target.is_file() and target not in active + (path,):
                    self._load(target, active + (path,))
                    if target in self.text:
                        links.append((m, target))
                    break

    def _segments(self, path, active):
        start = 0
        for m, child in self.inputs[path]:
            self.segments.append((path, start, m.start()))
            if child not in active and len(self.segments) < 20000:
                self._segments(child, active + (path,))
            start = m.end()
        self.segments.append((path, start, len(self.text[path])))

    def locate(self, original):
        """Find the original delimiters and record the occurrence in that file."""
        pattern = _pattern(original.tex, original.display)
        for i in range(self.cursor[0], len(self.segments)):
            path, start, end = self.segments[i]
            if i == self.cursor[0]:
                start = max(start, self.cursor[1])
            span = next(((a, b) for a, b in self.spans(path, pattern) if start <= a and b <= end), None)
            if span is None:
                continue
            self.cursor = (i, span[1])
            break
        else:
            # Pandoc can emit the title and authors in a different order from the preamble.
            remaining = [(path, span) for path in self.text for span in self.spans(path, pattern)
                         if (path, span) not in self.claimed]
            if len(remaining) != 1:
                return
            path, span = remaining[0]
        self.claimed.add((path, span))
        self.record(original, path, span)

    def record(self, original, path, span):
        original.tex = self.text[path][span[0]:span[1]]
        original.file = Path(os.path.relpath(path, self.folder)).as_posix()
        matches = self.exact_spans(path, original.tex)
        original.count = len(matches)
        original.occurrence = matches.index(span) + 1
        original.digest = self.digests[path]
        original.before, original.after = self.context(path, span)
        original.unique_context = sum(self.context(path, m) == (original.before, original.after) for m in matches) == 1

    def context(self, path, span):
        a, b = span
        return self.text[path][max(0, a - 32):a], self.text[path][b:b + 32]

    def spans(self, path, pattern):
        key = path, pattern
        if key not in self._matches:
            text = self.mask[path]
            found, i = [], 0
            while i < len(text):
                m = pattern.search(text, i)
                if m is None:
                    break
                if text[m.start()] == "$" and self.dollars[path].get(m.start()) != m.end():
                    i = m.start() + 1
                    continue
                found.append(m.span())
                i = max(m.end(), m.start() + 1)
            self._matches[key] = found
        return self._matches[key]

    def exact_spans(self, path, tex):
        expected = _same_source(tex)
        return [(a, b) for a, b in self.spans(path, _pattern(tex)) if _same_source(self.text[path][a:b]) == expected]

    def position(self, original):
        """The recorded occurrence in the current paper, if it can still be identified."""
        path = (self.folder / original.file).resolve()
        if path in self.mask and original.occurrence > 0:
            matches = self.exact_spans(path, original.tex)
            if original.digest and original.digest != self.digests[path]:
                if not original.unique_context:
                    return None
                matching = [span for span in matches if self.context(path, span) == (original.before, original.after)]
                return (path, matching[0]) if len(matching) == 1 else None
            if not original.digest and len(matches) > 1:
                return None
            if len(matches) == original.count and len(matches) >= original.occurrence:
                return path, matches[original.occurrence - 1]
        return None


def _macros(tree, user, position=None, names=None):
    """Literal expansions of the paper's zero-argument macros, longest first."""
    source = tree.text[tree.paper]
    if position:
        path, offset = position
        prefixes = []
        for file, start, end in tree.segments:
            if file == path and start <= offset < end:
                prefixes.append(tree.text[file][start:offset])
                break
            prefixes.append(tree.text[file][start:end])
        source = "\n".join(prefixes)
    definitions = documents._local_sources(str(tree.paper), source)
    macros = compat.with_document(user, definitions)
    choices = []
    if macros:
        for name, definition in macros.definitions.items():
            if names is not None and name not in names:
                continue
            body = compat._simple_expansion(name, macros.needed(name))
            if body is None and not compat._takes_arguments(definition):
                # An explicit [0], an empty xparse signature, or a macro using an argument-taking helper.
                tail = re.sub(r"^\s*\}?\s*(?:\[\s*0\s*\]|\{\s*\})?\s*", "", compat._after_name(definition))
                if tail.startswith("{") and compat._group_end(tail, 0) == len(tail):
                    body, _ = compat.expand_macros(name, macros)
            if body and body != name:
                choices.append((body, name))
    return sorted(choices, key=lambda pair: -len(pair[0]))


def _protected(tex):
    spans = compat._text_spans(tex)
    for token in latex_wrap._TOKEN_RE.finditer(tex):
        if token.group("be"):
            end = token.end()
            if token.group("be") == "begin" and token.group("env") in ("array", "alignedat", "alignat", "alignat*"):
                _, end = compat._argument(tex, compat._optional(tex, end, 1))
            spans.append((token.start(), end))
        elif token[0] in (r"\text", r"\mathrm", r"\operatorname", r"\textrm", r"\mbox"):
            _, end = compat._argument(tex, token.end() + int(tex[token.end():].startswith("*")))
            spans.append((token.start(), end))
    return spans


def _original_macros(source, tex, macros):
    """Restore a macro only where its original expansion survived in the returned tokens."""
    by_name = {name: body for body, name in macros}
    old, uses = [], []
    protected = _protected(source)
    for token in _TOKEN.finditer(source):
        name = token[0]
        if name in by_name and not compat._in(protected, token.start()):
            expansion = _TOKEN.findall(by_name[name])
            uses.append((len(old), expansion, name))
            old.extend(expansion)
        else:
            old.append(name)
    if not uses:
        return tex
    tokens = list(_TOKEN.finditer(tex))
    new = [t[0] for t in tokens]
    blocks = difflib.SequenceMatcher(None, old, new, autojunk=False).get_matching_blocks()
    changes = []
    protected = _protected(tex)
    for index, expansion, name in uses:
        size = len(expansion)
        if not size or sum(old[i:i+size] == expansion for i in range(len(old))) != sum(
                new[i:i+size] == expansion for i in range(len(new))):
            continue
        for a, b, length in blocks:
            if a <= index and index + size <= a + length:
                start = tokens[b + index - a].start()
                end = tokens[b + index - a + size - 1].end()
                if not any(start < y and x < end for x, y in protected):
                    gap = " " if re.match(r"[A-Za-z@]", tex[end:end + 1]) and name[-1].isalpha() else ""
                    changes.append((start, end, name + gap))
                break
    for start, end, value in reversed(changes):
        tex = tex[:start] + value + tex[end:]
    return tex


def _restore_macros(tex, macros):
    tokens = list(_TOKEN.finditer(tex))
    ends = {m.end() for m in tokens}
    protected = _protected(tex)
    out, last = [], 0
    for token in tokens:
        start = token.start()
        if start < last or any(a <= start < b for a, b in protected):
            continue
        for body, name in macros:
            end = start + len(body)
            if end in ends and tex.startswith(body, start):
                out.extend((tex[last:start], name))
                if re.match(r"[a-zA-Z@]", tex[end:end + 1]) and name[-1].isalpha():
                    out.append(" ")
                last = end
                break
    return "".join(out) + tex[last:]


def _row_marks(row):
    from .wordexport import _LABEL, _NO_NUMBER, _tag, _without_comments
    row = _without_comments(row)
    tag = _tag(row)
    marks = [m[0] for m in _NO_NUMBER.finditer(row)]
    if tag:
        marks.append(row[tag[0]:tag[1]])
    return marks + [m[0] for m in _LABEL.finditer(row)]


_MARKS_ONLY = re.compile(r"\s*(?:\\(?:label|tag\*?|notag|nonumber)\b\s*(?:\{[^{}]*\})?\s*)+")


def _leading(row, marks):
    """The start of `row` when the row begins with all its marks (``\\label{a}`` before its maths), else None."""
    from .wordexport import _without_comments
    text = _without_comments(row).lstrip()
    at = 0
    for mark in marks:
        if not text.startswith(mark, at):
            return None
        at += len(mark)
        at += len(text[at:]) - len(text[at:].lstrip())
    return text[:at].replace("\r\n", "\n") if marks and text[at:].strip() else None


def _replacement(original, tex, macros, notes, numbers=()):
    tex = _original_macros(original.body, tex, macros)
    source = original.tex.strip()
    for opening, closing, wrapper in _DELIMITERS:
        if source.startswith(opening) and source.endswith(closing):
            body = _restore_rows(original, source[len(opening):-len(closing)], tex, notes, numbers)
            return latex_wrap.wrap(body, wrapper, original.display) if body is not None else None
    env = latex_wrap._ENV_RE.fullmatch(source)
    if not env:
        return None
    name, body = env["env"], env["body"]
    head = ""
    if name in latex_wrap.GATHER_ENVS + latex_wrap.MULTLINE_ENVS:
        # Pandoc represents these as a one-column matrix or an equation array.
        wrapper, new_body = latex_wrap.split_env(tex, ("matrix", "gathered", "aligned", "array"))
        if wrapper == "array":
            new_body = re.sub(r"^\s*\{\s*[clr]\s*\}", "", new_body, count=1)
        if any(m[0] == "&" for m in latex_wrap._top_level(new_body)):
            notes.append("Check the columns of equation %d. Left the source as it is." % original.n)
            return None
    elif name in latex_wrap.WRAPPER_KEYS:
        wrapped = latex_wrap.wrap(tex, name)
        new_body = latex_wrap._ENV_RE.fullmatch(wrapped)["body"].strip()
    elif name in latex_wrap.ALIGN_ENVS:
        new_body = latex_wrap.classify(tex)[1]
        if name.startswith("alignat"):
            arg = latex_wrap._ALIGNAT_ARG_RE.match(body)
            if arg:
                # `\begin{alignat}{2}` as the author wrote it, kept out of the matching of the rows
                head = arg[0].rstrip()
                body = body[arg.end():]
        elif name.startswith("eqnarray"):
            if re.search(r"\\begin\s*\{", new_body):
                # a matrix or cases inside (or Word's one-row matrix): its & are not eqnarray's
                notes.append("Check the columns of equation %d. Left the source as it is." % original.n)
                return None
            new_body = re.sub(r"(?<!\\)&\s*(\\[A-Za-z]+(?![A-Za-z]|\s*\{)|[=<>+\-])", r"&\1&", new_body)
            if any(len(re.findall(r"(?<!\\)&", row)) != 2 for row in re.split(r"\\\\", new_body) if row.strip()):
                # a relation with a note over it (\stackrel{(*)}{=}): eqnarray's columns cannot be told
                notes.append("Check the columns of equation %d. Left the source as it is." % original.n)
                return None
    else:
        new_body = latex_wrap.plain_form(tex)
    restored = _restore_rows(original, body, new_body, notes, numbers)
    if restored is None:
        return None
    restored = restored.strip()
    # indented as the author indented the original's body
    first = next((line for line in body.splitlines() if line.strip() and not _MARKS_ONLY.fullmatch(line)), "")
    indent = first[:len(first) - len(first.lstrip())]
    if indent:
        restored = "\n".join(indent + line if line.strip() and not line[:1].isspace() else line
                             for line in restored.splitlines())
    if name.startswith("alignat") and head:
        # a column pair added in Word: alignat{n} must count them (2n - 1 & at most per row)
        pairs = max((len(re.findall(r"(?<!\\)&", row)) + 2) // 2 for row in re.split(r"\\\\", restored))
        count = re.search(r"\d+", head)
        if count and pairs > int(count[0]):
            head = head[:count.start()] + str(pairs) + head[count.end():]
    # `\begin{equation}\label{a}` on one line stays on one line
    joined = (body[:1] and not body[:1].isspace()
              and restored.split("\n", 1)[0].strip() == body.split("\n", 1)[0].strip())
    return "\\begin{%s}%s%s%s\n\\end{%s}" % (name, head, "" if joined else "\n", restored, name)


def _row_numbers(original, marks):
    """The number of each row of the original (None for a \\notag row), when that can be told."""
    from .wordexport import _NO_NUMBER
    numbered = [not any(_NO_NUMBER.fullmatch(m) for m in row) for row in marks]
    if sum(numbered) != len(original.numbers):
        return []
    given = iter(original.numbers)
    return [next(given) if n else None for n in numbered]


def _restore_rows(original, body, new_body, notes, numbers=()):
    from .wordexport import _top_rows, _without_comments
    # Delimiters and equation environments can themselves enclose an aligned or split block.
    nested = None
    old = latex_wrap._ENV_RE.fullmatch(body.strip())
    if old and old["env"] in latex_wrap._INNER:
        body = old["body"]
        new = latex_wrap._ENV_RE.fullmatch(new_body.strip())
        if new and new["env"] in latex_wrap._INNER:
            nested, new_body = new["env"], new["body"]
    old_rows, rows = _top_rows(body)[::2], _top_rows(new_body)
    marks = [_row_marks(row) for row in old_rows]
    leads = [_leading(row, m) for row, m in zip(old_rows, marks)]
    if any(marks) and max(len(old_rows), len(rows[::2])) > 1:
        def row_key(row, marks=()):
            row = _without_comments(row)
            for mark in marks:
                row = row.replace(mark, "")
            # braces around one token left out: Word gives back U^2 as U^{2} (but 2^{n+1} is not 2^n+1)
            tokens = [t for t in _TOKEN.findall(row) if t != "&"]
            out, k = [], 0
            while k < len(tokens):
                if tokens[k] == "{" and k + 2 < len(tokens) and tokens[k + 2] == "}" and tokens[k + 1] not in "{}":
                    out.append(tokens[k + 1])
                    k += 3
                else:
                    out.append(tokens[k])
                    k += 1
            return tuple(out)

        old = [row_key(r, m) for r, m in zip(old_rows, marks)]
        new = [row_key(r) for r in rows[::2]]
        def score(i, j):
            return difflib.SequenceMatcher(None, old[i], new[j], autojunk=False).ratio()

        paired = {}
        mine = _row_numbers(original, marks)
        if numbers and len(numbers) == len(new) and mine:
            # The number Word shows at a row's right says which row it is (rows moved with their
            # numbers), when the row's content agrees: no other old row is closer to it. A co-author
            # who swapped the bodies and left the numbers moved the content, and the labels follow that.
            by_number = {j: mine.index(n) for j, n in enumerate(numbers)
                         if n is not None and mine.count(n) == 1 and list(numbers).count(n) == 1}
            if all(score(i, j) >= max(score(k, j) for k in range(len(old))) for j, i in by_number.items()):
                paired.update(by_number)
        for j, value in enumerate(new):
            if j not in paired and value not in [old[i] for i in paired.values()] \
                    and old.count(value) == new.count(value) == 1:
                paired[j] = old.index(value)
        left = [i for i in range(len(old)) if i not in paired.values()]
        right = [j for j in range(len(new)) if j not in paired]
        if len(left) == len(right) == 1:
            paired[right[0]] = left[0]
        elif len(right) == 1 and left:
            scores = sorted(((difflib.SequenceMatcher(None, old[i], new[right[0]], autojunk=False).ratio(), i)
                             for i in left), reverse=True)
            if scores[0][0] >= 0.5 and scores[0][0] > scores[1][0]:
                paired[right[0]] = scores[0][1]
        open_rows = [j for j in range(len(new)) if j not in paired]
        if len(old) == len(new) and open_rows and all(i == j for j, i in paired.items()):
            # As many rows as before and none moved: rows edited in place keep their own labels, when
            # each is closer to its own old row than to any other one (a swap with edits is not), or
            # the old rows are the same (then which is which does not matter).
            if all(score(j, j) >= 0.5 and all(score(i, j) < score(j, j) or old[i] == old[j]
                                              for i in open_rows if i != j) for j in open_rows):
                for j in open_rows:
                    paired[j] = j
        right = [j for j in range(len(new)) if j not in paired]
        left = [i for i in range(len(old)) if i not in paired.values()]
        if any(j not in paired for j in right) and any(marks[i] for i in left):
            notes.append("Cannot tell which rows of equation %d keep their labels. Left the source as it is."
                         % original.n)
            return None
        unused = [m for i, row_marks in enumerate(marks) if i not in paired.values() for m in row_marks]
        if unused:
            notes.append("Equation %d has fewer matching rows. Check %s." % (original.n, ", ".join(unused)))
        leads = [leads[paired[j]] if j in paired else None for j in range(len(new))]
        marks = [marks[paired[j]] if j in paired else [] for j in range(len(new))]
    for i, mark in enumerate(marks):
        if mark and i < len(leads) and leads[i]:
            # where the author put them: before the row's maths
            rows[2 * i] = leads[i] + rows[2 * i].lstrip()
        elif mark:
            rows[2 * i] = rows[2 * i].rstrip() + " " + " ".join(mark) + ("\n" if i * 2 + 1 < len(rows) else "")
    content = "".join(rows).strip()
    return "\\begin{%s}\n%s\n\\end{%s}" % (nested, content, nested) if nested else content


_REFERENCE = re.compile(r"\\(eqref|ref|cref|Cref|autoref|cite[a-zA-Z]*)\s*(?:\[[^\]]*\]\s*)*\{([^{}]*)\}")


def _restore_references(original, new, numbers, notes):
    """The export printed `\\eqref{a}` as "(1)", which comes back from Word as text: put the reference back
    where that is certain (once, in a text part such as `\\text{by (1)}`), and name the others."""
    from .wordexport import _without_comments
    body = _without_comments(original.body)
    lost = []
    for m in _REFERENCE.finditer(body):
        if m[0] in new:
            continue
        printed = numbers.get(m[2]) if m[1] == "eqref" else None
        places = [i for i in (j.start() for j in re.finditer(re.escape(printed), new))
                  if any(a <= i < b for a, b in compat._text_spans(new))] if printed else []
        # certain only when the number was this reference's alone in what Word showed: in
        # "case (1), by \eqref{a}" a deleted reference must not move onto the case number
        # (and in the export's own text, macros written out: \caseone may print (1) too)
        shown = body.replace(m[0], printed) if printed else ""
        if (len(places) == 1 and new.count(printed) == 1 and body.count(m[0]) == 1 and shown.count(printed) == 1
                and (original.key or "").count(printed) <= 1):
            new = new[:places[0]] + m[0] + new[places[0] + len(printed):]
        elif m[0] not in lost:
            lost.append(m[0])
    if lost:
        notes.append("Equation %d had %s, which Word shows as plain text: put %s back in the corrected copy."
                     % (original.n, ", ".join(lost), "it" if len(lost) == 1 else "them"))
    return new


_AMSMATH = re.compile(r"\\begin\s*\{([pbBvV]?matrix|smallmatrix|cases|aligned|gathered|split|alignedat)\}"
                      r"|\\(text|operatorname|dfrac|tfrac|binom|boldsymbol|substack|overset|underset)(?![A-Za-z])")
_AMSMATH_LOADED = re.compile(r"\\usepackage\s*(?:\[[^\]]*\]\s*)?\{[^{}]*\b(?:amsmath|mathtools|physics)\b"
                             r"|\\documentclass\s*(?:\[[^\]]*\]\s*)?\{(?:ams\w+|acmart)\}"
                             r"|\\begin\s*\{(?:align|gather|multline|flalign|alignat|[pbBvV]matrix|cases)\*?\}"
                             r"|\\(?:text|eqref|operatorname|tag)\s*\{")


def _check_amsmath(tree, original, new, notes):
    """A note when the new LaTeX of an equation needs amsmath (Word's matrices come back as pmatrix) and
    nothing in the paper shows that it is loaded."""
    used = {m[1] or m[2] for m in _AMSMATH.finditer(new)} - {m[1] or m[2] for m in _AMSMATH.finditer(original.body)}
    if not used or any(_AMSMATH_LOADED.search(text) for text in tree.mask.values()):
        return
    notes.append("Equation %d now uses %s, which needs the amsmath package; the paper does not seem to load it."
                 % (original.n, ", ".join(sorted(used))))


def _edits(result, tree, notes):
    """Collect edits against a single snapshot, so duplicates and insertions keep their places."""
    originals = {o.n: o for o in result.originals} or {r.original.n: r.original for r in result.rows if r.original}
    positions = {n: tree.position(eq) for n, eq in originals.items()}
    edits = defaultdict(list)
    changes = defaultdict(list)
    filename = " ".join(result.returned_docx.name.splitlines())
    # what the export printed for \eqref{label}: the number of an equation with one label and one number
    # (\eqref puts the number in parentheses; a \tag*{H} shows H at the margin, so its \eqref prints (H))
    numbers = {o.labels[0]: "(%s)" % o.numbers[0] if re.search(r"\\tag\s*\*", o.tex) else o.numbers[0]
               for o in originals.values() if len(o.labels) == len(o.numbers) == 1}

    def not_found(eq):
        note = "Equation %d not found in %s. Left the source as it is." % (eq.n, eq.file or tree.paper.name)
        if note not in notes:
            notes.append(note)

    for row in result.rows:
        eq = row.original
        if row.status in ("unreadable", "unmatched") or (eq and eq.macro and row.status == "changed"):
            if row.note not in notes:
                notes.append(row.note)
            continue
        if eq:
            pos = positions[eq.n]
            if pos:
                changes[pos].append(row)
            elif row.status in ("changed", "deleted", "moved"):
                not_found(eq)
        elif row.status == "added":
            anchor = originals.get(row.after) or originals.get(min(originals, default=None))
            if anchor and not positions[anchor.n]:
                not_found(anchor)
                notes.append("Could not place the added equation: %s" % row.returned)
                continue
            if anchor:
                path, (start, end) = positions[anchor.n]
                index = end if row.after else start
            else:
                path = tree.paper
                end = re.search(r"\\end\s*\{document\}", tree.mask[path])
                index = end.start() if end else len(tree.text[path])
            place = (" after " + (anchor.numbers[-1] if anchor.numbers else "equation %d" % anchor.n)
                     if row.after else " at the start")
            added = latex_wrap.wrap(row.returned, "auto", row.display)
            block = ["% MathShuttle: added in " + filename + place + ":"] + ["% " + line for line in added.splitlines()]
            text = tree.text[path]
            # A comment ending mid-paragraph must preserve the space (or adjacency) already there.
            at_line_start = not index or text[index - 1] == "\n"
            at_line_end = not text[index:index + 1] or text[index] in "\r\n"
            prefix = "" if at_line_start else ("\n" if at_line_end or text[index].isspace() else "%\n")
            suffix = "" if at_line_end and index < len(text) else "\n"
            edits[path].append((index, index, prefix + "\n".join(block) + suffix))
    for (path, (start, end)), rows in changes.items():
        if len({(r.status, r.returned) for r in rows}) > 1:
            notes.append("Conflicting edits to a repeated input in %s. Left the source as it is." % path.name)
            continue
        row = rows[0]
        if row.status == "changed":
            names = compat._control_words(row.original.body)
            macros = _macros(tree, result.macros, (path, start), names) if names else []
            before = len(notes)
            new = _replacement(row.original, row.returned, macros, notes, row.numbers)
            if new is None:
                if len(notes) == before:
                    notes.append("Could not restore the delimiters of equation %d." % row.original.n)
            else:
                new = _restore_references(row.original, new, numbers, notes)
                _check_amsmath(tree, row.original, new, notes)
                edits[path].append((start, end, new))
        elif row.status == "deleted":
            people = [", ".join(" ".join(s.splitlines()) for s in (r.author, r.date) if s) for r in row.revisions]
            by = " (by %s)" % "; ".join(people) if any(people) else ""
            line = tree.text[path].rfind("\n", 0, start) + 1
            edits[path].append((line, line, "% MathShuttle: deleted in " + filename + by + "\n"))
        elif row.status == "moved":
            line = tree.text[path].rfind("\n", 0, start) + 1
            edits[path].append((line, line, "% MathShuttle: moved in " + filename + row.note[len("moved"):] + "\n"))
    return edits


def _outputs(tree, out, edits):
    """Choose separate files for edited inputs and redirect their parent inputs to the copies."""
    moved = out.parent != tree.folder
    paths = set(tree.text) if moved else set(edits) | {tree.paper}
    only = [(path, match.start(1) + entry.start(), match.start(1) + entry.end(), entry[0])
            for path, text in tree.mask.items() for match in re.finditer(r"\\includeonly\s*\{([^{}]*)\}", text)
            for entry in re.finditer(r"[^,\s]+", match[1])]
    while True:
        parents = {p for p, links in tree.inputs.items() if any(child in paths for _, child in links)}
        redirects = {m[2].strip() for links in tree.inputs.values() for m, child in links
                     if m[1] == "include" and child in paths}
        parents.update(path for path, _, _, entry in only if entry in redirects)
        if parents <= paths:
            break
        paths.update(parents)
    targets = {tree.paper: out}
    for i, path in enumerate(sorted(paths - {tree.paper}), 1):
        relative = path.relative_to(tree.folder) if path.is_relative_to(tree.folder) else Path("external-%d" % i, path.name)
        targets[path] = out.parent / (out.stem + "-inputs") / relative
    includes = {}
    for path in paths:
        for match, child in tree.inputs[path]:
            if child in targets:
                name = Path(os.path.relpath(targets[child], out.parent)).as_posix()
                if match[1] == "include" and name.endswith(".tex"):
                    name = name[:-4]
                    includes[match[2].strip()] = name
                edits[path].append((*match.span(2), name))
    for path, start, end, entry in only:
        if entry in includes:
            edits[path].append((start, end, includes[entry]))
    return targets


def _patch_lines(before, after, source, target):
    for line in difflib.unified_diff(before.splitlines(True), after.splitlines(True), source, target):
        yield line if line.endswith("\n") else line + "\n\\ No newline at end of file\n"


def corrected(result, out):
    """Write the corrected main file, its changed inputs and a diff covering all of them."""
    out = out.resolve()
    notes = []
    try:
        tree = SourceTree(result.paper_tex)
        edits = _edits(result, tree, notes)
        targets = _outputs(tree, out, edits)
        diff_path = out.with_suffix(".diff")
        protected = set(tree.text) | {result.returned_docx}
        destinations = [*targets.values(), diff_path]
        for path in destinations:
            if path != out and os.path.lexists(path):
                raise ConversionError("The file %s already exists. Choose another name for the corrected paper." % path)
        if len(set(destinations)) != len(destinations) or any(
                p.resolve() in protected or (p.exists() and any(p.samefile(s) for s in protected))
                for p in destinations):
            raise ConversionError("Choose a separate file for the corrected paper and its diff.")
        files, diff = {}, []
        for path, target in targets.items():
            before = tree.text[path]
            newline = "\r\n" if "\r\n" in before else "\n"
            after = before
            # Reverse insertion order for edits at the same point, to keep the returned order.
            for a, b, value in sorted(reversed(edits[path]), key=lambda edit: edit[:2], reverse=True):
                after = after[:a] + value.replace("\r\n", "\n").replace("\n", newline) + after[b:]
            name = os.path.relpath(path, tree.folder)
            diff.extend(_patch_lines(before, after, name, os.path.relpath(target, out.parent)))
            encoding = tree.encoding[path]
            bom = {"utf-16-le": codecs.BOM_UTF16_LE, "utf-16-be": codecs.BOM_UTF16_BE}.get(encoding, b"")
            files[target] = bom + after.encode(encoding)
        files[diff_path] = "".join(diff).encode("utf-8")
        staged = None
        created = {}
        try:
            out.parent.mkdir(parents=True, exist_ok=True)
            with tempfile.NamedTemporaryFile(dir=out.parent, delete=False) as f:
                staged = Path(f.name)
                f.write(files.pop(out))
            for target, data in files.items():
                target.parent.mkdir(parents=True, exist_ok=True)
                try:
                    with target.open("xb") as f:
                        created[target] = os.fstat(f.fileno())
                        f.write(data)
                except FileExistsError:
                    raise ConversionError("The file %s already exists. Choose another name for the corrected paper."
                                          % target)
            os.replace(staged, out)
        except BaseException:
            for target, stat in created.items():
                if target.exists() and os.path.samestat(stat, target.stat()):
                    target.unlink()
            raise
        finally:
            if staged:
                staged.unlink(missing_ok=True)
    except (OSError, UnicodeError) as exc:
        raise ConversionError("Could not write the corrected paper: %s" % exc) from exc
    if out.parent != tree.folder:
        notes.append(r"LaTeX looks for \input files, .sty files and figures next to the copy. "
                     "Copy any missing files there before compiling.")
    return notes
