"""The findings of the final 0.9.0 review (realistic cases): each test is one of them."""
import copy
import os
import sys
import xml.etree.ElementTree as ET

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, os.path.dirname(__file__))

from mathshuttle import compat, documents, preview  # noqa: E402
from mathshuttle.compare import compare  # noqa: E402
from test_compare import M, W, change_text, compact, edit, export, remove_marks  # noqa: E402
from test_compare_review import corrected  # noqa: E402

AMS = "\\usepackage{amsmath,amssymb}"


def seven_to_nine(root):
    change_text(root, "7", "9")


def test_a_display_written_with_shorthand_macros_is_not_put_into_a_nested_formula(tmp_path):
    preamble = AMS + "\\newcommand{\\be}{\\begin{equation}}\\newcommand{\\ee}{\\end{equation}}"
    body = "Let $x$ denote the input. \\be f(x)=7\\quad\\text{for $x$ positive}\\label{fx}\\ee"
    paper, docx = export(tmp_path, body, preamble)
    text, notes = corrected(tmp_path, paper, edit(docx, seven_to_nine))
    assert body in text  # nothing written into the paper's $x$
    assert any("not found" in n for n in notes) and not any("macro" in n for n in notes)


def test_upright_names_and_bold_symbols_are_not_joined(tmp_path):
    body = "The rate is $\\log\\log n+7$. The vector product is $\\boldsymbol{\\alpha}\\boldsymbol{\\beta}$."
    paper, docx = export(tmp_path, body, AMS)
    read = [f.equation.tex for f in documents.read_docx(str(docx)).found]
    assert read == ["\\log\\log n + 7", "\\boldsymbol{\\alpha}\\boldsymbol{\\beta}"]
    rows = compare(edit(docx, seven_to_nine), paper).rows
    assert [(r.status, r.returned) for r in rows] == [("changed", "\\log\\log n + 9"),
                                                       ("unchanged", "\\boldsymbol{\\alpha}\\boldsymbol{\\beta}")]


def test_a_matrix_that_needs_amsmath_in_a_paper_without_it_is_named(tmp_path):
    body = "\\[\\left(\\begin{array}{cc}1&2\\\\3&7\\end{array}\\right)\\]"
    paper, docx = export(tmp_path, body)
    _, notes = corrected(tmp_path, paper, edit(docx, seven_to_nine))
    assert any("amsmath" in n and "pmatrix" in n for n in notes), notes


def test_no_amsmath_note_when_the_paper_loads_it(tmp_path):
    body = "\\[\\left(\\begin{array}{cc}1&2\\\\3&7\\end{array}\\right)\\]"
    paper, docx = export(tmp_path, body, AMS)
    _, notes = corrected(tmp_path, paper, edit(docx, seven_to_nine))
    assert not any("amsmath" in n for n in notes), notes


def test_rows_keep_their_labels_when_word_adds_braces(tmp_path):
    body = "\\begin{align} U^2 &= a+b \\label{energy}\\\\ V^2 &= 7 \\label{speed}\\end{align}"
    paper, docx = export(tmp_path, body, AMS)
    text, notes = corrected(tmp_path, paper, edit(docx, seven_to_nine))
    assert not notes, notes
    assert "= a + b \\label{energy}" in text and "= 9 \\label{speed}" in text


def test_a_symbolic_tag_written_by_the_export_is_taken_off(tmp_path):
    paper, docx = export(tmp_path, "\\begin{equation}x=7\\tag{*}\\label{bound}\\end{equation}", AMS)
    text, _ = corrected(tmp_path, paper, edit(docx, seven_to_nine))
    assert "\\#" not in text and compact("x = 9 \\tag{*} \\label{bound}") in compact(text)


def test_an_equation_reference_in_text_comes_back(tmp_path):
    body = "\\begin{equation}x=1\\label{base}\\end{equation} \\[z=7\\quad\\text{by \\eqref{base}}\\]"
    paper, docx = export(tmp_path, body, AMS)
    text, notes = corrected(tmp_path, paper, edit(docx, seven_to_nine))
    assert "\\text{by \\eqref{base}}" in text and "(1)" not in text and not notes


