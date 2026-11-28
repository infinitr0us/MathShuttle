"""Write and read the equation source records in a Word document's custom XML part."""
from __future__ import annotations

import difflib
import json
import os
import re
import uuid
import xml.etree.ElementTree as ET
from collections import Counter, defaultdict
from pathlib import Path

from . import __version__, compat, documents, wordmath
from .convert import tidied
from .pandoc_engine import ConversionError, Equation, _collect_math, _run, markdown_blocks, markdown_maths
from .texsource import Original, SourceTree, _DELIMITERS, _dollar_spans, _pattern, _same_source
from .wordmath import W

NS = "urn:mathshuttle:equations"
DS = "http://schemas.openxmlformats.org/officeDocument/2006/customXml"
PARTS = ("word/document.xml", "word/footnotes.xml", "word/endnotes.xml")
ET.register_namespace("ms", NS)
ET.register_namespace("ds", DS)


def temporary_prefix():
    return "MS" + uuid.uuid4().hex[:20]


def key(tex):
    """Keep text spaces and the boundary between a control word and the next letter."""
    def compact(value):
        def space(m):
            if m[1]:
                return m[1] + " "
            return "" if m[0].isspace() else m[0]
        return re.sub(r"(\\[A-Za-z@]+)\s+(?=[A-Za-z@])|\\[\s\S]|\s+", space, value)

    parts, last = [], 0
    for start, end in compat._text_spans(tex):
        parts.extend((compact(tex[last:start]), tex[start:end]))
        last = end
    return "".join(parts) + compact(tex[last:])


def read_maths(fragments):
    """Read all number-free fragments together, through the same pipeline as Paste from Word."""
    positions = [i for i, f in enumerate(fragments) if not wordmath.empty(f)]
    values = [""] * len(fragments)
    for i, eq in zip(positions, documents._read_fragments([fragments[i] for i in positions])):
        values[i] = tidied(eq).tex if eq is not None else None
    return values


def raw_equations(text, fmt, folder=None):
    """Read equation bodies without expanding their macro calls, with the heading of each."""
    from .wordexport import _split_grouped
    raw, sections = [], []
    try:
        if fmt == "markdown" and folder:
            meta = {}
            blocks, _ = markdown_blocks(text, fmt + "-latex_macros", folder, meta=meta)
        else:
            args = ["-f", fmt + "-latex_macros", "-t", "json"]
            data = json.loads(_run(args if folder else args + ["--sandbox"], text.encode("utf-8"), cwd=folder)[0])
            meta, blocks = data["meta"], data["blocks"]
    except ConversionError:
        return raw, sections
    _split_grouped(blocks)
    heading = ""
    definitions = []
    for block in [meta, *blocks]:
        if block.get("t") == "Header":
            words = []
            documents._inline_text(block["c"][2], words)
            heading = "".join(words)
        if fmt == "markdown" and folder:
            markdown_maths(block, raw, definitions, folder)
        else:
            _collect_math(block, raw)
        sections.extend([heading] * (len(raw) - len(sections)))
    return raw, sections


def match_sources(maths, raw):
    expanded = [Equation(eq.tex, eq.display) for eq in maths]
    matched = documents._matched(expanded, raw)
    for i, eq in enumerate(expanded):
        if matched[i] is None and len(expanded) == len(raw) and eq.display == raw[i].display:
            # A macro can expand to an entire formula, leaving no common letters for _matched.
            matched[i] = raw[i]
    return [candidate or eq for candidate, eq in zip(matched, expanded)]


# the end of a display's own environment (an inner one, pmatrix or cases, is part of a formula)
_DISPLAY_END = (r"\\end\s*\{(?:equation|align|alignat|gather|multline|flalign|eqnarray|math|displaymath|dmath)"
                r"\*?\}\s*$")


def _without_citations(node):
    """Citations replaced by nothing, the maths in their notes too (`\\cite[case $p=2$]{x}`)."""
    if isinstance(node, list):
        for i, child in enumerate(node):
            if isinstance(child, dict) and child.get("t") == "Cite":
                node[i] = {"t": "Str", "c": ""}
            else:
                _without_citations(child)
    elif isinstance(node, dict):
        for child in node.values():
            _without_citations(child)


