"""Write and read the equation source records in a Word document's custom XML part."""
from __future__ import annotations

import json
import re
import uuid
import xml.etree.ElementTree as ET
from pathlib import Path

from . import __version__, compat, documents, wordmath
from .convert import tidied
from .pandoc_engine import ConversionError, Equation, _collect_math, _run, markdown_blocks, markdown_maths
from .texsource import Original, SourceTree
from .wordmath import W

NS = "urn:mathshuttle:equations"
DS = "http://schemas.openxmlformats.org/officeDocument/2006/customXml"
PARTS = ("word/document.xml", "word/footnotes.xml", "word/endnotes.xml")
ET.register_namespace("ms", NS)
ET.register_namespace("ds", DS)


def temporary_prefix():
    return "MS" + uuid.uuid4().hex[:20]


def key(tex):
    """Tidied OMML LaTeX without math whitespace. Keep spaces that belong to text."""
    parts, last = [], 0
    for start, end in compat._text_spans(tex):
        parts.extend(("".join(tex[last:start].split()), tex[start:end]))
        last = end
    return "".join(parts) + "".join(tex[last:].split())


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
    originals = []
    for i, source in enumerate(match_sources(prep.maths, raw)):
        original = Original(i + 1, source.tex, source.display, headings.get(id(source), ""))
        tree.locate(original)
        original.labels = _LABEL.findall(_without_comments(original.tex))
        originals.append(original)
    return originals


def _xml(root):
    return ET.tostring(root, encoding="utf-8", xml_declaration=True)


def _custom_part(parts, path, records):
    manifest = ET.Element("{%s}equations" % NS, source=Path(path).name, version=__version__)
    for eq in records:
        entry = ET.SubElement(manifest, "eq", n=str(eq.n), tex=eq.tex, display=str(int(eq.display)), section=eq.section,
                              file=eq.file, occurrence=str(eq.occurrence), count=str(eq.count), key=eq.key,
                              labels=json.dumps(eq.labels, ensure_ascii=False),
                              numbers=json.dumps(eq.numbers, ensure_ascii=False))
        if eq.picture:
            entry.set("picture", "1")
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
                    original.numbers = wordmath.strip_numbers(view)
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
    _custom_part(parts, path, records)


def manifest(z):
    """Records reached through the document's custom XML relationship, or None for an old export."""
    for _, kind, target in documents._targets(z, "word/document.xml"):
        if not kind.endswith("/customXml") or target not in z.namelist():
            continue
        root = documents._parse(z.read(target))
        if root.tag != "{%s}equations" % NS:
            continue
        records = []
        for el in root:
            if el.tag not in ("eq", "{%s}eq" % NS):
                continue
            if "key" not in el.attrib:
                return None
            records.append(Original(int(el.attrib["n"]), el.attrib["tex"], el.get("display") == "1",
                                    el.get("section", ""), el.get("file", ""), int(el.get("occurrence", 0)),
                                    int(el.get("count", 0)), json.loads(el.get("labels", "[]")),
                                    json.loads(el.get("numbers", "[]")), el.attrib["key"], el.get("picture") == "1"))
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
