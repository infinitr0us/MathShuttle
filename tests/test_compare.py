"""Compare actual Word revisions and source copies made from a fresh export."""
import re
import shutil
import xml.etree.ElementTree as ET
import zipfile
from pathlib import Path

import pytest

from mathshuttle import __version__, documents
from mathshuttle.__main__ import main
from mathshuttle.compare import compare, write_corrected
from mathshuttle.pandoc_engine import ConversionError
from mathshuttle.wordexport import document_to_word, passage_to_word


FIX = Path(__file__).parent / "fixtures" / "word_roundtrip"
PAPER = FIX / "paper.tex"
W = "{%s}" % documents.WML_NS
M = "{%s}" % documents.OMML_NS


def compact(tex):
    return re.sub(r"\^\{(\d)\}", r"^\1", re.sub(r"\s+", "", tex))


def originals(result):
    return {r.original.n: r for r in result.rows if r.original}


def test_word_edits(tmp_path):
    result = compare(FIX / "returned-edits.docx", PAPER)
    rows = originals(result)
    assert [rows[i].status for i in range(1, 6)] == [
        "unchanged", "changed", "unchanged", "changed", "unchanged"]
    assert rows[2].original.number == "(2)"
    assert rows[2].original.labels == ["eq:grad"] and rows[2].original.section == "Model"
    assert r"\frac{4}{N}" in rows[2].returned
    assert [(r.author, r.date) for r in rows[2].revisions] == [("A. Coauthor", "2026-11-28T00:52:00Z")]
    assert r"\R^n" in rows[3].original.tex
    assert compact(rows[4].returned) == "a=b-c"
    assert [compact(r.returned) for r in result.rows if r.status == "added"] == ["x^2+y^2=z^2"]
    out = tmp_path / "edited.tex"
    notes = write_corrected(result, out)
    text = out.read_text(encoding="utf-8")
    assert r"\frac{4}{N}" in text and r"\label{eq:grad}" in text
    assert re.search(r"\\begin{align}\s*a\s*=\s*b\s*-\s*c\s*\\label{eq:first}\s*\\end{align}", text)
    assert any("eq:second" in note for note in notes)
    assert text.index(r"\end{align}") < text.index("% MathShuttle: added") < text.index("and finally")
    assert out.with_suffix(".diff").read_text().startswith("--- ")


def test_word_deletion_and_comment(tmp_path):
    returned = FIX / "returned-deleted-comment.docx"
    result = compare(returned, PAPER)
    rows = originals(result)
    assert [rows[i].status for i in range(1, 6)] == ["unchanged"] * 4 + ["deleted"]
    assert [(c.author, c.text) for c in rows[1].comments] == [("A. Coauthor", "Should this be 1/(2N)?")]
    assert all(not r.comments for r in result.rows if r is not rows[1])
    assert rows[5].returned == "" and rows[5].revisions[0].author == "A. Coauthor"
    assert [compact(r.returned) for r in result.rows if r.status == "added"] == ["a^2+b^2=c^2"]
    out = tmp_path / "deleted.tex"
    write_corrected(result, out)
    text = out.read_text()
    assert f"% MathShuttle: deleted in {returned.name} (by A. Coauthor, 2026-11-28T00:50:00Z)" in text
    assert rows[5].original.tex.encode("utf-8") in out.read_bytes()
    assert any("a^2+b^2=c^2" in compact(line) for line in text.splitlines() if line.startswith("%"))


def test_numbered_equations_read_as_maths():
    for name in ("exported.docx", "returned-deleted-comment.docx"):
        found = documents.read_document(FIX / name).found
        assert found[0].numbers == ["(1)"] and found[1].numbers == ["(2)"]
        assert all(r"\#" not in f.equation.tex and r"\begin{array}" not in f.equation.tex for f in found[:4])
        assert found[3].numbers == ["(3)", "(4)"] and r"\begin{aligned}" in found[3].equation.tex


