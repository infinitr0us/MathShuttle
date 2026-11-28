"""Regression cases for comparing a co-author's Word edits with the paper."""
import base64
import copy
import re
import xml.etree.ElementTree as ET
import zipfile
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from mathshuttle import documents, preview, wordmarks
from mathshuttle.__main__ import main
from mathshuttle.app import CompareWindow
from mathshuttle.compare import CompareRow, _pieces, compare, write_corrected
from mathshuttle.pandoc_engine import ConversionError
from mathshuttle.texsource import Original
from test_compare import FIX, M, PAPER, W, change_text, compact, edit, export, remove_marks


def corrected(folder, paper, returned):
    result = compare(returned, paper)
    target = folder / "corrected.tex"
    notes = write_corrected(result, target)
    return target.read_text(encoding="utf-8"), notes


def math_text(value):
    math = ET.Element(M + "oMath")
    ET.SubElement(ET.SubElement(math, M + "r"), M + "t").text = value
    return math


def with_comments(docx):
    out = docx.with_name("comments.docx")
    with zipfile.ZipFile(docx) as src, zipfile.ZipFile(out, "w") as dst:
        for item in src.infolist():
            if item.filename == "word/comments.xml":
                continue
            data = src.read(item)
            if item.filename == "word/_rels/document.xml.rels":
                rels = ET.fromstring(data)
                if not any(e.get("Type", "").endswith("/comments") for e in rels):
                    ET.SubElement(rels, "{%s}Relationship" % documents.REL_NS, Id="review",
                                  Type=documents.R_NS + "/comments", Target="comments.xml")
                data = ET.tostring(rels)
            dst.writestr(item, data)
        comments = ET.Element(W + "comments")
        comment = ET.SubElement(comments, W + "comment", {W + "id": "50", W + "author": "Jo"})
        ET.SubElement(ET.SubElement(ET.SubElement(comment, W + "p"), W + "r"), W + "t").text = "Check this"
        dst.writestr("word/comments.xml", ET.tostring(comments))
    return out


def test_split_function_runs(tmp_path):
    paper, docx = export(tmp_path, r"$\sin(x)$")

    def split(root):
        fn = next(parent for parent in root.iter()
                  if any(run.findtext(M + "t") == "sin" for run in parent.findall(M + "r")))
        run = next(run for run in fn if run.findtext(M + "t") == "sin")
        index = list(fn).index(run)
        fn.remove(run)
        for char in reversed("sin"):
            piece = copy.deepcopy(run)
            piece.find(M + "t").text = char
            fn.insert(index, piece)

    returned = edit(docx, split)
    assert documents.read_docx(returned).found[0].equation.tex.startswith(r"\sin")
    assert compare(returned, paper).rows[0].status == "unchanged"


def test_control_word_keys(tmp_path):
    paper, docx = export(tmp_path, r"Compare $\sin h(x)$ and $\sinh(x)$.")

    def change(root):
        first, second = root.iter(M + "oMath")
        first[:] = list(second)

    assert compare(edit(docx, change), paper).rows[0].status == "changed"
    assert wordmarks.key(r"\sin   h (x)") == wordmarks.key(r"\sin h(x)")
    assert wordmarks.key(r"a\ b") != wordmarks.key(r"a\b")


def test_control_word_in_older_manifest(tmp_path):
    paper, docx = export(tmp_path, r"Compare $\sin h(x)$ and $\sinh(x)$.")
    older = tmp_path / "older.docx"
    with zipfile.ZipFile(docx) as src, zipfile.ZipFile(older, "w") as dst:
        for item in src.infolist():
            data = src.read(item)
            if item.filename == "customXml/item1.xml":
                part = ET.fromstring(data)
                part.attrib.pop("files")
                part[0].set("key", part[0].get("key").replace(" ", ""))
                data = ET.tostring(part)
            dst.writestr(item, data)

    def change(root):
        first, second = root.iter(M + "oMath")
        first[:] = list(second)

    assert compare(older, paper).rows[0].status == "unchanged"
    assert compare(edit(older, change), paper).rows[0].status == "changed"