def test_an_equation_reference_over_a_relation_comes_back(tmp_path):
    body = "\\begin{equation}x=1\\label{base}\\end{equation} \\[z\\overset{\\eqref{base}}{=}7\\]"
    paper, docx = export(tmp_path, body, AMS)
    text, notes = corrected(tmp_path, paper, edit(docx, seven_to_nine))
    assert "\\eqref{base}" in text and "(1)" not in text and not notes


def test_a_reference_that_cannot_be_put_back_is_named(tmp_path):
    body = "\\begin{equation}x=1\\label{base}\\end{equation} \\[z=7\\quad\\text{by Eq.~\\ref{base}}\\]"
    paper, docx = export(tmp_path, body, AMS)
    _, notes = corrected(tmp_path, paper, edit(docx, seven_to_nine))
    assert any("\\ref{base}" in n for n in notes), notes


def test_the_papers_colours_go_into_a_diagram_picture(monkeypatch):
    lines = list(compat._library_lines("\\usepackage{tikz-cd}\n\\definecolor{mapblue}{RGB}{36,96,140}\n"
                                       "% \\definecolor{old}{RGB}{0,0,0}\n\\colorlet{soft}{mapblue!40}\n"))
    assert lines == ["\\definecolor{mapblue}{RGB}{36,96,140}", "\\colorlet{soft}{mapblue!40}"]
    monkeypatch.setattr(preview, "installed", lambda name: True)
    tex = "\\begin{tikzcd} A \\arrow[r,color=soft] & B \\end{tikzcd}"
    preamble = preview._picture_preamble(tex, ["\\usepackage{tikz-cd}"] + lines, "")
    # soft is made from mapblue: both, in the paper's order, after the package
    assert (preamble.index("\\usepackage{tikz-cd}") < preamble.index("\\definecolor{mapblue}")
            < preamble.index("\\colorlet{soft}{mapblue!40}"))
    # an alias the diagram does not use, of a colour defined elsewhere, stays out
    alias = list(compat._library_lines("\\providecolor{accent}{HTML}{225588}\n\\colorlet{softaccent}{accent!25}"))
    plain = preview._picture_preamble("\\begin{tikzcd} A \\arrow[r] & B \\end{tikzcd}",
                                      ["\\usepackage{tikz-cd}", alias[1]], "")
    assert "softaccent" not in plain
    # a definition that is not a plain colour never reaches the picture
    assert not list(compat._library_lines("\\definecolor{x}{RGB}{\\input{secret}}"))


def test_an_untouched_empty_set_is_not_deleted(tmp_path):
    preamble = AMS + "\\newcommand{\\set}[1]{\\left\\{#1\\right\\}}"
    paper, docx = export(tmp_path, "The initial active set is $\\set{}$.", preamble)
    rows = compare(docx, paper).rows
    assert [r.status for r in rows] == ["unchanged"]


def test_an_equation_added_after_a_picture_is_placed_after_it(tmp_path, monkeypatch, capsys):
    import base64
    from mathshuttle.__main__ import main
    png = tmp_path / "stub.png"
    png.write_bytes(base64.b64decode("iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAIAAACQd1PeAAAADElEQVR4nGNgYGAAAAAEAAH2FzhVAAAAAElFTkSuQmCC"))
    monkeypatch.setattr(preview, "render_picture", lambda *a, **k: (str(png), 1.0, 0.3))
    body = ("\\begin{equation}\n\\begin{tikzcd} A \\arrow[r] & B \\end{tikzcd}\n\\label{d}\n\\end{equation}\n"
            "Next $x=1$.")
    paper, docx = export(tmp_path, body, "\\usepackage{tikz-cd}")

    def add(root):
        body = root.find(W + "body")
        paragraphs = body.findall(W + "p")
        at = next(i for i, p in enumerate(paragraphs) if list(p.iter(W + "drawing")))
        new = ET.Element(W + "p")
        ET.SubElement(ET.SubElement(ET.SubElement(new, M + "oMath"), M + "r"), M + "t").text = "z=9"
        body.insert(list(body).index(paragraphs[at]) + 1, new)

    capsys.readouterr()
    main(["compare", str(edit(docx, add)), str(paper)])
    assert "added after (1)" in capsys.readouterr().out


# ---- the second reviewer's findings -------------------------------------------------------------------

