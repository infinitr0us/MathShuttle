"""pandoc discovery, clipboard text decoding and the CLI's I/O encoding.

None of these write the clipboard; `test_cli_*` run the real pandoc in a child process.
"""
import os
import subprocess
import sys

import pytest

ROOT = os.path.join(os.path.dirname(__file__), "..")
sys.path.insert(0, ROOT)

from mathshuttle import clipboard  # noqa: E402
from mathshuttle import pandoc_engine as pe  # noqa: E402


# ---- pandoc discovery ------------------------------------------------------------------------------
def test_mistyped_env_var_is_an_error(monkeypatch, tmp_path):
    monkeypatch.setattr(pe, "_pandoc_path", None)
    monkeypatch.setenv("MATHSHUTTLE_PANDOC", str(tmp_path / "pandoc.exe"))
    with pytest.raises(pe.PandocNotFound) as e:
        pe.find_pandoc()
    assert "MATHSHUTTLE_PANDOC" in str(e.value)


def test_pandoc_that_cannot_be_started_is_a_conversion_error(monkeypatch, tmp_path):
    # e.g. pandoc.exe removed or replaced by `winget upgrade` while the app runs
    def run(*args, **kwargs):
        raise FileNotFoundError(2, "The system cannot find the file specified")
    monkeypatch.setattr(pe, "_pandoc_path", str(tmp_path / "pandoc.exe"))
    monkeypatch.setattr(pe.subprocess, "run", run)
    with pytest.raises(pe.ConversionError) as e:
        pe._run(["--version"], b"")
    assert str(e.value).startswith("Could not run ")


def test_current_directory_is_not_searched(monkeypatch, tmp_path):
    exe = "pandoc.exe" if sys.platform == "win32" else "pandoc"
    (tmp_path / exe).write_bytes(b"planted")
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("MATHSHUTTLE_PANDOC", raising=False)
    monkeypatch.setenv("PATH", os.pathsep.join([".", "", os.environ.get("PATH", "")]))
    candidates = [os.path.normcase(os.path.abspath(p)) for p in pe._candidate_paths()]  # which() says .\pandoc.EXE
    assert os.path.normcase(str(tmp_path / exe)) not in candidates


@pytest.mark.skipif(sys.platform != "darwin", reason="macOS")
def test_homebrew_is_searched_without_it_on_path(monkeypatch):
    # an app started from the Finder has launchd's PATH, without Homebrew's bin
    monkeypatch.delenv("MATHSHUTTLE_PANDOC", raising=False)
    monkeypatch.setenv("PATH", "/usr/bin:/bin:/usr/sbin:/sbin")
    from mathshuttle import pandoc_fetch  # (a copy get-pandoc installed comes after them: not here)
    monkeypatch.setattr(pandoc_fetch, "installed_pandoc", lambda: None)
    candidates = list(pe._candidate_paths())
    assert candidates[-2:] == ["/opt/homebrew/bin/pandoc", "/usr/local/bin/pandoc"]
    assert not any(p.endswith(".exe") for p in candidates)


@pytest.mark.skipif(sys.platform != "darwin", reason="macOS")
def test_pandoc_not_found_says_how_to_install_it(monkeypatch, tmp_path):
    monkeypatch.setattr(pe, "_pandoc_path", None)
    monkeypatch.delenv("MATHSHUTTLE_PANDOC", raising=False)
    monkeypatch.setattr(pe, "_candidate_paths", lambda: iter([str(tmp_path / "pandoc")]))
    with pytest.raises(pe.PandocNotFound, match="brew install pandoc"):
        pe.find_pandoc()


# ---- clipboard backend ----------------------------------------------------------------------------------
def test_clipboard_without_a_backend_is_a_clipboard_error(monkeypatch):
    # what the app catches and reports, also where there is no clipboard code (not an import error)
    monkeypatch.setattr(clipboard, "_backend_module", None)
    monkeypatch.setattr(clipboard.sys, "platform", "linux")
    with pytest.raises(clipboard.ClipboardError, match="Windows and macOS only"):
        clipboard.get_text()


