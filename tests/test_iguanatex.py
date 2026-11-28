"""LaTeX stored on shapes by the IguanaTeX and TeXPoint PowerPoint add-ins."""
import zipfile
import xml.etree.ElementTree as ET
from xml.sax.saxutils import quoteattr

import pytest

from mathshuttle.documents import A_NS, OMML_NS, P_NS, REL_NS, R_NS, read_document


def shape(name, tag=None, kind="pic", children=""):
    nv = {"pic": "nvPicPr", "grpSp": "nvGrpSpPr", "sp": "nvSpPr"}[kind]
    ref = '<p:custDataLst><p:tags r:id="tag%d"/></p:custDataLst>' % tag if tag else ""
    return (f'<p:{kind}><p:{nv}><p:cNvPr name={quoteattr(name)}/><p:nvPr>{ref}</p:nvPr></p:{nv}>'
            f'{children}</p:{kind}>')


def native(letter):
    return shape("native", kind="sp", children=(
        f"<p:txBody><a:p><m:oMath><m:r><m:t>{letter}</m:t></m:r></m:oMath></a:p></p:txBody>"))


TITLE = ('<p:sp><p:nvSpPr><p:nvPr><p:ph type="title"/></p:nvPr></p:nvSpPr>'
         '<p:txBody><a:p><a:r><a:t>Model</a:t></a:r></a:p></p:txBody></p:sp>')


def deck(tmp_path, slides, notes=None):
    """Each slide or notes page has its own shapes and list of tag parts. None is a missing part."""
    path = tmp_path / "equations.pptx"
    namespaces = f'xmlns:p="{P_NS}" xmlns:r="{R_NS}" xmlns:a="{A_NS}" xmlns:m="{OMML_NS}"'
    types = ET.Element("Types", xmlns="http://schemas.openxmlformats.org/package/2006/content-types")
    ET.SubElement(types, "Default", Extension="rels",
                  ContentType="application/vnd.openxmlformats-package.relationships+xml")

    def part(name, kind, xml):
        ET.SubElement(types, "Override", PartName="/" + name,
                      ContentType="application/vnd.openxmlformats-officedocument.presentationml." + kind + "+xml")
        z.writestr(name, xml)

    def relationships(name, entries):
        rels = ET.Element("Relationships", xmlns=REL_NS)
        for rid, kind, target in entries:
            ET.SubElement(rels, "Relationship", Id=rid, Type=R_NS + "/" + kind, Target=target)
        z.writestr(name, ET.tostring(rels))

    def page(folder, name, root, kind, content, rels):
        body, tags = content
        for index, values in enumerate(tags, 1):
            tag_file = f"{name}-tag{index}.xml"
            rels.append((f"tag{index}", "tags", "../tags/" + tag_file))
            if values is not None:
                tag_list = ET.Element("p:tagLst", {"xmlns:p": P_NS})
                for key, value in values.items():
                    ET.SubElement(tag_list, "p:tag", name=key, val=value)
                part("ppt/tags/" + tag_file, "tags", ET.tostring(tag_list))
        part(f"ppt/{folder}/{name}.xml", kind,
             f"<p:{root} {namespaces}><p:cSld><p:spTree>{body}</p:spTree></p:cSld></p:{root}>")
        relationships(f"ppt/{folder}/_rels/{name}.xml.rels", rels)

    with zipfile.ZipFile(path, "w") as z:
        ids, slide_rels = [], []
        for number, content in enumerate(slides, 1):
            name = "slide%d" % (len(slides) + 1 - number)  # part names run opposite to presentation order
            ids.append(f'<p:sldId id="{255 + number}" r:id="rId{number}"/>')
            slide_rels.append((f"rId{number}", "slide", f"slides/{name}.xml"))
            rels = []
            if notes and number == 1:
                page("notesSlides", "notesSlide1", "notes", "notesSlide", notes, [])
                rels.append(("notes", "notesSlide", "../notesSlides/notesSlide1.xml"))
            page("slides", name, "sld", "slide", content, rels)
        part("ppt/presentation.xml", "presentation.main",
             f'<p:presentation {namespaces}><p:sldIdLst>{"".join(ids)}</p:sldIdLst></p:presentation>')
        relationships("ppt/_rels/presentation.xml.rels", slide_rels)
        relationships("_rels/.rels", [("rId1", "officeDocument", "ppt/presentation.xml")])
        z.writestr("[Content_Types].xml", ET.tostring(types))
    return str(path)


def document(body, preamble=""):
    return "\\documentclass{article}\n%s\n\\begin{document}\n%s\n\\end{document}" % (preamble, body)


