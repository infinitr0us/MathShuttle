"""Command help, input choices and per-equation extraction diagnostics."""
import io
import re
import sys
import xml.etree.ElementTree as ET

import pytest

from mathshuttle import clipboard
from mathshuttle.__main__ import main
from mathshuttle.pandoc_engine import ConversionError


def help_text(args, capsys):
    with pytest.raises(SystemExit) as stop:
        main([*args, "--help"])
    assert stop.value.code == 0
    return capsys.readouterr().out


def test_help_explains_the_window_commands_and_examples(capsys):
    help_output = help_text([], capsys)
    assert "mathshuttle without arguments opens the window" in help_output
    assert "LaTeX, MathML and OMML" in help_output
    commands, examples = help_output.split("Examples:\n")
    for name in ("convert", "copy", "paste", "extract", "version"):
        assert re.search(r"^\s+" + name + r"\s+\w+", commands, re.MULTILINE)
    assert re.search(r"--version\s+Show the MathShuttle, pandoc and texmath versions", commands)
    assert 4 <= len(re.findall(r"^  mathshuttle \S+", examples, re.MULTILINE)) <= 6


@pytest.mark.parametrize("cmd, names", [
    ("convert", ["FILE", "--expr", "--from", "--to", "--mode", "--wrapper", "--macros", "--no-tidy", "--annotate"]),
    ("copy", ["FILE", "--expr", "--from", "--mode", "--macros", "--index"]),
    ("paste", ["--to", "--mode", "--wrapper", "--no-tidy"]),
    ("extract", ["FILE", "--to", "--wrapper", "--macros", "--docx", "--display", "--unique", "--match"]),
])
def test_each_argument_has_help(cmd, names, capsys):
    help_output = help_text([cmd], capsys)
    # argparse indents each argument by two spaces and wraps its help at a deeper indent.
    labels_with_help = re.findall(r"^  (\S[^\n]*?)(?: {2,}|\n {3,})\w", help_output, re.MULTILINE)
    documented = {word for label in labels_with_help for word in label.split()}
    assert set(names) <= documented
    if cmd == "convert":
        description = " ".join(help_output.split())
        assert "default output (-t omml) is OMML text" in description
        assert "XML" in description and "mathshuttle copy to paste into Office" in description


def test_convert_literal_uses_omml_by_default(capsys):
    assert main(["convert", "-e", "x^2"]) == 0
    output = capsys.readouterr()
    assert "<m:sSup>" in output.out and "<m:t>x</m:t>" in output.out
    assert not output.err


def test_empty_expression_never_reads_stdin(monkeypatch, capsys):
    monkeypatch.setattr(sys, "stdin", io.StringIO("y"))
    assert main(["convert", "--expr", ""]) == 1
    assert "input is empty" in capsys.readouterr().err


def test_copy_literal_uses_the_supplied_text(monkeypatch):
    copied = []
    monkeypatch.setattr(clipboard, "copy_mathml_for_office", copied.append)
    assert main(["copy", "--expr", "$x$ and $y$", "--index", "2"]) == 0
    assert len(copied) == 1 and "<mi>y</mi>" in copied[0]


@pytest.mark.parametrize("cmd", ["convert", "copy"])
@pytest.mark.parametrize("arguments", [["-e", "x", "input.tex"], ["input.tex", "--expr", "x"]])
def test_literal_and_file_are_exclusive(cmd, arguments, monkeypatch, capsys):
    monkeypatch.setattr(clipboard, "copy_mathml_for_office", lambda _: pytest.fail("conflicting inputs were accepted"))
    with pytest.raises(SystemExit) as stop:
        main([cmd, *arguments])
    assert stop.value.code == 2
    error = capsys.readouterr().err.split("error:")[-1]
    assert "FILE" in error and "--expr" in error