def word365_save(root):
    """What Word 365 does to every maths run when it saves: its fonts, as w:rPr."""
    for run in root.iter(M + "r"):
        props = ET.Element(W + "rPr")
        ET.SubElement(props, W + "rFonts", {W + "ascii": "Cambria Math", W + "hAnsi": "Cambria Math"})
        run.insert(1 if len(run) and run[0].tag == M + "rPr" else 0, props)


def test_two_rows_edited_in_place_keep_their_labels(tmp_path):
    body = "\\begin{align} a &= b + c \\label{eq:1} \\\\ d &= e + f \\label{eq:2} \\end{align}"
    paper, docx = export(tmp_path, body, AMS)

    def change(root):
        change_text(root, "c", "x")
        change_text(root, "f", "y")

    text, notes = corrected(tmp_path, paper, edit(docx, change))
    assert not notes, notes
    assert "b + x \\label{eq:1}" in text and "e + y \\label{eq:2}" in text


def test_alignat_rows_swapped_keep_their_labels(tmp_path):
    body = ("\\begin{alignat}{2} a &= 1 & b &= 3 \\label{eq:first} \\\\ "
            "c &= 2 & d &= 4 \\label{eq:second} \\end{alignat}")
    paper, docx = export(tmp_path, body, AMS)

    def swap(root):
        array = next(root.iter(M + "eqArr"))
        first, second = array.findall(M + "e")
        array.remove(second)
        array.insert(list(array).index(first), second)

    text, notes = corrected(tmp_path, paper, edit(docx, swap))
    assert not notes, notes
    assert compact("\\begin{alignat}{2}c&=2&d&=4\\label{eq:second}\\\\a&=1&b&=3\\label{eq:first}") in compact(text)


def test_a_one_row_align_saved_by_word_365_is_unchanged(tmp_path):
    paper, docx = export(tmp_path, "\\begin{align} a &= b + c \\label{eq:one} \\end{align}", AMS)
    returned = edit(docx, word365_save)
    assert [f.equation.tex for f in documents.read_docx(str(returned)).found] == ["a = b + c"]
    assert [r.status for r in compare(returned, paper).rows] == ["unchanged"]


def test_a_literal_ampersand_stays_after_a_word_365_save(tmp_path):
    paper, docx = export(tmp_path, "\\begin{equation} A \\& B \\label{eq:amp} \\end{equation}", AMS)
    returned = edit(docx, word365_save)
    assert [r.status for r in compare(returned, paper).rows] == ["unchanged"]


def test_maths_in_a_citation_note_does_not_stop_the_other_sources(tmp_path):
    body = "By \\cite[Theorem $3.1$]{smith}, we have $y = 2$ and $z = 3$."
    paper, docx = export(tmp_path, body)
    text, notes = corrected(tmp_path, paper, edit(docx, lambda r: change_text(r, "2", "9")))
    assert not notes, notes
    assert "$y = 9$ and $z = 3$" in text.replace("$y=9$", "$y = 9$")


def test_a_comment_ending_inside_a_numbered_equation(tmp_path):
    paper, docx = export(tmp_path, "\\begin{equation} E = mc^2 \\label{e} \\end{equation}", AMS)

    def comment(root):
        row = root.find(".//" + M + "eqArr/" + M + "e")
        row.insert(0, ET.Element(W + "commentRangeStart", {W + "id": "50"}))
        row.append(ET.Element(W + "commentRangeEnd", {W + "id": "50"}))
        ET.SubElement(ET.SubElement(row, W + "r"), W + "commentReference", {W + "id": "50"})

    found = documents.read_docx(str(edit(docx, comment))).found
    assert [(f.equation.tex, f.numbers) for f in found] == [("E = mc^{2}", ["(1)"])]


def test_an_eqnarray_with_a_matrix_inside_is_left_as_it_is(tmp_path):
    body = ("\\begin{eqnarray} A &=& \\begin{pmatrix} 1 & -1 \\\\ 0 & 2 \\end{pmatrix} \\label{e1} \\\\ "
            "B &=& 3 \\label{e2} \\end{eqnarray}")
    paper, docx = export(tmp_path, body, AMS)
    text, notes = corrected(tmp_path, paper, edit(docx, lambda r: change_text(r, "3", "4")))
    assert text == paper.read_text(encoding="utf-8") and any("columns" in n for n in notes)


