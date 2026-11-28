"""Findings of the Codex review of the document import, the hotkey and the preview: hand-made Office files
(zipfile) for the structures Word / PowerPoint write that the sample files do not have."""
import io
import os
import subprocess
import sys
import threading
import time
import types
import xml.etree.ElementTree as ET
import zipfile

import pytest

ROOT = os.path.join(os.path.dirname(__file__), "..")
sys.path.insert(0, ROOT)

from mathshuttle import hotkeys, preview  # noqa: E402
from mathshuttle.documents import read_document  # noqa: E402
from mathshuttle.pandoc_engine import Equation  # noqa: E402

W = 'xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main"'
M = 'xmlns:m="http://schemas.openxmlformats.org/officeDocument/2006/math"'


def omath(text):
    return "<m:oMath><m:r><m:t>%s</m:t></m:r></m:oMath>" % text


def docx(tmp_path, body, footnotes=None, styles=None, name="t.docx", encoding="utf-8", strict=False):
    document = '<?xml version="1.0" encoding="%s"?><w:document %s %s><w:body>%s</w:body></w:document>' % (
        encoding.upper(), W, M, body)
    if strict:
        document = (document.replace("http://schemas.openxmlformats.org/wordprocessingml/2006/main",
                                     "http://purl.oclc.org/ooxml/wordprocessingml/main")
                    .replace("http://schemas.openxmlformats.org/officeDocument/2006/math",
                             "http://purl.oclc.org/ooxml/officeDocument/math"))
    path = tmp_path / name
    with zipfile.ZipFile(path, "w") as z:
        z.writestr("[Content_Types].xml", "<Types/>")
        z.writestr("word/document.xml", document.encode(encoding))
        if footnotes:
            z.writestr("word/footnotes.xml", '<w:footnotes %s %s>%s</w:footnotes>' % (W, M, footnotes))
        if styles:
            z.writestr("word/styles.xml", '<w:styles %s>%s</w:styles>' % (W, styles))
    return str(path)


def found(path):
    d = read_document(path)
    return [(f.where, f.equation.tex, f.equation.display) for f in d.found], d.notes


def test_math_paragraph_with_several_equations(tmp_path):
    body = ("<w:p><m:oMathPara>%s%s</m:oMathPara></w:p><w:p><w:r><w:t>and </w:t></w:r>%s</w:p>"
            % (omath("x"), omath("y"), omath("z")))
    eqs, _ = found(docx(tmp_path, body))
    assert [(tex, display) for _, tex, display in eqs] == [("x", True), ("y", True), ("z", False)]


def test_tracked_deletion_is_not_read(tmp_path):
    body = ('<w:p><w:del w:id="1" w:author="a">%s</w:del><w:ins w:id="2" w:author="a">%s</w:ins></w:p>'
            % (omath("x"), omath("y")))
    eqs, _ = found(docx(tmp_path, body))
    assert [tex for _, tex, _ in eqs] == ["y"]


@pytest.mark.parametrize("accept", [True, False])
@pytest.mark.parametrize("slot", ["sup", "sub", "num", "den", "deg", "e", "lim", "fName"])
def test_empty_tracked_math_slots(accept, slot):
    from mathshuttle import wordmath
    change = "del" if accept else "ins"
    xml = ('<m:oMath %s %s><m:%s><m:r><w:%s><m:t>x</m:t></w:%s></m:r></m:%s></m:oMath>'
           % (W, M, slot, change, change, slot))
    view = wordmath.revision_view(ET.fromstring(xml), accept)
    retained = view.find(wordmath.M + slot)
    assert retained is not None and len(retained) == 0


@pytest.mark.parametrize("revision,accept", [("ins", False), ("del", True)])
def test_revision_around_a_whole_slot(revision, accept):
    from mathshuttle import wordmath
    xml = ('<m:oMath %s %s><m:sSup><m:e><m:r><m:t>x</m:t></m:r></m:e>'
           '<w:%s><m:sup><m:r><m:t>2</m:t></m:r></m:sup></w:%s></m:sSup></m:oMath>' % (W, M, revision, revision))
    view = wordmath.revision_view(ET.fromstring(xml), accept)
    slot = view.find(".//" + wordmath.M + "sup")
    assert slot is not None and len(slot) == 0