@pytest.mark.parametrize("value", ["x^2", "a name", r"\frac{a}{b}", "x_i", "{x}", "$x$", "a=b", "2+5"])
def test_equation_used_as_a_file_name(value, tmp_path, monkeypatch, capsys):
    monkeypatch.chdir(tmp_path)
    assert main(["convert", value]) == 1
    error = capsys.readouterr().err
    assert "taken as a file name" in error
    assert "mathshuttle convert -e " in error and value in error.split(" -e ")[-1]


def test_existing_file_with_a_mathy_name_is_read(tmp_path, monkeypatch, capsys):
    monkeypatch.chdir(tmp_path)
    (tmp_path / "x^2").write_text("y", encoding="utf-8")
    assert main(["convert", "x^2", "-t", "latex", "--wrapper", "bare"]) == 0
    assert capsys.readouterr().out == "y\n"


@pytest.mark.parametrize("name", ["paper.tex", "notes.docx", "talk.PPTX", "notes.md", "existing", "-draft.tex"])
def test_document_in_command_position_suggests_extract(name, tmp_path, monkeypatch, capsys):
    monkeypatch.chdir(tmp_path)
    (tmp_path / "existing").touch()
    with pytest.raises(SystemExit) as stop:
        main([name])
    assert stop.value.code == 2
    argument = "-- -draft.tex" if name == "-draft.tex" else name
    assert f"mathshuttle extract {argument}" in capsys.readouterr().err


@pytest.mark.parametrize("arg, diagnostic", [("conver", "invalid choice"), ("--unknown", "unrecognized arguments")])
def test_unknown_command_or_option_shows_help_hint(arg, diagnostic, capsys):
    with pytest.raises(SystemExit) as stop:
        main([arg])
    assert stop.value.code == 2
    error = capsys.readouterr().err
    assert diagnostic in error and "see mathshuttle --help" in error


@pytest.mark.parametrize("tty", [False, True])
def test_stdin_hint_before_reading_only_at_a_terminal(tty, monkeypatch, capsys):
    class Input(io.StringIO):
        def isatty(self):
            return tty

        def read(self):
            prompt = capsys.readouterr().err.splitlines()
            if tty:
                eof = "Ctrl+Z then Enter" if sys.platform == "win32" else "Ctrl+D"
                assert len(prompt) == 1 and "keyboard" in prompt[0] and eof in prompt[0]
            else:
                assert not prompt
            return super().read()

    monkeypatch.setattr(sys, "stdin", Input("x"))
    assert main(["convert", "-t", "latex", "--wrapper", "bare"]) == 0
    assert capsys.readouterr().out == "x\n"


@pytest.fixture
def paper(tmp_path):
    path = tmp_path / "paper.tex"
    path.write_text("\\section{First}\n$x$\n\\section{Middle}\n$\\UnknownCommand{x}$\n\\section{Last}\n$y$\n",
                    encoding="utf-8")
    return str(path)


@pytest.mark.parametrize("fmt, comment", [
    ("mathml", "<!-- %s -->"), ("omml", "<!-- %s -->"), ("typst", "// %s"), ("eqn", '.\\" %s'),
])
def test_extract_continues_after_an_unconvertible_equation(paper, fmt, comment, capsys):
    assert main(["extract", paper, "-t", fmt]) == 1
    output = capsys.readouterr()
    sections = output.out.strip().split("\n\n")
    assert len(sections) == 3
    reason = r"texmath does not know \UnknownCommand"  # (diagnose.explained, the short form)
    assert sections[1].splitlines() == [comment % "[2] Middle", comment % ("Could not be converted: " + reason)]
    for block, location, variable in [(sections[0], "[1] First", "x"), (sections[2], "[3] Last", "y")]:
        header, equation = block.split("\n", 1)
        assert header == comment % location
        if fmt in ("mathml", "omml"):
            root = ET.fromstring(equation)
            assert "".join(root.itertext()).strip() == variable
        else:
            assert re.search(r"\b" + variable + r"\b", equation)
    assert output.err.splitlines() == [
        "3 equation(s) in paper.tex",
        "[2] Middle: " + reason,
        "1 of 3 equation(s) in paper.tex could not be converted",
    ]