def export(folder, body, preamble=""):
    paper = folder / "paper.tex"
    paper.write_text("\\documentclass{article}\n" + preamble + "\n\\begin{document}\n" + body +
                     "\n\\end{document}\n", encoding="utf-8")
    docx = folder / "paper.docx"
    document_to_word(paper, docx)
    return paper, docx


def edit(docx, change, legacy=False):
    out = docx.with_name("returned.docx")
    with zipfile.ZipFile(docx) as src, zipfile.ZipFile(out, "w") as dst:
        for info in src.infolist():
            data = src.read(info)
            if info.filename == "word/document.xml":
                root = ET.fromstring(data)
                change(root)
                data = ET.tostring(root)
            if not (legacy and info.filename.startswith("customXml/")):
                dst.writestr(info, data)
    return out


def change_text(root, old, new):
    next(t for t in root.iter(M + "t") if t.text == old).text = new


def remove_marks(root):
    ids = {e.get(W + "id") for e in root.iter(W + "bookmarkStart") if e.get(W + "name").startswith("_MSEq")}
    for parent in root.iter():
        for e in list(parent):
            if e.tag in (W + "bookmarkStart", W + "bookmarkEnd") and e.get(W + "id") in ids:
                parent.remove(e)


def test_export_parts_and_passage(tmp_path):
    paper, docx = export(tmp_path, r"Value $x\in\R$.\footnote{Here $y$.} Then $z$.", r"\newcommand{\R}{\mathbb{R}}")
    with zipfile.ZipFile(docx) as z:
        assert z.testzip() is None
        manifest = ET.fromstring(z.read("customXml/item1.xml"))
        assert manifest.tag == "{urn:mathshuttle:equations}equations"
        assert manifest.get("source") == paper.name and manifest.get("version") == __version__
        assert [e.get("tex") for e in manifest] == [r"$x\in\R$", "$z$", "$y$"]
        assert all(e.get("key") for e in manifest)
        for part, names in [("document", ["_MSEq1", "_MSEq2"]), ("footnotes", ["_MSEq3"])]:
            xml = ET.fromstring(z.read(f"word/{part}.xml"))
            assert [b.get(W + "name") for b in xml.iter(W + "bookmarkStart")] == names
            assert {b.get(W + "id") for b in xml.iter(W + "bookmarkStart")} == {
                b.get(W + "id") for b in xml.iter(W + "bookmarkEnd")}
        assert ("rIdMS1", documents.R_NS + "/customXml", "customXml/item1.xml") in documents._targets(z, "word/document.xml")
        assert documents._targets(z, "customXml/item1.xml")[0][2] == "customXml/itemProps1.xml"
        assert b"/customXml/itemProps1.xml" in z.read("[Content_Types].xml")
    assert all(r.status == "unchanged" for r in compare(docx, paper).rows)
    passage_to_word("$x$", docx)
    with zipfile.ZipFile(docx) as z:
        assert b"_MSEq" not in z.read("word/document.xml") and "customXml/item1.xml" not in z.namelist()


@pytest.mark.parametrize("legacy", [True, False])
def test_missing_marks_order_and_revisions(tmp_path, legacy):
    paper, docx = export(tmp_path, r"$a=1$, $b=2$, $c=3$.")

    def change(root):
        remove_marks(root)
        run = next(r for r in root.iter(M + "r") if r.findtext(M + "t") == "2")
        run.remove(run.find(M + "t"))
        for tag, value in [("del", "2"), ("ins", "4")]:
            revision = ET.SubElement(run, W + tag, {W + "author": "Jo", W + "date": "2026-01-01"})
            ET.SubElement(revision, M + "t").text = value

    result = compare(edit(docx, change, legacy), paper)
    assert [r.status for r in result.rows] == ["unchanged", "changed", "unchanged"]
    assert compact(result.rows[1].returned) == "b=4" and result.rows[1].revisions[0].author == "Jo"