def test_deleted_word_equation_skipped_before_batch(tmp_path, monkeypatch):
    from mathshuttle import documents
    from unittest.mock import Mock
    body = ('<w:p><m:oMath><m:r><w:del><m:t>x</m:t></w:del></m:r></m:oMath>%s%s</w:p>'
            % (omath("y"), omath("z")))
    read = Mock(wraps=documents.read_omml)
    monkeypatch.setattr(documents, "read_omml", read)
    eqs, notes = found(docx(tmp_path, body))
    assert [tex for _, tex, _ in eqs] == ["y", "z"] and not notes
    assert read.call_count == 1


@pytest.mark.parametrize("mark,max_dist,number", [
    ("#(A)", False, None), ("#S", True, None), ("#N", True, None),
    ("#(x+y)", True, None), ("#(A.1-2')", True, "(A.1-2')"),
])
def test_number_like_word_marks(tmp_path, mark, max_dist, number):
    props = '<m:eqArrPr><m:maxDist m:val="1"/></m:eqArrPr>' if max_dist else ""
    body = ('<w:p><m:oMath><m:eqArr>%s<m:e><m:r><m:t>n=</m:t></m:r><m:r><m:t>%s</m:t></m:r>'
            '</m:e></m:eqArr></m:oMath></w:p>' % (props, mark))
    eq = read_document(docx(tmp_path, body)).found[0]
    assert eq.numbers == ([number] if number else [])
    if not number:
        assert r"\#" in eq.equation.tex


def test_styled_ampersand_in_numbered_equation(tmp_path):
    body = ('<w:p><m:oMath><m:eqArr><m:eqArrPr><m:maxDist m:val="1"/></m:eqArrPr><m:e>'
            '<m:r><m:t>A</m:t></m:r><m:r><m:rPr><m:sty m:val="p"/></m:rPr><m:t>&amp;</m:t></m:r>'
            '<m:r><m:t>B</m:t></m:r><m:r><m:t>#(1)</m:t></m:r></m:e></m:eqArr></m:oMath></w:p>')
    eq = read_document(docx(tmp_path, body)).found[0]
    assert r"\&" in eq.equation.tex and eq.numbers == ["(1)"]


def test_strict_open_xml(tmp_path):
    eqs, _ = found(docx(tmp_path, "<w:p>%s</w:p>" % omath("x"), strict=True))
    assert [tex for _, tex, _ in eqs] == ["x"]


def test_utf16_part(tmp_path):
    eqs, _ = found(docx(tmp_path, "<w:p>%s</w:p>" % omath("x"), encoding="utf-16"))
    assert [tex for _, tex, _ in eqs] == ["x"]


def test_footnotes(tmp_path):
    notes = ('<w:footnote w:type="separator" w:id="-1"><w:p/></w:footnote>'
             '<w:footnote w:id="1"><w:p>%s</w:p></w:footnote>' % omath("y"))
    eqs, _ = found(docx(tmp_path, "<w:p>%s</w:p>" % omath("x"), footnotes=notes))
    assert [(where, tex) for where, tex, _ in eqs] == [("(before the first heading)", "x"), ("footnote 1", "y")]


def test_outline_level_9_is_body_text_and_text_boxes_keep_context(tmp_path):
    body = ('<w:p><w:pPr><w:outlineLvl w:val="0"/></w:pPr><w:r><w:t>Intro</w:t></w:r></w:p>'
            '<w:p><w:pPr><w:outlineLvl w:val="9"/></w:pPr><w:r><w:t>plain text</w:t></w:r></w:p>'
            '<w:p><w:r><w:t>outer </w:t></w:r><w:r><w:txbxContent><w:p><w:r><w:t>inner </w:t></w:r>%s</w:p>'
            '</w:txbxContent></w:r>%s</w:p>' % (omath("a"), omath("b")))
    d = read_document(docx(tmp_path, body))
    assert [f.where for f in d.found] == ["Intro", "Intro"]
    assert d.found[1].context.startswith("outer") and "□" in d.found[1].context


