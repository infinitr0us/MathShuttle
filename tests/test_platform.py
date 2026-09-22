"""pandoc discovery, clipboard text decoding and the CLI's I/O encoding.

None of these write the clipboard; `test_cli_*` run the real pandoc in a child process.
"""
import os
import subprocess
import sys

import pytest

ROOT = os.path.join(os.path.dirname(__file__), "..")
sys.path.insert(0, ROOT)

from equation_tool import clipboard  # noqa: E402
from equation_tool import pandoc_engine as pe  # noqa: E402


# ---- pandoc discovery ------------------------------------------------------------------------------
def test_mistyped_env_var_is_an_error(monkeypatch, tmp_path):
    monkeypatch.setattr(pe, "_pandoc_path", None)
    monkeypatch.setenv("EQUATION_TOOL_PANDOC", str(tmp_path / "pandoc.exe"))
    with pytest.raises(pe.PandocNotFound) as e:
        pe.find_pandoc()
    assert "EQUATION_TOOL_PANDOC" in str(e.value)


def test_current_directory_is_not_searched(monkeypatch, tmp_path):
    (tmp_path / "pandoc.exe").write_bytes(b"planted")
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("EQUATION_TOOL_PANDOC", raising=False)
    monkeypatch.setenv("PATH", os.pathsep.join([".", "", os.environ.get("PATH", "")]))
    candidates = [os.path.normcase(os.path.abspath(p)) for p in pe._candidate_paths()]  # which() says .\pandoc.EXE
    assert os.path.normcase(str(tmp_path / "pandoc.exe")) not in candidates


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
    return subprocess.run([sys.executable, "-m", "equation_tool", *args], input=stdin, capture_output=True,
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