def _trace_sources(tree, maths, raw):
    """Read an in-memory copy with a position written into each literal formula."""
    from .wordexport import _split_grouped
    prefix = temporary_prefix()
    mark = re.compile(prefix + r"(\d+)Z")
    origins, inserts = [], defaultdict(dict)

    def insert(path, offset, origin):
        if offset not in inserts[path]:
            inserts[path][offset] = " " + prefix + str(len(origins)) + "Z "
            origins.append(origin)

    patterns = {_pattern(eq.tex, eq.display) for eq in raw}
    for path, source in tree.text.items():
        spans = {span for pattern in patterns for span in tree.spans(path, pattern)}
        for a, b in sorted(spans, key=lambda span: (span[0], -span[1])):
            literal = source[a:b]
            close = next((end for start, end, _ in _DELIMITERS if literal.startswith(start) and literal.endswith(end)),
                         None)
            if close:
                offset = b - len(close)
            else:
                end = re.search(_DISPLAY_END, literal)  # a display's own environment, not a matrix in it
                if not end:
                    continue
                offset = a + end.start()
            insert(path, offset, (path, (a, b), ""))
        for definition in compat.parse_definitions(source)[0]:
            if re.fullmatch(r"\}?\s*(?:\[[^\]]*\]\s*)*\{?\s*\\(?:end\s*\{[^{}]+\}|\]|\))\s*\}?\s*",
                            compat._after_name(definition)):
                continue  # a shorthand that only closes an equation (\ee): the maths is the paper's own
            # Label maths created in prose, where there are no literal delimiters at the call.
            ends = [b - (2 if definition.source.startswith("$$", a) else 1)
                    for a, b in _dollar_spans(definition.source).items()]
            ends.extend(m.start() for m in re.finditer(r"\\(?:\)|\]|end\s*\{(?:equation|align|gather|multline)\*?\})",
                                                       definition.source))
            for call in re.finditer(r"\\ensuremath\s*\{", definition.source):
                end = compat._group_end(definition.source, call.end() - 1)
                if end > 0:
                    ends.append(end - 1)
            for offset in ends:
                insert(path, definition.span[0] + offset, (None, None, definition.name))

    chunks = []
    for path, start, end in tree.segments:
        pos = start
        for offset, value in sorted(inserts[path].items()):
            if start <= offset < end:
                chunks.extend((tree.text[path][pos:offset], value))
                pos = offset
        chunks.extend((tree.text[path][pos:end], "\n"))
    traced, meta = [], {}
    try:
        blocks, _, _ = documents._read_latex_blocks("".join(chunks), str(tree.folder), [], meta=meta)
    except ConversionError:
        return {}
    _split_grouped(blocks)
    _without_citations([meta, blocks])  # as the export prints them: no maths of their own
    _collect_math([meta, blocks], traced)
    # Pair the traced formulas with the export's by content: the export leaves out some that tracing
    # sees (`\cite[Theorem $3.1$]{x}` is printed as a citation), which must not cost the others.
    seen = [(eq.display, _same_source(mark.sub("", eq.tex))) for eq in traced]
    wanted = [(eq.display, _same_source(eq.tex)) for eq in maths]
    if seen == wanted:
        pairs = list(enumerate(range(len(traced))))
    else:
        # only formulas that occur once on both sides: a repeated one could be paired with another
        once_seen, once_wanted = Counter(seen), Counter(wanted)
        matcher = difflib.SequenceMatcher(None, seen, wanted, autojunk=False)
        pairs = [(j, i) for tag, i1, i2, j1, j2 in matcher.get_opcodes() if tag == "equal"
                 for i, j in zip(range(i1, i2), range(j1, j2))
                 if once_seen[seen[i]] == once_wanted[wanted[j]] == 1]
    traced = {j: traced[i] for j, i in pairs}
    # An equation's own mark ends it (it was put before the closing delimiter); a mark in the middle
    # belongs to a formula nested in it (`\text{for $x$}` in a display made by \be ... \ee).
    own = re.compile(prefix + r"(\d+)Z\s*(?:\\end\s*\{[^{}]+\}\s*)*$")
    result = {}
    for i, eq in traced.items():
        last = own.search(eq.tex)
        if last:
            result[i] = origins[int(last[1])]
    return result


class _Sources(list):
    """The source records and the files in the snapshot they describe."""

    def __init__(self, tree):
        super().__init__()
        self.hashes = {Path(os.path.relpath(p, tree.folder)).as_posix(): value for p, value in tree.digests.items()}