def test_starred_tag_needs_matching_manifest(tmp_path):
    paper, docx = export(tmp_path, r"\begin{align}n&=\#S\notag\\x&=1\tag*{A}\end{align}")
    for row in documents.read_docx(docx).found:
        assert r"\#" in row.equation.tex and row.numbers == ["A"]
    assert r"\#" in compare(docx, paper).rows[0].returned


@pytest.mark.parametrize("unreadable", [False, True])
def test_one_bad_equation(tmp_path, unreadable, monkeypatch):
    paper, docx = export(tmp_path, "$x$ then $y$.")
    returned = edit(docx, lambda root: change_text(root, "x", "bad" if unreadable else "   "))
    if unreadable:
        read = documents._read_fragments

        def read_one_bad(fragments):
            return [None if any(t.text == "bad" for t in f.iter(M + "t")) else value
                    for f, value in zip(fragments, read(fragments))]

        monkeypatch.setattr(documents, "_read_fragments", read_one_bad)
    result = compare(returned, paper)
    assert result.rows[1].status == "unchanged"
    if unreadable:
        assert "could not be read" in result.rows[0].note.lower()
        text, notes = corrected(tmp_path, paper, returned)
        assert notes and text == paper.read_text()
    else:
        assert result.rows[0].status == "deleted"


def test_markers_in_shift_enter_paragraph(tmp_path):
    paper, docx = export(tmp_path, "$x$. Then $y$.")

    def change(root):
        para = root.find(".//" + W + "p")
        start, end = para.find(W + "bookmarkStart"), para.find(W + "bookmarkEnd")
        first = para.find(M + "oMath")
        index = list(para).index(start)
        for e in (start, first, end):
            para.remove(e)
        lines = ET.Element(M + "oMathPara")
        props = ET.Element(M + "rPr")
        ET.SubElement(props, M + "aln")
        first.find(M + "r").insert(0, props)
        first.insert(0, ET.Element(W + "commentRangeStart", {W + "id": "50"}))
        lines.extend([start, first, math_text("z"), ET.Element(W + "commentRangeEnd", {W + "id": "50"}), end])
        para.insert(index, lines)

    rows = compare(with_comments(edit(docx, change)), paper).rows
    assert [(r.original.n, r.status, len(r.comments)) for r in rows] == [(1, "changed", 1), (2, "unchanged", 0)]


def test_combined_lines_with_two_bookmarks(tmp_path):
    paper, docx = export(tmp_path, "$x$ and $y$.")

    def change(root):
        para = root.find(".//" + W + "p")
        lines = ET.Element(M + "oMathPara")
        for child in list(para):
            if child.tag in (W + "bookmarkStart", W + "bookmarkEnd", M + "oMath"):
                para.remove(child)
                lines.append(child)
        run = lines.find(M + "oMath/" + M + "r")
        props = ET.Element(M + "rPr")
        ET.SubElement(props, M + "aln")
        run.insert(0, props)
        para.append(lines)

    text, notes = corrected(tmp_path, paper, edit(docx, change))
    assert notes and text == paper.read_text()


def test_paragraph_author(tmp_path):
    paper, docx = export(tmp_path, "$a$ and $b$.")

    def change(root):
        para = root.find(".//" + W + "p")
        props = ET.SubElement(para, W + "pPr") if para.find(W + "pPr") is None else para.find(W + "pPr")
        ET.SubElement(ET.SubElement(props, W + "rPr"), W + "ins", {W + "author": "Jo"})
        change_text(root, "b", "c")

    assert all(not r.revisions for r in compare(edit(docx, change), paper).rows)


@pytest.mark.parametrize("unknown_bookmark", [False, True])
def test_bookmark_bounds_repeated_equations(tmp_path, unknown_bookmark):
    paper, docx = export(tmp_path, "First $x=1$.\n\nMiddle $z$.\n\nLast $x=1$.")

    def change(root):
        body = root.find(W + "body")
        first, middle, last = body.findall(W + "p")
        body.remove(first)
        if unknown_bookmark:
            last.find(W + "bookmarkStart").set(W + "name", "_MSEq99")
        else:
            remove_marks(last)
        change_text(last, "1", "2")

    returned = edit(docx, change)
    rows = compare(returned, paper).rows
    assert [(r.original.n, r.status) for r in rows] == [(1, "deleted"), (2, "unchanged"), (3, "changed")]
    assert "Last$x=2$." in compact(corrected(tmp_path, paper, returned)[0])


