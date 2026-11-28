"""Pictures for equations the Word writer cannot convert."""
import io
import struct
import xml.etree.ElementTree as ET
import zipfile
from pathlib import Path

import pytest

from mathshuttle import preview
from mathshuttle.__main__ import main
from mathshuttle.compare import compare
from mathshuttle.wordexport import document_to_word, passage_to_word


W = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"
WP = "{http://schemas.openxmlformats.org/drawingml/2006/wordprocessingDrawing}"
PIC = "{http://schemas.openxmlformats.org/drawingml/2006/picture}"
DIAGRAM = r'\begin{tikzcd} \Obj \arrow[r,"f"] & B \end{tikzcd}'


@pytest.fixture(autouse=True)
def picture_cache(tmp_path, monkeypatch):
    monkeypatch.setattr(preview, "cache_dir", lambda: str(tmp_path))


def needs_tex(*packages):
    t = preview.tools()
    if not t or not t["pdflatex"] or not (t["gs"] or t["cairo"] or t["ppm"]):
        pytest.skip("needs pdfLaTeX and Ghostscript or Poppler")
    required = ["standalone", "amsmath", "amssymb", "amsfonts", "mathtools", "bm", *packages]
    if "mhchem" in required:
        required.append("chemgreek")
    if "tikz-cd" in required:
        required.append("tikz")
    for package in required:
        if not preview.installed(package):
            pytest.skip("missing LaTeX package " + package)


def paper(folder, body, preamble=""):
    path = folder / "paper.tex"
    path.write_text("\\documentclass{article}\n" + preamble + "\n\\begin{document}\n" +
                    body + "\n\\end{document}\n", encoding="utf-8")
    return path


def text_of(node):
    return "".join(t.text or "" for t in node.iter(W + "t"))


def pictures(docx, part="document"):
    with zipfile.ZipFile(docx) as z:
        xml = ET.fromstring(z.read("word/%s.xml" % part))
        pngs = [z.read(name) for name in z.namelist() if name.startswith("word/media/") and name.endswith(".png")]
    return xml, pngs


def descriptions(node):
    return [pic.get("descr") for pic in node.iter(WP + "docPr")]