def sources(path, prep):
    from .wordexport import _LABEL, _without_comments

    tree = SourceTree(path)
    fmt = "markdown" if tree.paper.suffix.lower() == ".md" else "latex"
    raw, sections = raw_equations(tree.text[tree.paper], fmt, str(tree.folder))
    if not raw and prep.maths:
        for f in documents.read_document(path, expand_macros=True).found:
            raw.append(f.equation)
            sections.append(re.sub(r"  \[[^{}]*\]$", "", f.where))
    headings = {id(eq): heading for eq, heading in zip(raw, sections)}
    traced = _trace_sources(tree, prep.maths, raw) if fmt == "latex" else {}
    originals = _Sources(tree)
    for i, source in enumerate(match_sources(prep.maths, raw)):
        original = Original(i + 1, source.tex, source.display, headings.get(id(source), ""))
        original.numbers = [prep.numbers[int(k)] for k in re.findall(r"QQMSN(\d+)QQ", prep.maths[i].prepared)
                            if int(k) in prep.numbers]
        if i in traced:
            location, span, original.macro = traced[i]
            if location:
                tree.record(original, location, span)
            else:
                original.tex = prep.maths[i].tex
        elif fmt == "markdown":
            tree.locate(original)
        original.labels = _LABEL.findall(_without_comments(original.tex))
        originals.append(original)
    return originals


def _xml(root):
    return ET.tostring(root, encoding="utf-8", xml_declaration=True)


def _custom_part(parts, path, records, hashes):
    manifest = ET.Element("{%s}equations" % NS, source=Path(path).name, version=__version__)
    manifest.set("files", json.dumps(hashes, ensure_ascii=False))
    for eq in records:
        entry = ET.SubElement(manifest, "eq", n=str(eq.n), tex=eq.tex, display=str(int(eq.display)), section=eq.section,
                              file=eq.file, occurrence=str(eq.occurrence), count=str(eq.count), key=eq.key,
                              labels=json.dumps(eq.labels, ensure_ascii=False),
                              numbers=json.dumps(eq.numbers, ensure_ascii=False))
        if eq.picture:
            entry.set("picture", "1")
        if eq.digest:
            entry.attrib.update(before=eq.before, after=eq.after)
            if not eq.unique_context:
                entry.set("uniqueContext", "0")
        if eq.macro:
            entry.set("macro", eq.macro)
    i = 1
    while f"customXml/item{i}.xml" in parts or f"customXml/itemProps{i}.xml" in parts:
        i += 1
    item, props = f"item{i}.xml", f"itemProps{i}.xml"
    parts["customXml/" + item] = _xml(manifest)
    ds = "{%s}" % DS
    store = ET.Element(ds + "datastoreItem", {ds + "itemID": "{%s}" % str(uuid.uuid4()).upper()})
    ET.SubElement(ET.SubElement(store, ds + "schemaRefs"), ds + "schemaRef", {ds + "uri": NS})
    parts["customXml/" + props] = _xml(store)
    rels = ET.Element("Relationships", xmlns=documents.REL_NS)
    ET.SubElement(rels, "Relationship", Id="rId1", Type=documents.R_NS + "/customXmlProps", Target=props)
    parts["customXml/_rels/" + item + ".rels"] = _xml(rels)
    rel_path = "word/_rels/document.xml.rels"
    rels = ET.fromstring(parts[rel_path])
    ids = {r.get("Id") for r in rels}
    rid = next("rIdMS%d" % n for n in range(1, len(ids) + 2) if "rIdMS%d" % n not in ids)
    ET.SubElement(rels, "{%s}Relationship" % documents.REL_NS, Id=rid, Type=documents.R_NS + "/customXml",
                  Target="../customXml/" + item)
    parts[rel_path] = _xml(rels)
    types = ET.fromstring(parts["[Content_Types].xml"])
    ET.SubElement(types, types.tag.replace("}Types", "}Override"), PartName="/customXml/" + props,
                  ContentType="application/vnd.openxmlformats-officedocument.customXmlProperties+xml")
    parts["[Content_Types].xml"] = _xml(types)