def test_tracked_move(tmp_path):
    paper, docx = export(tmp_path, "First $x=1$.\n\nLast $y$.")

    def change(root):
        first, last = root.find(W + "body").findall(W + "p")
        math = first.find(M + "oMath")
        index = list(first).index(math)
        first.remove(math)
        old = ET.Element(W + "moveFrom", {W + "author": "Jo", W + "id": "12"})
        old.append(math)
        first.insert(index, old)
        ET.SubElement(last, W + "moveTo", {W + "author": "Jo", W + "id": "13"}).append(copy.deepcopy(math))

    returned = edit(docx, change)
    rows = compare(returned, paper).rows
    assert sorted(r.status for r in rows) == ["moved", "unchanged"]
    moved = next(r for r in rows if r.status == "moved")
    assert moved.original.n == 1 and moved.after == 2 and "moved to after (2)" in moved.note
    text, _ = corrected(tmp_path, paper, returned)
    assert "% MathShuttle: moved in returned.docx to after (2)\nFirst $x=1$." in text


@pytest.mark.parametrize("deleted", [False, True])
def test_picture_review(tmp_path, monkeypatch, deleted):
    png = tmp_path / "image.png"
    png.write_bytes(base64.b64decode("iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAIAAACQd1PeAAAADElEQVR4nGNg"
                                     "YGAAAAAEAAH2FzhVAAAAAElFTkSuQmCC"))
    monkeypatch.setattr(preview, "render_picture", lambda *a, **k: (str(png), 1, 0.25))
    paper, docx = export(tmp_path, r"Water $\ce{H2O}$ and $x$.")

    def change(root):
        para = root.find(".//" + W + "p")
        picture = next(run for run in para if run.find(".//" + W + "drawing") is not None)
        if deleted:
            para.remove(picture)
        else:
            index = list(para).index(picture)
            para.insert(index, ET.Element(W + "commentRangeStart", {W + "id": "50"}))
            para.insert(index + 2, ET.Element(W + "commentRangeEnd", {W + "id": "50"}))

    row = compare(with_comments(edit(docx, change)), paper).rows[0]
    assert row.original.tex == r"$\ce{H2O}$" and row.status == ("deleted" if deleted else "commented")
    if not deleted:
        assert row.comments[0].text == "Check this"


def test_linear_formula_beside_deleted_number():
    math = math_text("a+b")
    array = ET.SubElement(math, M + "eqArr")
    props = ET.SubElement(ET.SubElement(array, M + "eqArrPr"), M + "ctrlPr")
    ET.SubElement(props, W + "del")
    run = ET.SubElement(ET.SubElement(array, M + "e"), M + "r")
    ET.SubElement(ET.SubElement(run, W + "del"), M + "t").text = "x#(1)"
    assert len(_pieces(math, at_boundary=True)) == 2


def test_linear_addition_in_a_returned_word_file(tmp_path):
    path = tmp_path / "word.docx"
    path.write_bytes((FIX / "returned-deleted-comment.docx").read_bytes())

    def change(root):
        math = next(m for m in root.iter(M + "oMath")
                    if m.find(M + "eqArr/" + M + "eqArrPr/" + M + "ctrlPr/" + W + "del") is not None)
        array = math.find(M + "eqArr")
        math[:] = [math_text("a+b")[0], array]

    result = compare(edit(path, change), PAPER)
    assert [compact(r.returned) for r in result.rows if r.status == "added"] == ["a+b"]
    assert next(r for r in result.rows if r.original and r.original.n == 5).status == "deleted"