# ---- clipboard decoding (no clipboard access) ---------------------------------------------------------
@pytest.mark.parametrize("raw", [
    "<math><mi>α</mi></math>".encode("utf-8") + b"\0garbage",
    b"\xef\xbb\xbf" + "<math><mi>α</mi></math>".encode("utf-8") + b"\0",
    "\ufeff<math><mi>α</mi></math>".encode("utf-16-le") + b"\0\0garbage",
    "<math><mi>α</mi></math>".encode("utf-16-le") + b"\0\0",
])
def test_decode_registered_text_format(raw):
    assert clipboard._decode_text(raw) == "<math><mi>α</mi></math>"


# ---- CLI encoding --------------------------------------------------------------------------------------
def _run_cli(args, stdin=b""):
    env = {k: v for k, v in os.environ.items() if k not in ("PYTHONIOENCODING", "PYTHONUTF8")}
    return subprocess.run([sys.executable, "-m", "mathshuttle", *args], input=stdin, capture_output=True,
                          cwd=ROOT, env=env, timeout=60)


def test_cli_output_to_a_pipe_is_utf8(tmp_path):
    # a pipe uses the ANSI code page by default (cp1252 / cp936), in which ℝ cannot be encoded
    src = tmp_path / "eq.tex"
    src.write_bytes(b"\xef\xbb\xbf\\mathbb{R}^n")  # with a BOM, as Notepad may save it
    proc = _run_cli(["convert", "-t", "mathml", str(src)])
    assert proc.returncode == 0, proc.stderr.decode("utf-8", "replace")
    assert '<mi mathvariant="double-struck">ℝ</mi>' in proc.stdout.decode("utf-8")


def test_cli_stdin_is_read_as_utf8():
    proc = _run_cli(["convert", "-t", "latex", "--wrapper", "bare"], "$\\text{café 公式}$".encode("utf-8"))
    assert proc.returncode == 0, proc.stderr.decode("utf-8", "replace")
    assert proc.stdout.decode("utf-8").strip() == "\\text{café 公式}"


def test_cli_reads_a_utf16_file(tmp_path, capsys):
    # what `>` writes in Windows PowerShell 5.1 (it used to end in a UnicodeDecodeError traceback)
    from mathshuttle.__main__ import main
    src = tmp_path / "eq.tex"
    src.write_bytes("\\text{café} \\\\\r\n x".encode("utf-16"))  # with its BOM, and CRLF
    assert main(["convert", "-t", "latex", "--wrapper", "bare", str(src)]) == 0
    assert capsys.readouterr().out == "\\text{café} \\\\\n x\n"


@pytest.mark.parametrize("content", [None, b"\xff\x00 not text \xc3"])
def test_cli_unreadable_file_is_a_clean_error(tmp_path, capsys, content):
    from mathshuttle.__main__ import main
    src = tmp_path / "eq.tex"
    if content is not None:
        src.write_bytes(content)
    assert main(["convert", str(src)]) == 1
    err = capsys.readouterr().err
    assert err.startswith("error: ") and str(src) in err and "Traceback" not in err


@pytest.mark.skipif(sys.platform not in ("win32", "darwin"), reason="no clipboard backend here")
def test_cli_clipboard_error_is_a_clean_error(monkeypatch, capsys):
    # no clipboard access: the clipboard read is replaced by one that fails
    from mathshuttle.__main__ import main

    def busy():
        raise clipboard.ClipboardError("Could not open the clipboard (another application is holding it)")
    monkeypatch.setattr(clipboard, "read_equation_from_office", busy)
    assert main(["paste"]) == 1
    assert capsys.readouterr().err == "error: Could not open the clipboard (another application is holding it)\n"