@pytest.mark.parametrize("use_poppler", [False, True])
def test_diagram_with_paper_definition_arrows_and_label(tmp_path, monkeypatch, use_poppler):
    needs_tex("tikz-cd")
    if use_poppler:
        t = preview.tools()
        if not (t["cairo"] or t["ppm"]):
            pytest.skip("no Poppler PDF converter")
        monkeypatch.setitem(t, "gs", str(tmp_path / "missing-ghostscript"))
    src = paper(tmp_path, r"Before \[" + DIAGRAM + r"\] after.", r"\newcommand{\Obj}{A}")
    docx = src.with_suffix(".docx")
    notes = document_to_word(src, docx)
    xml, pngs = pictures(docx)
    assert len(pngs) == 1 and xml.find(".//" + PIC + "pic") is not None
    assert descriptions(xml) == [DIAGRAM]
    paras = list(xml.iter(W + "p"))
    assert [text_of(p).strip() for p in paras] == ["Before", "", "after."]
    assert descriptions(paras[1]) == [DIAGRAM]
    assert any("1 equation" in n and "picture" in n and "tikzcd" in n and "edited" in n for n in notes)
    width, height = struct.unpack(">II", pngs[0][16:24])
    assert 140 < width < 650 and 35 < height < 220
    extent = xml.find(".//" + WP + "extent")
    assert int(extent.get("cx")) / 914400 == pytest.approx(width / 300, abs=0.005)
    assert int(extent.get("cy")) / 914400 == pytest.approx(height / 300, abs=0.005)
    # Both letters alone would also give a non-blank PNG. Check for the shaft
    # between them and for the label above the shaft, independent of margins.
    from PIL import Image
    with Image.open(io.BytesIO(pngs[0])) as im:
        rgba = im.convert("RGBA")
        white = Image.new("RGBA", im.size, "white")
        white.alpha_composite(rgba)
        ink = white.convert("L").point(lambda p: 255 if p < 150 else 0)
    ink = ink.crop(ink.getbbox())
    w, h = ink.size
    middle = ink.crop((w // 3, 0, 2 * w // 3, h))
    shaft = max(range(h), key=lambda y: middle.crop((0, y, middle.width, y + 1)).histogram()[255])
    assert middle.crop((0, shaft, middle.width, shaft + 1)).histogram()[255] > middle.width * 0.85
    assert middle.crop((0, 0, middle.width, shaft - 3)).histogram()[255] > 10


@pytest.mark.parametrize("tex,package", [(r"\ce{H2O}", "mhchem"), (r"\xymatrix{A \ar[r] & B}", "xy")])
def test_passage_picture_inline(tmp_path, tex, package):
    needs_tex(package)
    docx = tmp_path / "p.docx"
    passage_to_word("Before $" + tex + "$ after.", docx)
    xml, pngs = pictures(docx)
    assert len(pngs) == 1
    para, = xml.iter(W + "p")
    assert descriptions(para) == [tex] and text_of(para) == "Before  after."


def test_display_in_footnote_and_inline_in_markdown_table(tmp_path):
    needs_tex("mhchem")
    src = tmp_path / "notes.md"
    src.write_text("Some text.[^1]\n\n| Formula |\n| --- |\n| Water $\\ce{H2O}$ here |\n\n"
                   "[^1]: Before $$\\ce{CO2}$$ after.\n", encoding="utf-8")
    docx = src.with_suffix(".docx")
    notes = document_to_word(src, docx)
    xml, pngs = pictures(docx)
    assert len(pngs) == 2
    assert any(descriptions(cell) == [r"\ce{H2O}"] for cell in xml.iter(W + "tc"))
    footnotes, _ = pictures(docx, "footnotes")
    para, = [p for p in footnotes.iter(W + "p") if descriptions(p)]
    assert descriptions(para) == [r"\ce{CO2}"] and text_of(para) == ""
    assert "Before" in text_of(footnotes) and "after." in text_of(footnotes)
    assert any("2 equations" in n and "pictures" in n and "edited" in n for n in notes)


def test_no_latex_keeps_text_and_explains_once(tmp_path, monkeypatch):
    monkeypatch.setattr(preview, "tools", lambda: None)
    docx = tmp_path / "p.docx"
    notes = passage_to_word(r"$\bogus{x}$ and $\bogus{y}$", docx)
    xml, pngs = pictures(docx)
    assert not pngs and text_of(xml).count("[not converted: texmath does not know \\bogus]") == 2
    assert r"\bogus{x}" in text_of(xml) and r"\bogus{y}" in text_of(xml)
    assert sum("picture" in n.lower() and "latex" in n.lower() for n in notes) == 1


@pytest.mark.parametrize("passage", [True, False])
def test_no_pictures_switch(tmp_path, monkeypatch, passage):
    monkeypatch.setattr(preview, "tools", lambda: pytest.fail("disabled pictures must not probe LaTeX"))
    docx = tmp_path / "p.docx"
    tex = r"$\ce{H2O}$"
    args = ["-e", tex] if passage else [str(paper(tmp_path, tex))]
    assert main(["word", *args, "-o", str(docx), "--no-pictures"]) == 0
    xml, pngs = pictures(docx)
    assert not pngs and "[not converted:" in text_of(xml) and tex in text_of(xml)


def test_drawing_error_leaves_text_and_reason(tmp_path):
    needs_tex()
    docx = tmp_path / "p.docx"
    notes = passage_to_word(r"$\undefinedMathShuttle{x}$", docx)
    xml, pngs = pictures(docx)
    assert not pngs and "[not converted:" in text_of(xml) and r"\undefinedMathShuttle{x}" in text_of(xml)
    assert any("picture" in n.lower() and "undefined" in n.lower() for n in notes)


def test_units_request_mhchem(monkeypatch):
    monkeypatch.setattr(preview, "installed", lambda name: name == "mhchem")
    assert r"\usepackage{mhchem}" in preview._picture_preamble(r"\pu{25 kg}", [], "")


@pytest.mark.parametrize("source", ["tex", "md", "passage"])
def test_package_options_and_scoped_macros(tmp_path, source):
    needs_tex("xcolor", "mhchem")
    docx = tmp_path / "p.docx"
    content = (r"\usepackage[dvipsnames]{xcolor}" "\n\n"
               r"\newcommand{\water}{\textcolor{ForestGreen}{\ce{H2O}}}" "\n\n"
               r"First $\water$." "\n\n" r"\renewcommand{\water}{\ce{CH3COOH}}" "\n\n" r"Second $\water$.")
    if source == "tex":
        document_to_word(paper(tmp_path, content.split("\n\n", 1)[1], content.split("\n\n", 1)[0]), docx)
    elif source == "md":
        src = tmp_path / "p.md"
        src.write_text(content)
        document_to_word(src, docx)
    else:
        passage_to_word(content, docx)
    xml, pngs = pictures(docx)
    assert descriptions(xml) == [r"\water", r"\water"] and len(pngs) == 2
    first, second = [int(e.get("cx")) for e in xml.iter(WP + "extent")]
    assert second > first * 1.5


def test_compare_keeps_equation_numbers_across_a_picture(tmp_path, monkeypatch):
    # The bookmark/compare path is testable even on a machine without TeX.
    import base64
    png = tmp_path / "eq.png"
    png.write_bytes(base64.b64decode("iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAIAAACQd1PeAAAADElEQVR4nGNgYGAAAAAE"
                                     "AAH2FzhVAAAAAElFTkSuQmCC"))
    monkeypatch.setattr(preview, "render_picture", lambda *a, **kw: (str(png), 0.1, 0.1))
    src = paper(tmp_path, r"$a=1$, $\ce{H2O}$, $b=2$.")
    docx = src.with_suffix(".docx")
    document_to_word(src, docx)
    xml, _ = pictures(docx)
    assert [b.get(W + "name") for b in xml.iter(W + "bookmarkStart")] == ["_MSEq1", "_MSEq2", "_MSEq3"]
    with zipfile.ZipFile(docx) as z:
        records = ET.fromstring(z.read("customXml/item1.xml"))
    assert [r.get("tex") for r in records] == ["$a=1$", r"$\ce{H2O}$", "$b=2$"]
    assert records[1].get("picture") == "1"
    rows = compare(docx, src).rows
    assert [(r.original.n, r.status) for r in rows] == [(1, "unchanged"), (3, "unchanged")]
    # A Word editor can strip custom XML and keep the bookmarks. Comparing on a
    # machine without LaTeX must still honour the numbering in the returned file.
    returned = tmp_path / "returned.docx"
    with zipfile.ZipFile(docx) as original, zipfile.ZipFile(returned, "w") as saved:
        for item in original.infolist():
            if not item.filename.startswith("customXml/"):
                saved.writestr(item, original.read(item))
    monkeypatch.setattr(preview, "render_picture", lambda *a, **kw: pytest.fail("compare should not need LaTeX"))
    rows = compare(returned, src).rows
    assert [(r.original.n, r.status) for r in rows] == [(1, "unchanged"), (3, "unchanged")]


def test_render_cache_uses_definitions_options_and_cleans_up(tmp_path):
    needs_tex("mhchem", "xcolor")
    tex = r"\textcolor{ForestGreen}{\chem}"
    packages = [r"\usepackage[dvipsnames]{xcolor}", r"\usepackage{not-a-real-package}"]
    definitions = [r"\newcommand{\chem}{\ce{H2O}}"]
    first = preview.render_picture(tex, False, definitions, packages, work=str(tmp_path))
    assert first == preview.render_picture(tex, False, definitions, packages, work=str(tmp_path))
    assert list(tmp_path.iterdir()) == [Path(first[0])]
    second = preview.render_picture(tex, False, [r"\newcommand{\chem}{\ce{CH3COOH}}"], packages, work=str(tmp_path))
    assert second[0] != first[0] and second[1] > first[1]


def test_picture_cannot_input_another_file(tmp_path):
    needs_tex()
    local = tmp_path / "outside.tex"
    local.write_text("ABC", encoding="ascii")
    with pytest.raises(preview.PreviewError):
        preview.render_picture(r"\target", False, [r"\newcommand{\target}{\input{" + local.as_posix() + "}}"],
                               work=str(tmp_path))
    assert list(tmp_path.iterdir()) == [local]



def test_display_picture_is_centred_and_numbered(tmp_path):
    # a numbered display picture: a centre tab before it, a right tab and its number after it (raised to
    # its middle), as Word sets a numbered equation; the next native equation keeps the following number
    needs_tex("tikz-cd")
    body = ("\\begin{equation}\n" + DIAGRAM.replace("\\Obj", "A") + "\n\\label{d}\n\\end{equation}\n"
            "and\n\\begin{equation}\nE = mc^2\n\\end{equation}")
    source = paper(tmp_path, body, "\\usepackage{tikz-cd}")
    out = tmp_path / "paper.docx"
    document_to_word(str(source), str(out))
    xml, pngs = pictures(out)
    assert len(pngs) == 1
    para = next(p for p in xml.iter(W + "p") if list(p.iter(WP + "docPr")))
    tabs = [t.get(W + "val") for t in para.iter(W + "tab") if t.get(W + "val")]
    assert tabs == ["center", "right"]
    assert text_of(para).strip() == "(1)"
    assert any(r.find(W + "rPr/" + W + "position") is not None for r in para.iter(W + "r"))
    names = [b.get(W + "name", "") for b in para.iter(W + "bookmarkStart")]
    assert all(n.startswith("_MSEq") for n in names)  # the layout's own bookmark is gone
    with zipfile.ZipFile(out) as z:
        assert "#(2)" in z.read("word/document.xml").decode("utf-8")