@pytest.mark.parametrize("revision", ["ins", "del"])
def test_tracked_exponent_read_and_compare(tmp_path, revision):
    paper, docx = export(tmp_path, "$x^2$ and $y=1$.")

    def change(root):
        run = root.find(".//" + M + "sup/" + M + "r")
        text = run.find(M + "t")
        run.remove(text)
        ET.SubElement(run, W + revision, {W + "author": "Jo"}).append(text)
        change_text(root, "1", "3")

    returned = edit(docx, change)
    doc = documents.read_docx(returned)
    assert len(doc.found) == 2 and not doc.notes
    first, second = compare(returned, paper).rows
    assert first.revisions[0].author == "Jo" and first.status != "unreadable"
    assert second.status == "changed" and compact(second.returned) == "y=3"


@pytest.mark.parametrize("body", [r"\ensuremath{x}", "$x$", r"\(x\)"])
def test_prose_macro_and_literal(tmp_path, body):
    paper, docx = export(tmp_path, r"Here \withx. Next $x$.", r"\newcommand{\withx}{%s}" % body)
    returned = edit(docx, lambda root: change_text(root, "x", "y"))
    text, notes = corrected(tmp_path, paper, returned)
    assert text == paper.read_text()
    assert any(r"made by the macro \withx" in note and "change the macro" in note for note in notes)
    assert compare(returned, paper).rows[1].original.occurrence == 1


def test_title_abstract_body_positions(tmp_path):
    paper, docx = export(tmp_path, r"\maketitle \begin{abstract}Summary $y$.\end{abstract} Body $x=1$.",
                         r"\title{Title $x=1$}")

    def change(root):
        body = next(p for p in root.iter(W + "p") if "Body" in "".join(p.itertext()))
        change_text(body, "1", "2")

    text, _ = corrected(tmp_path, paper, edit(docx, change))
    assert r"\title{Title$x=1$}" in compact(text) and "Body$x=2$." in compact(text)


def test_changed_file_same_duplicate_count(tmp_path):
    paper, docx = export(tmp_path, "A $x$.\n\nB $x$.\n\nC $y$.")
    returned = edit(docx, lambda root: setattr(list(root.iter(M + "t"))[1], "text", "q"))
    paper.write_text(paper.read_text().replace("A $x$", "A $y$").replace("C $y$", "C $x$"))
    text, notes = corrected(tmp_path, paper, returned)
    assert "C $x$" in text
    assert ("B $q$" in text) or (notes and text == paper.read_text())


def test_identical_old_context_after_one_disappears(tmp_path):
    paragraph = "Before " * 8 + "$x$" + " after" * 8
    paper, docx = export(tmp_path, paragraph + "\n\n" + paragraph)
    def change(root):
        for t in root.iter(M + "t"):
            if t.text == "x":
                t.text = "q"

    returned = edit(docx, change)
    paper.write_text(paper.read_text().replace("$x$", "$y$", 1))
    text, notes = corrected(tmp_path, paper, returned)
    assert notes and text == paper.read_text()


def test_ambiguous_unbookmarked_repeats(tmp_path):
    paper, docx = export(tmp_path, "One $x=1$.\n\nTwo $x=1$.")

    def change(root):
        first, last = root.find(W + "body").findall(W + "p")
        root.find(W + "body").remove(first)
        remove_marks(last)
        change_text(last, "1", "2")

    text, notes = corrected(tmp_path, paper, edit(docx, change))
    assert notes and text == paper.read_text()


def test_renamed_paper(tmp_path):
    paper, docx = export(tmp_path, "Here $x=1$.")
    renamed = paper.rename(tmp_path / "draft.tex")
    text, notes = corrected(tmp_path, renamed, edit(docx, lambda r: change_text(r, "1", "2")))
    assert not notes and "Here$x=2$." in compact(text)


def test_unchanged_missing_source(tmp_path):
    paper, docx = export(tmp_path, "Here $x$.")
    paper.write_text(paper.read_text().replace("$x$", "nothing"))
    text, notes = corrected(tmp_path, paper, docx)
    assert not notes and text == paper.read_text()