def pptx(tmp_path, slide_body, notes_body=None):
    p = 'xmlns:p="http://schemas.openxmlformats.org/presentationml/2006/main"'
    a = 'xmlns:a="http://schemas.openxmlformats.org/drawingml/2006/main"'
    r = 'xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships"'
    rel = 'xmlns="http://schemas.openxmlformats.org/package/2006/relationships"'
    path = tmp_path / "t.pptx"
    with zipfile.ZipFile(path, "w") as z:
        z.writestr("ppt/presentation.xml", '<p:presentation %s %s><p:sldIdLst><p:sldId id="256" r:id="rId1"/>'
                                           '</p:sldIdLst></p:presentation>' % (p, r))
        z.writestr("ppt/_rels/presentation.xml.rels", '<Relationships %s><Relationship Id="rId1" Type="x/slide" '
                                                      'Target="slides/slide1.xml"/></Relationships>' % rel)
        z.writestr("ppt/slides/slide1.xml", '<p:sld %s %s %s><p:cSld><p:spTree>%s</p:spTree></p:cSld></p:sld>'
                   % (p, a, M, slide_body))
        if notes_body:
            z.writestr("ppt/slides/_rels/slide1.xml.rels",
                       '<Relationships %s><Relationship Id="rId2" Type="http://schemas.openxmlformats.org/'
                       'officeDocument/2006/relationships/notesSlide" Target="../notesSlides/notesSlide1.xml"/>'
                       '</Relationships>' % rel)
            z.writestr("ppt/notesSlides/notesSlide1.xml", '<p:notes %s %s %s><p:cSld><p:spTree>%s</p:spTree>'
                                                          '</p:cSld></p:notes>' % (p, a, M, notes_body))
    return str(path)


def shape(content, ph=None):
    ph = '<p:nvSpPr><p:nvPr><p:ph type="%s"/></p:nvPr></p:nvSpPr>' % ph if ph else ""
    return "<p:sp>%s<p:txBody><a:p>%s</a:p></p:txBody></p:sp>" % (ph, content)


def test_speaker_notes(tmp_path):
    eqs, _ = found(pptx(tmp_path, shape(omath("x")), shape(omath("y"), ph="body") + shape(omath("9"), ph="sldNum")))
    assert [(where, tex) for where, tex, _ in eqs] == [("slide 1", "x"), ("slide 1 notes", "y")]


def test_powerpoint_legacy_equation_objects_are_counted(tmp_path):
    ole = '<p:graphicFrame><p:oleObj progId="Equation.3" r:id="rId9"/></p:graphicFrame>'.replace(
        "r:id", "name")
    _, notes = found(pptx(tmp_path, shape(omath("x")) + ole))
    assert any("Equation Editor 3.0" in n for n in notes)


def test_heading_equations(tmp_path):
    md = tmp_path / "h.md"
    md.write_text("# Model $x$\n\nBody $y$.\n", encoding="utf-8")
    assert [f.equation.tex for f in read_document(str(md)).found] == ["x", "y"]
    tex = tmp_path / "h.tex"
    tex.write_text(r"\section{Model $x$} Body $y$.", encoding="utf-8")
    assert [f.equation.tex for f in read_document(str(tex)).found] == ["x", "y"]


def test_utf16_tex_file(tmp_path):
    tex = tmp_path / "u.tex"
    tex.write_bytes(b"\xff\xfe" + "$x$".encode("utf-16-le"))
    assert [f.equation.tex for f in read_document(str(tex)).found] == ["x"]


def test_missing_input_is_reported(tmp_path):
    tex = tmp_path / "m.tex"
    tex.write_text("$a$ \\input{missing} $b$", encoding="utf-8")
    d = read_document(str(tex))
    assert any("missing" in n for n in d.notes)


def test_extract_xml_comment_is_well_formed(tmp_path):
    # a Word heading keeps a literal "--" (pandoc's LaTeX / Markdown readers would make it an en dash)
    body = ('<w:p><w:pPr><w:outlineLvl w:val="0"/></w:pPr><w:r><w:t>A--B-</w:t></w:r></w:p><w:p>%s</w:p>'
            % omath("x"))
    path = docx(tmp_path, body)
    env = dict(os.environ, PYTHONIOENCODING="utf-8")
    for dst in ("mathml", "omml"):
        p = subprocess.run([sys.executable, "-m", "mathshuttle", "extract", path, "-t", dst], cwd=ROOT,
                           capture_output=True, env=env)
        out = p.stdout.decode("utf-8")
        assert p.returncode == 0 and "A-" in out
        ET.fromstring('<r xmlns:m="http://schemas.openxmlformats.org/officeDocument/2006/math">%s</r>' % out)


# ---- preview --------------------------------------------------------------------------------------
needs_latex = pytest.mark.skipif(preview.tools() is None, reason="no LaTeX installation on PATH")


def test_latex_runs_without_shell_escape():
    t = preview.tools() or {"latex": "latex", "miktex": False}
    assert "-no-shell-escape" in preview.latex_command(t, "eq.tex")


@needs_latex
def test_shell_escape_is_off(tmp_path):
    check = r"\ifnum\pdfshellescape=0 x\else\errmessage{shell escape is on}\fi"
    assert preview.render([Equation(check, False)], work=str(tmp_path))