def test_extract_default_latex_preserves_unknown_commands(paper, capsys):
    assert main(["extract", paper]) == 0
    output = capsys.readouterr()
    assert r"\UnknownCommand{x}" in output.out and "% [3] Last" in output.out
    assert output.err == "3 equation(s) in paper.tex\n"


def test_extract_lists_every_failure_in_order(paper, monkeypatch, capsys):
    def reject(eqs, *args):
        return [ConversionError("\nUnsupported symbol\nAdditional details") for eq in eqs]

    monkeypatch.setattr("mathshuttle.__main__.convert_equations", reject)
    assert main(["extract", paper, "-t", "mathml"]) == 1
    output = capsys.readouterr()
    assert output.out.count("<!-- Could not be converted: Unsupported symbol -->") == 3
    assert output.err.splitlines()[1:] == [
        "[1] First: Unsupported symbol",
        "[2] Middle: Unsupported symbol",
        "[3] Last: Unsupported symbol",
        "3 of 3 equation(s) in paper.tex could not be converted",
    ]


def test_extract_filters_preserve_positions_counts_and_aux_numbers(tmp_path, capsys):
    path = tmp_path / "locations.tex"
    path.write_text(r"""\section{Intro}
$y$ $x$ \[x\]
\section{Method}
\[x\] $x$ \[x\]
\begin{equation} L = 0 \label{eq:loss} \end{equation}
""", encoding="utf-8")
    path.with_suffix(".aux").write_text(r"\newlabel{eq:loss}{{2.1}{17}{Method}{equation.2.1}{}}", encoding="utf-8")
    assert main(["extract", str(path), "--unique", "--match", "mEtHod"]) == 0
    out = capsys.readouterr().out
    assert out.count("% [") == 2
    assert "% [4] Method  (3x)\n" in out
    assert "% [7] Method  [eq:loss]  (2.1)  (1x)\n" in out

    word = tmp_path / "filtered.docx"
    assert main(["extract", str(path), "--display", "--match", "X", "--unique", "--docx", str(word)]) == 0
    assert capsys.readouterr().out == "% [3] Intro  (3x)\n\\[ x \\]\n\n"
    from mathshuttle.documents import read_document
    assert len(read_document(str(word)).found) == 1
    import zipfile
    with zipfile.ZipFile(str(word)) as z:  # the Word file says what the listing says
        assert "[3] Intro (3x)" in " ".join(re.sub(r"<[^>]+>", " ", z.read("word/document.xml").decode()).split())
    whole = tmp_path / "all.docx"
    assert main(["extract", str(path), "--docx", str(whole)]) == 0
    capsys.readouterr()
    with zipfile.ZipFile(str(whole)) as z:
        assert "[7] Method [eq:loss] (2.1)" in " ".join(re.sub(r"<[^>]+>", " ", z.read("word/document.xml").decode()).split())

    assert main(["extract", str(path), "--display", "--match", "2.1"]) == 0
    assert capsys.readouterr().out.startswith("% [7] Method  [eq:loss]  (2.1)\n")
    assert main(["extract", str(path), "--match", "absent"]) == 0
    output = capsys.readouterr()
    assert output.out == "" and output.err.startswith("0 equation(s)")


def test_location_uses_the_active_label_with_a_number(tmp_path, capsys):
    path = tmp_path / "labels.tex"
    path.write_text("\\section{Bounds}\n\\begin{equation}\nx % \\label{old}\n"
                    "\\label {current}\n\\end{equation}", encoding="utf-8")
    path.with_suffix(".aux").write_text("\\newlabel{old}{{7}{9}}\n\\newlabel{current}{{2.3}{9}}", encoding="utf-8")
    assert main(["extract", str(path)]) == 0
    assert capsys.readouterr().out.splitlines()[0] == "% [1] Bounds  [current]  (2.3)"
