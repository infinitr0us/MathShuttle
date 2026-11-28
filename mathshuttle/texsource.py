"""Find pandoc's equations in the paper and the local files it reads."""
from __future__ import annotations

import codecs
import difflib
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
        original.tex = self.text[path][span[0]:span[1]]
        original.file = Path(os.path.relpath(path, self.folder)).as_posix()
        matches = self.exact_spans(path, original.tex)
        original.count = len(matches)
        original.occurrence = matches.index(span) + 1

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
            if len(matches) == original.count and len(matches) >= original.occurrence:
                return path, matches[original.occurrence - 1]
        return None


def _macros(tree, user):
    """Literal expansions of the paper's zero-argument macros, longest first."""
    definitions = documents._local_sources(str(tree.paper), tree.text[tree.paper])
    macros = compat.with_document(user, definitions)
    choices = {}
    if macros:
        for name, definition in macros.definitions.items():
            body = compat._simple_expansion(name, macros.needed(name))
            if body is None and not compat._takes_arguments(definition):
                # An explicit [0], an empty xparse signature, or a macro using an argument-taking helper.
                tail = re.sub(r"^\s*\}?\s*(?:\[\s*0\s*\]|\{\s*\})?\s*", "", compat._after_name(definition))
                if tail.startswith("{") and compat._group_end(tail, 0) == len(tail):
                    body, _ = compat.expand_macros(name, macros)
            if body and body != name:
                choices.setdefault(body, name)
    return sorted(choices.items(), key=lambda pair: -len(pair[0]))


def _restore_macros(tex, macros):
    tokens = list(_TOKEN.finditer(tex))
    ends = {m.end() for m in tokens}
    protected = compat._text_spans(tex) + [m.span() for m in latex_wrap._TOKEN_RE.finditer(tex) if m.group("be")]
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


def _replacement(original, tex, macros, notes):
    tex = _restore_macros(tex, macros)
    source = original.tex.strip()
    for opening, closing, wrapper in _DELIMITERS:
        if source.startswith(opening) and source.endswith(closing):
            body = _restore_rows(original, source[len(opening):-len(closing)], tex, notes)
            return latex_wrap.wrap(body, wrapper, original.display)
    env = latex_wrap._ENV_RE.fullmatch(source)
    if not env:
        return None
    name, body = env["env"], env["body"]
    if name in latex_wrap.WRAPPER_KEYS:
        wrapped = latex_wrap.wrap(tex, name)
        new_body = latex_wrap._ENV_RE.fullmatch(wrapped)["body"].strip()
    elif name in latex_wrap.ALIGN_ENVS:
        new_body = latex_wrap.classify(tex)[1]
        if name.startswith("alignat"):
            arg = latex_wrap._ALIGNAT_ARG_RE.match(body)
            if arg:
                new_body = arg[0].strip() + "\n" + new_body
        elif name.startswith("eqnarray"):
            new_body = re.sub(r"(?<!\\)&\s*(\\[A-Za-z]+|[=<>+\-])", r"&\1&", new_body)
    else:
        new_body = latex_wrap.plain_form(tex)
    return "\\begin{%s}\n%s\n\\end{%s}" % (name, _restore_rows(original, body, new_body, notes).strip(), name)


def _restore_rows(original, body, new_body, notes):
    from .wordexport import _top_rows
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
    if len(rows[::2]) != len(old_rows):
        if any(marks):
            retained = original.labels[:1]
            note = "Equation %d has a different number of rows." % original.n
            if retained:
                note += " Kept label %s." % retained[0]
            if original.labels[1:]:
                note += " Check the other labels: %s." % ", ".join(original.labels[1:])
            notes.append(note)
            marks = [[r"\label{%s}" % label for label in retained]]
    for i, mark in enumerate(marks):
        if mark:
            rows[2 * i] = rows[2 * i].rstrip() + " " + " ".join(mark) + ("\n" if i * 2 + 1 < len(rows) else "")
    content = "".join(rows).strip()
    return "\\begin{%s}\n%s\n\\end{%s}" % (nested, content, nested) if nested else content