def test_a_repeated_formula_added_without_bookmarks_leaves_the_others_matched(tmp_path):
    body = "Let $E = mc^2$. Here $x$ is real. Also $F = ma$. Again $x$ is positive."
    paper, docx = export(tmp_path, body)

    def change(root):
        remove_marks(root)
        change_text(root, "2", "3")
        extra = ET.SubElement(root.find(".//" + W + "p"), M + "oMath")
        ET.SubElement(ET.SubElement(extra, M + "r"), M + "t").text = "x"

    rows = compare(edit(docx, change, legacy=True), paper).rows
    statuses = {r.original.tex if r.original else r.returned: r.status for r in rows}
    assert statuses["$E = mc^2$"] == "changed" and statuses["$F = ma$"] == "unchanged"


def test_a_letter_typed_into_a_function_name_with_track_changes(tmp_path):
    paper, docx = export(tmp_path, "$\\sin(x)$")

    def insert_h(root):
        parent = next(p for p in root.iter() if any(r.findtext(M + "t") == "sin" for r in p.findall(M + "r")))
        sin = next(r for r in parent if r.findtext(M + "t") == "sin")
        run = ET.Element(M + "r")
        ET.SubElement(ET.SubElement(run, M + "rPr"), M + "sty", {M + "val": "p"})
        ET.SubElement(ET.SubElement(run, W + "ins", {W + "id": "1", W + "author": "Co"}), M + "t").text = "h"
        parent.insert(list(parent).index(sin) + 1, run)

    returned = edit(docx, insert_h)
    assert [f.equation.tex for f in documents.read_docx(str(returned)).found] == ["\\sinh(x)"]
    assert [(r.status, r.returned) for r in compare(returned, paper).rows] == [("changed", "\\sinh(x)")]


# ---- the re-check of those fixes ------------------------------------------------------------------------

def swap_rows(root):
    array = next(root.iter(M + "eqArr"))
    first, second = array.findall(M + "e")
    array.remove(second)
    array.insert(list(array).index(first), second)


def test_an_edit_does_not_land_in_a_citation_with_the_same_formula(tmp_path):
    body = "Following \\cite[case $p=2$]{smith}, we first take $p=2$."
    paper, docx = export(tmp_path, body, AMS)
    text, _ = corrected(tmp_path, paper, edit(docx, lambda r: change_text(r, "2", "3")))
    assert "\\cite[case $p=2$]{smith}" in text and "take $p = 3$" in text.replace("$p=3$", "$p = 3$")


def test_a_matrix_inside_a_shorthand_display_is_not_its_source(tmp_path):
    preamble = AMS + "\\newcommand{\\be}{\\begin{equation}}\\newcommand{\\ee}{\\end{equation}}"
    m = "\\begin{pmatrix}0 & 1\\\\1 & 0\\end{pmatrix}"
    body = "The swap matrix is $" + m + "$. For the chain take \\be P = " + m + "\\ee"
    paper, docx = export(tmp_path, body, preamble)
    text, notes = corrected(tmp_path, paper, edit(docx, lambda r: change_text(r, "P", "Q")))
    assert body in text and any("not found" in n for n in notes)


def test_swapped_rows_keep_their_labels_by_their_numbers(tmp_path):
    body = "\\begin{align} T(n)&\\le 2^{n+1}\\label{coarse}\\\\ T(n)&\\le 2^n+1\\label{sharp}\\end{align}"
    paper, docx = export(tmp_path, body, AMS)
    text, notes = corrected(tmp_path, paper, edit(docx, swap_rows))
    assert not notes, notes
    assert compact("2^{n}+1\\label{sharp}\\\\T(n)&\\leq2^{n+1}\\label{coarse}") in compact(text)


def test_one_row_with_two_column_pairs_keeps_its_columns(tmp_path):
    paper, docx = export(tmp_path, "\\begin{align} a &= b & c &= 7\\label{two}\\end{align}", AMS)

    def change(root):
        word365_save(root)
        change_text(root, "7", "9")

    returned = edit(docx, change)
    assert "&" in documents.read_docx(str(returned)).found[0].equation.tex
    text, _ = corrected(tmp_path, paper, returned)
    assert compact("a&=b&c&=9\\label{two}") in compact(text)