@needs_latex
def test_concurrent_renders_of_one_equation(tmp_path):
    results, errors = [], []

    def job():
        try:
            results.append(preview.render([Equation("x^3 + y^3", True)], work=str(tmp_path)))
        except preview.PreviewError as e:
            errors.append(e)

    threads = [threading.Thread(target=job) for _ in range(3)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(60)
    assert not errors and len(set(results)) == 1


def test_nested_tag_is_dropped():
    assert "tag" not in preview.math_line(r"\begin{equation}x\tag{\text{A}}\end{equation}", True)


# ---- hotkey ---------------------------------------------------------------------------------------
@pytest.mark.skipif(sys.platform != "win32", reason="the Win32 listener's thread (RegisterHotKey)")
def test_timed_out_listener_releases_the_key(monkeypatch):
    real = hotkeys._user32

    class Slow:
        def __getattr__(self, name):
            return getattr(real, name)

        def RegisterHotKey(self, *args):
            time.sleep(0.4)
            return real.RegisterHotKey(*args)

    monkeypatch.setattr(hotkeys, "_user32", Slow())
    late = hotkeys.HotkeyListener("Ctrl+Alt+Shift+F22")
    assert late.start_and_wait(timeout=0.05) is not None
    late.join(2)
    monkeypatch.setattr(hotkeys, "_user32", real)
    again = hotkeys.HotkeyListener("Ctrl+Alt+Shift+F22")
    assert again.start_and_wait() is None  # the late registration was released
    again.stop()


@pytest.mark.skipif(sys.platform not in ("win32", "darwin"), reason="no clipboard backend here")
def test_copy_for_office_clipboard_race():
    """A conversion does not overwrite what was copied after it read the clipboard (overwrites the clipboard)."""
    from mathshuttle import clipboard
    clipboard.set_text("first")
    sequence = clipboard.sequence_number()
    clipboard.set_text("copied meanwhile")
    with pytest.raises(clipboard.ClipboardChanged):
        clipboard.set_text("result", expect_sequence=sequence)
    assert clipboard.get_text() == "copied meanwhile"
    clipboard.set_text("ok", expect_sequence=clipboard.sequence_number())
    assert clipboard.get_text() == "ok"


# What Word 365 saved when a whole fraction was deleted with Track Changes on (2026-11-28): the
# structure's own properties carry the deletion, besides its runs.
_DELETED_FRACTION = ('<m:oMath %s %s><m:r><m:t>a</m:t></m:r><m:r><m:t>+</m:t></m:r><m:f><m:fPr><m:ctrlPr>'
                     '<w:%s w:id="2"/></m:ctrlPr></m:fPr><m:num><m:r><w:%s w:id="3"><m:t>b</m:t></w:%s></m:r>'
                     '</m:num><m:den><m:r><w:%s w:id="4"><m:t>c</m:t></w:%s></m:r></m:den></m:f>'
                     '<m:r><m:t>=</m:t></m:r><m:r><m:t>d</m:t></m:r></m:oMath>')


def test_a_structure_deleted_whole_goes_with_its_slots():
    from mathshuttle import wordmath
    xml = _DELETED_FRACTION % ((W, M) + ("del",) * 5)
    accepted = wordmath.revision_view(ET.fromstring(xml), True)
    assert accepted.find(".//" + wordmath.M + "f") is None
    assert "".join(t.text for t in accepted.iter(wordmath.M + "t")) == "a+=d"
    rejected = wordmath.revision_view(ET.fromstring(xml), False)
    assert "".join(t.text for t in rejected.iter(wordmath.M + "t")) == "a+bc=d"
    assert rejected.find(".//" + wordmath.M + "f") is not None


def test_a_structure_inserted_whole_is_not_in_the_original():
    from mathshuttle import wordmath
    xml = _DELETED_FRACTION % ((W, M) + ("ins",) * 5)
    assert wordmath.revision_view(ET.fromstring(xml), False).find(".//" + wordmath.M + "f") is None
    assert wordmath.revision_view(ET.fromstring(xml), True).find(".//" + wordmath.M + "f") is not None


def test_a_structure_marked_deleted_keeps_what_was_not_deleted():
    from mathshuttle import wordmath
    xml = ('<m:oMath %s %s><m:f><m:fPr><m:ctrlPr><w:del w:id="2"/></m:ctrlPr></m:fPr><m:num><m:r>'
           '<m:t>b</m:t></m:r></m:num><m:den><m:r><m:t>c</m:t></m:r></m:den></m:f></m:oMath>' % (W, M))
    assert wordmath.revision_view(ET.fromstring(xml), True).find(".//" + wordmath.M + "f") is not None