def _edits(result, tree, notes):
    """Collect edits against a single snapshot, so duplicates and insertions keep their places."""
    originals = {r.original.n: r.original for r in result.rows if r.original}
    positions = {n: tree.position(eq) for n, eq in originals.items()}
    edits = defaultdict(list)
    changes = defaultdict(list)
    macros = _macros(tree, result.macros)
    filename = " ".join(result.returned_docx.name.splitlines())

    def not_found(eq):
        note = "Equation %d not found in %s. Left the source as it is." % (eq.n, eq.file or tree.paper.name)
        if note not in notes:
            notes.append(note)

    for row in result.rows:
        eq = row.original
        if eq:
            pos = positions[eq.n]
            if pos:
                changes[pos].append(row)
            else:
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
            added = latex_wrap.wrap(_restore_macros(row.returned, macros), "auto", row.display)
            block = ["% MathShuttle: added in " + filename + place + ":"] + ["% " + line for line in added.splitlines()]
            text = tree.text[path]
            # A comment ending mid-paragraph must preserve the space (or adjacency) already there.
            at_line_start = not index or text[index - 1] == "\n"
            at_line_end = not text[index:index + 1] or text[index] in "\r\n"
            prefix = "" if at_line_start else ("\n" if at_line_end else "%\n")
            suffix = "" if at_line_end and index < len(text) else "\n"
            edits[path].append((index, index, prefix + "\n".join(block) + suffix))
    for (path, (start, end)), rows in changes.items():
        if len({(r.status, r.returned) for r in rows}) > 1:
            notes.append("Conflicting edits to a repeated input in %s. Left the source as it is." % path.name)
            continue
        row = rows[0]
        if row.status == "changed":
            new = _replacement(row.original, row.returned, macros, notes)
            if new is None:
                notes.append("Could not restore the delimiters of equation %d." % row.original.n)
            else:
                edits[path].append((start, end, new))
        elif row.status == "deleted":
            people = [", ".join(" ".join(s.splitlines()) for s in (r.author, r.date) if s) for r in row.revisions]
            by = " (by %s)" % "; ".join(people) if any(people) else ""
            line = tree.text[path].rfind("\n", 0, start) + 1
            edits[path].append((line, line, "% MathShuttle: deleted in " + filename + by + "\n"))
    return edits


def _outputs(tree, out, edits):
    """Choose separate files for edited inputs and redirect their parent inputs to the copies."""
    moved = out.parent != tree.folder
    paths = set(tree.text) if moved else set(edits) | {tree.paper}
    while True:
        parents = {p for p, links in tree.inputs.items() if any(child in paths for _, child in links)}
        if parents <= paths:
            break
        paths.update(parents)
    targets = {tree.paper: out}
    for i, path in enumerate(sorted(paths - {tree.paper}), 1):
        relative = path.relative_to(tree.folder) if path.is_relative_to(tree.folder) else Path("external-%d" % i, path.name)
        targets[path] = out.parent / (out.stem + "-inputs") / relative
    for path in paths:
        for match, child in tree.inputs[path]:
            if child in targets:
                name = Path(os.path.relpath(targets[child], out.parent)).as_posix()
                if match[1] == "include" and name.endswith(".tex"):
                    name = name[:-4]
                edits[path].append((*match.span(2), name))
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
        staged = {}
        try:
            for target, data in files.items():
                target.parent.mkdir(parents=True, exist_ok=True)
                with tempfile.NamedTemporaryFile(dir=target.parent, delete=False) as f:
                    staged[target] = Path(f.name)
                    f.write(data)
            # Put inputs and the diff in place before publishing the main paper that refers to them.
            for target in [p for p in staged if p != out] + [out]:
                os.replace(staged[target], target)
        finally:
            for temp in staged.values():
                temp.unlink(missing_ok=True)
    except (OSError, UnicodeError) as exc:
        raise ConversionError("Could not write the corrected paper: %s" % exc) from exc
    return notes