def test_a_split_run_after_an_operator_name_is_not_joined_to_it(tmp_path):
    paper, docx = export(tmp_path, "The log determinant is $\\log\\det A+7$.", AMS)

    def split(root):
        for parent in root.iter():
            for i, child in enumerate(parent):
                if child.tag == M + "r" and child.findtext(M + "t") == "det":
                    second = copy.deepcopy(child)
                    child.find(M + "t").text = "d"
                    second.find(M + "t").text = "et"
                    parent.insert(i + 1, second)
                    return

    assert [r.status for r in compare(edit(docx, split), paper).rows] == ["unchanged"]
    other = tmp_path / "gdp"
    other.mkdir()
    _, docx2 = export(other, "$\\log\\mathrm{GDP}$", AMS)
    assert [f.equation.tex for f in documents.read_docx(str(docx2)).found] == ["\\log \\mathrm{GDP}"]


def test_a_deleted_reference_does_not_move_onto_another_number(tmp_path):
    body = ("\\begin{equation} x=1 \\label{base}\\end{equation}"
            "\\[f=7\\quad\\text{in case (1), by \\eqref{base}}\\]")
    paper, docx = export(tmp_path, body, AMS)

    def delete(root):
        for t in root.iter(M + "t"):
            if t.text and "in case" in t.text:
                t.text = "in case (1)"

    text, notes = corrected(tmp_path, paper, edit(docx, delete))
    assert "\\text{in case (1)}" in text and any("\\eqref{base}" in n for n in notes)


def test_a_reference_to_a_starred_tag_gets_its_parentheses_once(tmp_path):
    body = "\\begin{equation}x=1\\tag*{H}\\label{hyp}\\end{equation}\\[z=7\\quad\\text{by \\eqref{hyp}}\\]"
    paper, docx = export(tmp_path, body, AMS)
    text, _ = corrected(tmp_path, paper, edit(docx, seven_to_nine))
    assert "\\text{by \\eqref{hyp}}" in text and "(\\eqref" not in text


def test_an_edit_next_to_a_repeated_formula_without_bookmarks(tmp_path):
    paper, docx = export(tmp_path, "Let $x$ be a state. We start at $x=1$. Later $x$ is observed.")

    def change(root):
        remove_marks(root)
        change_text(root, "1", "2")
        para = next(root.iter(W + "p"))
        para.append(copy.deepcopy(list(para.iter(M + "oMath"))[-1]))

    rows = compare(edit(docx, change, legacy=True), paper).rows
    assert ("changed", "$x=1$", "x = 2") in [(r.status, r.original.tex if r.original else None, r.returned)
                                            for r in rows]


def test_an_eqnarray_relation_with_a_note_over_it_is_left_as_it_is(tmp_path):
    body = "\\begin{eqnarray}a &=& b \\label{s1}\\\\ b &\\stackrel{(*)}{=}& 7\\label{s2}\\end{eqnarray}"
    paper, docx = export(tmp_path, body, AMS)
    text, notes = corrected(tmp_path, paper, edit(docx, seven_to_nine))
    assert text == paper.read_text(encoding="utf-8") and any("columns" in n for n in notes)


def test_a_tag_with_parentheses_in_it(tmp_path):
    paper, docx = export(tmp_path, "\\begin{equation} x=7 \\tag{P(t)}\\label{problem}\\end{equation}", AMS)
    text, _ = corrected(tmp_path, paper, edit(docx, seven_to_nine))
    assert "\\#" not in text and compact("x=9\\tag{P(t)}\\label{problem}") in compact(text)


# ---- the last re-check ------------------------------------------------------------------------------

def test_a_starred_tag_with_parentheses_in_it(tmp_path):
    paper, docx = export(tmp_path, "\\begin{equation} x=7 \\tag*{P(t)}\\label{prob}\\end{equation}", AMS)
    text, _ = corrected(tmp_path, paper, edit(docx, seven_to_nine))
    assert "\\#" not in text and compact("x=9\\tag*{P(t)}\\label{prob}") in compact(text)