@pytest.mark.parametrize("env", ["gather", "multline", "gather*", "multline*"])
def test_keep_gather_multline_rows(tmp_path, env):
    first = "" if env.startswith("multline") else r"\label{a}"
    body = r"\begin{%s}a=1%s\\ b=2\label{b}\end{%s}" % (env, first, env)
    paper, docx = export(tmp_path, body, r"\usepackage{amsmath}")
    text, _ = corrected(tmp_path, paper, edit(docx, lambda r: change_text(r, "2", "9")))
    assert compact(body.replace("2", "9")) in compact(text)


def test_reordered_labels(tmp_path):
    paper, docx = export(tmp_path, r"\begin{align}a&=1\label{a}\\b&=2\label{b}\end{align}")

    def change(root):
        array = next(root.iter(M + "eqArr"))
        first, second = array.findall(M + "e")
        array.remove(second)
        array.insert(list(array).index(first), second)

    text, _ = corrected(tmp_path, paper, edit(docx, change))
    assert r"b&=2\label{b}\\a&=1\label{a}" in compact(text)


def test_identical_rows_edited_in_place_keep_their_places(tmp_path):
    paper, docx = export(tmp_path, r"\begin{align}x&=1\label{a}\\x&=1\label{b}\end{align}")
    text, notes = corrected(tmp_path, paper, edit(docx, lambda r: change_text(r, "1", "2")))
    assert not notes and r"x&=2\label{a}\\x&=1\label{b}" in compact(text)


def test_ambiguous_labels_reported(tmp_path):
    # rows swapped and both edited: which label goes where cannot be told
    paper, docx = export(tmp_path, r"\begin{align}x&=1\label{a}\\y&=1\label{b}\\z&=1\label{c}\end{align}")

    def swap_and_edit(root):
        array = next(root.iter(M + "eqArr"))
        first, second = array.findall(M + "e")[:2]
        array.remove(second)
        array.insert(list(array).index(first), second)
        for t in root.iter(M + "t"):
            if t.text == "1" and len([x for x in root.iter(M + "t") if x.text == "2"]) < 2:
                t.text = "2"

    text, notes = corrected(tmp_path, paper, edit(docx, swap_and_edit))
    assert notes and text == paper.read_text()


def test_redirect_includeonly(tmp_path):
    (tmp_path / "chap.tex").write_text("Chapter $x=1$.")
    paper, docx = export(tmp_path, r"Intro. \include{chap}", r"\includeonly{chap, other}")
    text, _ = corrected(tmp_path, paper, edit(docx, lambda r: change_text(r, "1", "2")))
    assert r"\includeonly{corrected-inputs/chap,other}" in compact(text)


def test_macros_only_where_used(tmp_path):
    paper, docx = export(tmp_path, r"Here $\alpha+\field+R+\mathrm{R}+\text{R}+2$.",
                         r"\newcommand{\lr}{\alpha}\newcommand{\field}{R}")
    text, _ = corrected(tmp_path, paper, edit(docx, lambda r: change_text(r, "2", "3")))
    assert compact(r"$\alpha+\field+R+\mathrm{R}+\text{R}+3$") in compact(text)


def test_later_macro_definition(tmp_path):
    paper, docx = export(tmp_path, r"Here $\alpha+2$. Later \newcommand{\A}{\alpha} we use $\A$.")
    text, _ = corrected(tmp_path, paper, edit(docx, lambda r: change_text(r, "2", "3")))
    assert r"Here$\alpha+3$" in compact(text)


def test_array_spec_kept_literal():
    from mathshuttle.texsource import _replacement
    body = r"$\col+\begin{array}{cc}a&b\end{array}+1$"
    value = r"c+\begin{array}{cc}a&b\end{array}+2"
    assert _replacement(Original(1, body, False), value, [("c", r"\col")], []) == body.replace("1", "2")


@pytest.mark.parametrize("name", ["corrected-inputs/chapter.tex", "corrected.diff"])
def test_collateral_collision(tmp_path, name):
    (tmp_path / "chapter.tex").write_text("Value $x=1$.")
    paper, docx = export(tmp_path, r"\input{chapter}")
    existing = tmp_path / name
    existing.parent.mkdir(exist_ok=True)
    existing.write_text("manual edits")
    with pytest.raises(ConversionError, match=re.escape(existing.name)):
        corrected(tmp_path, paper, edit(docx, lambda r: change_text(r, "1", "2")))
    assert existing.read_text() == "manual edits" and not (tmp_path / "corrected.tex").exists()