def test_picture_and_native_shapes_keep_their_places(tmp_path):
    first = TITLE + native("a") + shape("Loss", 1) + native("b")
    second = shape("Bound", 1)
    path = deck(tmp_path, [(first, [{"LATEXADDIN": document(r"\[x < y\]")}]),
                           (second, [{"LATEXADDIN": "Using $u$ and $v$."}])])
    result = read_document(path)
    assert result.source == "office"
    assert [(f.equation.tex, f.equation.display, f.where, f.context) for f in result.found] == [
        ("a", True, "slide 1: Model", ""), ("x < y", True, "slide 1: Model (IguanaTeX)", "Loss"),
        ("b", True, "slide 1: Model", ""), ("u", False, "slide 2 (IguanaTeX)", "Bound"),
        ("v", False, "slide 2 (IguanaTeX)", "Bound")]
    assert any("2 IguanaTeX" in note for note in result.notes)


def test_preamble_macros_in_align_and_scoped_to_the_picture(tmp_path):
    align = "\\begin{align*}\n x &= \\field \\\\\n y &= \\abs{z}\n\\end{align*}"
    preamble = (r"\usepackage{mathtools} % shape definitions follow" + "\n" + r"\newcommand{\field}{\mathbb{R}}" + "\n"
                + r"\DeclarePairedDelimiter{\abs}{\lvert}{\rvert}")
    tags = [{"LATEXADDIN": document(align, preamble)},
            {"LATEXADDIN": document(r"$\field$", r"\newcommand{\field}{\mathbb{C}}")}]
    result = read_document(deck(tmp_path, [(shape("Real", 1) + shape("Complex", 2), tags)]))
    real, other = [f.equation for f in result.found]
    assert real.display and real.tex.startswith(r"\begin{align*}") and real.tex.endswith(r"\end{align*}")
    assert all(s in real.tex for s in (r"\mathbb{R}", r"\lvert", r"\rvert", r"\\", "&="))
    assert r"\field" not in real.tex and r"\abs" not in real.tex
    assert other.tex == r"\mathbb{C}" and not other.display


def test_vector_group_and_picture_in_user_group(tmp_path):
    vector = shape("Vector", 1, "grpSp", shape("Stroke", kind="sp"))
    group = shape("User group", kind="grpSp", children=shape("Nested", 2) + shape("Ordinary picture"))
    result = read_document(deck(tmp_path, [(vector + group, [{"LATEXADDIN": "$v$"}, {"LATEXADDIN": "$n$"}])]))
    assert [(f.equation.tex, f.context) for f in result.found] == [("v", "Vector"), ("n", "Nested")]


@pytest.mark.parametrize("template", ["ignored\tignored\tignored\tx+y\tignored", "old EQUATION x+y TeMpLaTe TP end"])
def test_texpoint_source_and_template(tmp_path, template):
    tags = [{"SOURCE": r"\[a+b\]"}, {"TEXPOINT": "template", "SOURCE": template}]
    result = read_document(deck(tmp_path, [(shape("Display", 1) + shape("Inline", 2), tags)]))
    assert [(f.equation.tex, f.equation.display, f.where) for f in result.found] == [
        ("a+b", True, "slide 1 (TeXPoint)"), ("x+y", False, "slide 1 (TeXPoint)")]
    assert any("2 TeXPoint" in note for note in result.notes)


def test_bare_texpoint_source_and_sources_without_equations(tmp_path):
    tags = [{"SOURCE": r"\frac{a}{b}"}, {"SOURCE": "no fields", "TEXPOINT": "template"},
            {"LATEXADDIN": document("Text only")}, {"LATEXADDIN": "% Empty editor\n"},
            {"LATEXADDIN": document(r"\[\]")}]
    names = ["Bare formula", "Empty template", "Text only", "Comment only", "Empty display"]
    shapes = "".join(shape(name, i) for i, name in enumerate(names, 1))
    result = read_document(deck(tmp_path, [(shapes, tags)]))
    assert [(f.equation.tex, f.equation.display) for f in result.found] == [(r"\frac{a}{b}", True)]
    for name in names[1:]:
        assert any(name in note and "slide 1" in note for note in result.notes)


def test_tags_in_notes_use_their_own_relationships(tmp_path):
    notes = (shape("Note", 1, "sp"), [{"SOURCE": "$n$"}])
    result = read_document(deck(tmp_path, [(shape("On slide", 1), [{"LATEXADDIN": "$s$"}])], notes))
    assert [(f.equation.tex, f.where) for f in result.found] == [
        ("s", "slide 1 (IguanaTeX)"), ("n", "slide 1 notes (TeXPoint)")]


def test_unreadable_latex_and_missing_tag_part_do_not_hide_other_shapes(tmp_path):
    shapes = TITLE + shape("Broken", 1) + shape("Missing", 2) + shape("Other tags", 3) + shape("Good", 4)
    tags = [{"LATEXADDIN": document(r"\begin{align*} x")}, None, {"OWNER": "someone"}, {"SOURCE": "$z$"}]
    result = read_document(deck(tmp_path, [(shapes, tags)]))
    assert [f.equation.tex for f in result.found] == ["z"]
    assert any("slide 1" in note and "IguanaTeX" in note and "Broken" in note for note in result.notes)
    assert any("slide 1" in note and "Missing" in note for note in result.notes)
    assert any("1 IguanaTeX" in note for note in result.notes)
    assert any("1 TeXPoint" in note for note in result.notes)