def test_moved_bookmarks_and_duplicate_occurrence(tmp_path):
    paper, docx = export(tmp_path, "One $x$.\n\nTwo $x$.\n\nThree $z$.")

    def change(root):
        body = root.find(W + "body")
        first, second, third = body.findall(W + "p")
        change_text(second, "x", "q")
        body.remove(second)
        body.insert(0, second)

    result = compare(edit(docx, change), paper)
    assert originals(result)[2].status == "changed" and originals(result)[1].status == "unchanged"
    out = tmp_path / "out.tex"
    write_corrected(result, out)
    assert "One $x$.\n\nTwo $q$." in out.read_text()


def test_source_and_input_copy_and_macro_tokens(tmp_path):
    sub = tmp_path / "section.tex"
    sub.write_text(r"Here \( x \in \R^2 \)." + "\n")
    paper, docx = export(tmp_path, "\\section{Bounds}\n\\input{section}", r"\newcommand{\R}{\mathbb{R}}")
    result = compare(edit(docx, lambda root: change_text(root, "2", "3")), paper)
    assert result.rows[0].original.file == "section.tex"
    sub.write_text(r"Here \( x  \in   \R^2 \)." + "\n")
    out = tmp_path / "corrected.tex"
    assert not write_corrected(result, out)
    target = re.search(r"\\input\{([^}]+)\}", out.read_text())[1]
    copied = tmp_path / target
    assert copied != sub and compact(copied.read_text()).startswith(r"Here\(x\in\R^3\)")
    assert "^2" in sub.read_text() and r"\input{section}" in paper.read_text()
    assert "section.tex" in out.with_suffix(".diff").read_text()
    with pytest.raises(ConversionError):
        write_corrected(result, paper)
    with pytest.raises(ConversionError):
        write_corrected(result, sub)


def test_paper_changed_since_export(tmp_path):
    paper, docx = export(tmp_path, "$a=1$ and $b=2$.")
    returned = edit(docx, lambda root: change_text(root, "2", "4"))
    paper.write_text(paper.read_text().replace("$b=2$", "$c=3$"))
    result = compare(returned, paper)
    assert result.rows[1].original.tex == "$b=2$"
    out = tmp_path / "corrected.tex"
    assert any("not found" in n.lower() for n in write_corrected(result, out))
    assert out.read_bytes() == paper.read_bytes()


def test_labels_stay_on_alignment_rows(tmp_path):
    paper, docx = export(tmp_path, r"\begin{align} a&=1\label{one}\\ b&=2\label{two}\end{align}")
    result = compare(edit(docx, lambda root: change_text(root, "2", "4")), paper)
    out = tmp_path / "corrected.tex"
    write_corrected(result, out)
    assert r"a&=1\label{one}\\b&=4\label{two}" in compact(out.read_text())


def test_cli(tmp_path, capsys):
    paper = tmp_path / "paper.tex"
    shutil.copyfile(PAPER, paper)
    argv = ["compare", str(FIX / "returned-deleted-comment.docx"), str(paper)]
    assert main(argv + ["-o", str(tmp_path / "corrected.tex")]) == 0
    output = capsys.readouterr().out
    assert "deleted" in output and "eq:energy" in output and "Bounds" in output and "added" in output
    # an unchanged equation is shown only when it has a comment: (1) yes, the inline one (3) not
    assert "unchanged (1) eq:loss" in output and "Should this be 1/(2N)?" in output
    assert output.count("unchanged") == 1
    assert main(argv + ["--all"]) == 0
    assert capsys.readouterr().out.count("unchanged") >= 2


def test_macro_boundaries_and_longest_expansion(tmp_path):
    from mathshuttle.texsource import SourceTree, _macros, _restore_macros
    macros = [(r"\mathbb{R}^{2}", r"\Plane"), (r"\mathbb{R}", r"\R"), (r"\alpha", r"\A")]
    assert _restore_macros(r"\mathbb{R}^{2}+\mathbb{R}x+\alphabet+\alpha", macros) == r"\Plane+\R x+\alphabet+\A"
    paper = tmp_path / "defs.tex"
    paper.write_text(r"\newcommand{\Space}[1]{\mathbb{#1}}\newcommand{\R}[0]{\Space{R}}")
    assert _macros(SourceTree(paper), None) == [(r"\mathbb{R}", r"\R")]