def mark_export(parts, path, prefix, originals):
    """Number bookmarks in the finished file and record what each one actually contains."""
    records, fragments = [], []
    marker = re.compile("_?" + prefix + r"(\d+)$")
    for name in PARTS:
        if name not in parts:
            continue
        xml = parts[name].decode("utf-8")
        # A user can give a heading the same label as a reserved equation bookmark.
        # Keep links to that heading pointing to its own unique bookmark.
        xml = re.sub(r'(w:(?:name|anchor)=")_MSEq(\d+)"',
                     lambda m: m[1] + "_" + prefix + "Ref" + m[2] + '"', xml)
        renamed, empty = {}, set()
        current = None
        for el in ET.fromstring(xml).iter():
            if el.tag == W + "bookmarkStart":
                found = marker.fullmatch(el.get(W + "name", ""))
                if found:
                    current = (el.get(W + "name"), el.get(W + "id"), originals[int(found[1]) - 1])
            elif current and el.tag == W + "bookmarkEnd" and el.get(W + "id") == current[1]:
                if current[0] not in renamed:
                    empty.add(current[1])  # an equation pandoc had to leave as ordinary text
                current = None
            elif current and current[0] not in renamed and (
                    el.tag in documents._MATH_TAGS or (current[2].picture and el.tag == W + "drawing")):
                old_name, _, original = current
                original.n = len(records) + 1
                renamed[old_name] = "_MSEq%d" % original.n
                if not original.picture:
                    view = wordmath.revision_view(el)
                    original.numbers = wordmath.strip_numbers(view, original.numbers)
                    fragments.append(view)
                records.append(original)
        # Preserve pandoc's namespace declarations, including names used in attribute values.
        def bookmark(m):
            ident = re.search(r'\bw:id="([^"]+)"', m[0])
            if ident and ident[1] in empty:
                return ""
            return re.sub(r'w:name="([^"]+)"', lambda n: 'w:name="%s"' % renamed.get(n[1], n[1]), m[0])

        parts[name] = re.sub(r"<w:bookmark(?:Start|End)\b[^>]*/>", bookmark, xml).encode("utf-8")
    for record, tex in zip((r for r in records if not r.picture), read_maths(fragments)):
        if tex is None:
            raise ConversionError("Could not read back exported equation %d." % record.n)
        record.key = key(tex)
    _custom_part(parts, path, records, originals.hashes if originals else {})


def manifest(z, main_file=None):
    """Records reached through the document's custom XML relationship, or None for an old export."""
    for _, kind, target in documents._targets(z, "word/document.xml"):
        if not kind.endswith("/customXml") or target not in z.namelist():
            continue
        root = documents._parse(z.read(target))
        if root.tag != "{%s}equations" % NS:
            continue
        records = []
        hashes = json.loads(root.get("files", "{}"))
        for el in root:
            if el.tag not in ("eq", "{%s}eq" % NS):
                continue
            if "key" not in el.attrib:
                return None
            file = el.get("file", "")
            if main_file and file == root.get("source"):
                file = main_file
            record = Original(int(el.attrib["n"]), el.attrib["tex"], el.get("display") == "1",
                              el.get("section", ""), file, int(el.get("occurrence", 0)),
                              int(el.get("count", 0)), json.loads(el.get("labels", "[]")),
                              json.loads(el.get("numbers", "[]")), el.attrib["key"], el.get("picture") == "1",
                              hashes.get(el.get("file", ""), ""), el.get("before", ""), el.get("after", ""),
                              el.get("macro", ""), el.get("uniqueContext") != "0")
            if "files" not in root.attrib:
                # Earlier exports dropped control-word spaces. The saved literal can recover them.
                source_key = key(record.body)
                if source_key.replace(" ", "") == record.key and not compat._text_spans(record.body):
                    record.key = source_key
            records.append(record)
        return records
    return None


def picture_bookmarks(z):
    """Picture numbers that survive when a Word editor removes the custom XML."""
    numbers = set()
    for part in PARTS:
        if part not in z.namelist():
            continue
        marked = {}
        for el in documents._parse(z.read(part)).iter():
            if el.tag == W + "bookmarkStart":
                found = re.fullmatch(r"_MSEq(\d+)", el.get(W + "name", ""))
                if found:
                    marked[el.get(W + "id")] = int(found[1])
            elif el.tag == W + "bookmarkEnd":
                marked.pop(el.get(W + "id"), None)
            elif el.tag == W + "drawing":
                numbers.update(marked.values())
    return numbers