def test_diff_created_while_writing(tmp_path, monkeypatch):
    (tmp_path / "chapter.tex").write_text("Value $x=1$.")
    paper, docx = export(tmp_path, r"\input{chapter}")
    result = compare(edit(docx, lambda r: change_text(r, "1", "2")), paper)
    diff = tmp_path / "corrected.diff"
    open_path = Path.open

    def concurrent_open(path, mode="r", *args, **kwargs):
        if path == diff and mode == "xb":
            with open_path(diff, "wb") as other:
                other.write(b"another reviewer")
        return open_path(path, mode, *args, **kwargs)

    monkeypatch.setattr(Path, "open", concurrent_open)
    with pytest.raises(ConversionError, match="corrected.diff"):
        write_corrected(result, tmp_path / "corrected.tex")
    assert diff.read_bytes() == b"another reviewer"
    assert not (tmp_path / "corrected-inputs/chapter.tex").exists()
    assert not (tmp_path / "corrected.tex").exists()


def test_copy_away_from_paper(tmp_path):
    paper, docx = export(tmp_path, "Here $x$.")
    _, notes = corrected(tmp_path / "away", paper, docx)
    assert any(all(word in note for word in ("input", ".sty", "figures")) for note in notes)


def test_comment_before_space(tmp_path):
    paper, docx = export(tmp_path, "First $x$ then more.")

    def change(root):
        para = root.find(".//" + W + "p")
        para.insert(list(para).index(para.find(W + "bookmarkEnd")) + 1, math_text("z"))

    text, _ = corrected(tmp_path, paper, edit(docx, change))
    assert "$x$\n% MathShuttle: added" in text


def test_compare_cli_macro_files(tmp_path, monkeypatch):
    paper, docx = export(tmp_path, "$x$.")
    files = [tmp_path / name for name in ("one.tex", "two.tex")]
    for file, macro in zip(files, ("one", "two")):
        file.write_text(r"\newcommand{\%s}{x}" % macro)
    import mathshuttle.compare as api
    run = Mock(wraps=api.compare)
    monkeypatch.setattr(api, "compare", run)
    assert main(["compare", str(docx), str(paper), "--macros", str(files[0]), "--macros", str(files[1])]) == 0
    assert set(run.call_args.args[2].definitions) >= {r"\one", r"\two"}


def test_deleted_selection_disables_word_actions():
    window = SimpleNamespace(_row=lambda: CompareRow("deleted", Original(1, "$x$", False)),
                             detail=Mock(), copy_button=Mock(), use_button=Mock())
    window._latex = lambda: CompareWindow._latex(window)
    CompareWindow._show(window)
    assert window._latex() is None and CompareWindow.STATUS["moved"] == "Moved"
    for button in (window.copy_button, window.use_button):
        button.configure.assert_called_with(state="disabled")


@pytest.mark.parametrize("opening,indent", [("\\begin{equation}\\label{a}\n", ""),
                                            ("\\begin{equation}\n\\label{a}\n", ""),
                                            ("\\begin{equation}\n  \\label{a}\n", "  ")])
def test_a_label_stays_where_the_author_put_it(tmp_path, opening, indent):
    paper, docx = export(tmp_path, opening + indent + "x=2\n\\end{equation}")
    text, _ = corrected(tmp_path, paper, edit(docx, lambda r: change_text(r, "2", "9")))
    assert opening + indent + "x = 9\n\\end{equation}" in text, text


def test_alignat_keeps_its_argument_on_the_begin_line(tmp_path):
    body = "\\begin{alignat}{2}\na&=1 &\\quad b&=3\\label{first}\\\\\nc&=2 & d&=4\\label{last}\n\\end{alignat}"
    paper, docx = export(tmp_path, body, "\\usepackage{amsmath}")
    text, _ = corrected(tmp_path, paper, edit(docx, lambda r: change_text(r, "2", "9")))
    assert "\\begin{alignat}{2}\na & = 1" in text and "c & = 9" in text and "\\label{last}" in text, text