def test_missing_one_bookmark_and_inserted_neighbour(tmp_path):
    paper, docx = export(tmp_path, r"$x=1$, $y=2$, $z=3$.")

    def change(root):
        para = root.find(".//" + W + "p")
        start = next(e for e in para if e.get(W + "name") == "_MSEq2")
        end = next(e for e in para if e.tag == W + "bookmarkEnd" and e.get(W + "id") == start.get(W + "id"))
        para.remove(start)
        para.remove(end)
        change_text(para, "2", "9")
        before = next(e for e in para if e.get(W + "name") == "_MSEq3")
        new = ET.Element(M + "oMath")
        run = ET.SubElement(new, M + "r")
        rev = ET.SubElement(run, W + "ins", {W + "author": "Sam"})
        ET.SubElement(rev, M + "t").text = "q"
        para.insert(list(para).index(before) + 1, new)

    result = compare(edit(docx, change), paper)
    assert [r.status for r in result.rows] == ["unchanged", "changed", "added", "unchanged"]
    assert result.rows[2].after == 2 and result.rows[2].revisions[0].author == "Sam"


def test_occurrence_ignores_prose_comments_and_display_dollars(tmp_path):
    paper, docx = export(tmp_path, "% $x$ is an example\n$a$ x $b$.\n\n$$x$$\n\nActual $x$.")
    def change(root):
        list(root.iter(M + "t"))[-1].text = "y"
    result = compare(edit(docx, change), paper)
    assert result.rows[-1].original.count == 1
    out = tmp_path / "copy.tex"
    write_corrected(result, out)
    assert "% $x$ is an example\n$a$ x $b$.\n\n$$x$$\n\nActual $y$." in out.read_text()


def test_added_before_first_equation_or_in_empty_paper(tmp_path):
    for i, body in enumerate(("One $a$.", "No equations.")):
        paper, docx = export(tmp_path, body)

        def change(root):
            new = ET.Element(M + "oMath")
            ET.SubElement(ET.SubElement(new, M + "r"), M + "t").text = "q"
            para = root.find(".//" + W + "p")
            para.insert(1, new)

        result = compare(edit(docx, change), paper)
        assert result.rows[0].status == "added" and result.rows[0].after is None
        out = tmp_path / ("copy%d.tex" % i)
        write_corrected(result, out)
        text = out.read_text()
        assert text.index("% $q$") < text.index("$a$" if "$a$" in body else r"\end{document}")


def test_correction_preserves_same_line_prose_and_nested_input(tmp_path):
    (tmp_path / "chapter.tex").write_text("Chapter.\\input{part} End.")
    (tmp_path / "part.tex").write_text("Text $x=2$.")
    paper, docx = export(tmp_path, r"See \input{chapter}.")
    result = compare(edit(docx, lambda root: change_text(root, "2", "3")), paper)
    out = tmp_path / "edits" / "corrected.tex"
    write_corrected(result, out)
    main = out.read_text()
    chapter = (out.parent / re.search(r"\\input\{([^}]+)\}", main)[1]).read_text()
    part = (out.parent / re.search(r"\\input\{([^}]+)\}", chapter)[1]).read_text()
    assert compact(part) == "Text$x=3$."
    assert (tmp_path / "part.tex").read_text() == "Text $x=2$."


def test_whole_equation_deleted_by_ancestor(tmp_path):
    paper, docx = export(tmp_path, r"One $x$, two $y$.")

    def change(root):
        para = root.find(".//" + W + "p")
        math = para.find(M + "oMath")
        index = list(para).index(math)
        para.remove(math)
        deleted = ET.Element(W + "del", {W + "author": "Jo"})
        deleted.append(math)
        para.insert(index, deleted)

    rows = compare(edit(docx, change), paper).rows
    assert [r.status for r in rows] == ["deleted", "unchanged"]
    assert rows[0].revisions[0].author == "Jo"