def test_swapped_bodies_under_fixed_numbers_take_their_labels_along(tmp_path):
    body = "\\begin{align}u(0,t)&=0\\label{bc:left}\\\\u(1,t)&=1\\label{bc:right}\\end{align}"
    paper, docx = export(tmp_path, body, AMS)

    def swap_bodies(root):
        first, second = next(root.iter(M + "eqArr")).findall(M + "e")
        a, b = list(first)[:-1], list(second)[:-1]
        first[:-1], second[:-1] = b, a

    text, notes = corrected(tmp_path, paper, edit(docx, swap_bodies))
    assert not notes, notes
    assert compact("u(1,t)&=1\\label{bc:right}\\\\u(0,t)&=0\\label{bc:left}") in compact(text)


def test_a_numbered_row_keeps_its_label_next_to_an_unnumbered_one(tmp_path):
    body = "\\begin{align}u(0,t)&=0\\notag\\\\u(1,t)&=0\\label{bc:right}\\end{align}"
    paper, docx = export(tmp_path, body, AMS)

    def shift(root):
        left, right = next(root.iter(M + "eqArr")).findall(M + "e")
        change_text(left, "0", "1")
        change_text(right, "1", "2")

    text, notes = corrected(tmp_path, paper, edit(docx, shift))
    assert not notes, notes
    assert compact("u(1,t)&=0\\notag\\\\u(2,t)&=0\\label{bc:right}") in compact(text)


def test_a_reference_next_to_a_macro_that_prints_the_same_number(tmp_path):
    preamble = AMS + "\n\\newcommand{\\caseone}{(1)}"
    body = ("\\begin{equation} x=1\\label{bound}\\end{equation}"
            "\\[f=7\\quad\\text{in case \\caseone, by \\eqref{bound}}\\]")
    paper, docx = export(tmp_path, body, preamble)

    def delete(root):
        t = next(t for t in root.iter(M + "t") if "in case" in (t.text or ""))
        t.text = t.text.split(", by")[0]

    text, notes = corrected(tmp_path, paper, edit(docx, delete))
    assert "\\text{in case \\eqref" not in text and any("\\eqref{bound}" in n for n in notes)


def test_a_reference_to_a_starred_tag_in_parentheses(tmp_path):
    body = "\\begin{equation} x=1 \\tag*{(H)}\\label{hyp}\\end{equation}\\[z=7\\quad\\text{by \\eqref{hyp}}\\]"
    paper, docx = export(tmp_path, body, AMS)
    text, _ = corrected(tmp_path, paper, edit(docx, seven_to_nine))
    assert "\\text{by \\eqref{hyp}}" in text


def test_an_alignat_with_an_added_column_pair_counts_it(tmp_path):
    paper, docx = export(tmp_path, "\\begin{alignat}{2} a &= 1 & b &= 2\\label{values}\\end{alignat}", AMS)

    def add_pair(root):
        row = next(root.iter(M + "eqArr")).find(M + "e")
        added = copy.deepcopy(list(row)[4:-1])
        added[1].find(M + "t").text = "c"
        added[-1].find(M + "t").text = "3"
        row[-1:-1] = added

    text, _ = corrected(tmp_path, paper, edit(docx, add_pair))
    assert "\\begin{alignat}{3}" in text and compact("c&=3\\label{values}") in compact(text)


def test_a_tie_between_a_repeated_formula_and_another_is_not_guessed(tmp_path):
    body = "The example uses $p=2$. The linear case has $p=1$. The proof assumes $p=2$."
    paper, docx = export(tmp_path, body)

    def change(root):
        remove_marks(root)
        formulas = list(root.iter(M + "oMath"))
        change_text(formulas[0], "2", "3")
        for parent in root.iter():
            if formulas[1] in list(parent):
                parent.remove(formulas[1])
                break

    text, _ = corrected(tmp_path, paper, edit(docx, change, legacy=True))
    assert "linear case has $p=1$" in text and "$p = 3$" not in text and "$p=3$" not in text


def test_two_operator_names_side_by_side_stay_two(tmp_path):
    paper, docx = export(tmp_path, "Write $a=\\lim\\inf I_n$ and $b=\\lim\\sup I_n$.", AMS)
    read = [f.equation.tex for f in documents.read_docx(str(docx)).found]
    assert read == ["a = \\lim\\inf I_{n}", "b = \\lim\\sup I_{n}"]
