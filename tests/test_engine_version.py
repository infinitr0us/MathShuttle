"""texmath version detection: the binary scan, following a Scoop shim, and the `version` command.

The scan / shim tests use throw-away files; `test_version_command` needs the real pandoc.
"""
import os
import re
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from mathshuttle import pandoc_engine as pe  # noqa: E402
from mathshuttle.__main__ import main  # noqa: E402


def test_scan_finds_the_package_id(tmp_path):
    exe = tmp_path / "pandoc.exe"
    exe.write_bytes(b"\x00junk\x00texmath-0.13.2.2-1a2b3c4d5e6f\x00pandoc-3.11\x00")
    assert pe._scan_texmath_version(str(exe)) == "0.13.2.2"


@pytest.mark.parametrize("content", [b"", b"no package id in here", b"texmath-x.y texmath-1"])
def test_scan_without_package_id(tmp_path, content):
    exe = tmp_path / "pandoc.exe"
    exe.write_bytes(content)
    assert pe._scan_texmath_version(str(exe)) is None


def test_scan_missing_file(tmp_path):
    assert pe._scan_texmath_version(str(tmp_path / "missing.exe")) is None


@pytest.mark.parametrize("quoted", [True, False])
def test_scoop_shim_is_followed_for_the_scan_only(tmp_path, monkeypatch, quoted):
    real = tmp_path / "apps" / "pandoc" / "current" / "pandoc.exe"
    real.parent.mkdir(parents=True)
    real.write_bytes(b"texmath-0.13.2.2")
    shims = tmp_path / "shims"
    shims.mkdir()
    shim = shims / "pandoc.exe"
    shim.write_bytes(b"generic launcher, no package id")
    (shims / "pandoc.shim").write_text('path = "%s"\n' % real if quoted else "path = %s\n" % real, encoding="utf-8")
    monkeypatch.setattr(pe, "_pandoc_path", str(shim))
    monkeypatch.setattr(pe, "_texmath_version", None)
    assert pe.find_pandoc() == str(shim)  # conversions keep going through the launcher
    assert pe.pandoc_binary() == str(real)
    assert pe.texmath_version() == "0.13.2.2"


def test_launcher_without_shim_file_reports_unknown(tmp_path, monkeypatch):
    launcher = tmp_path / "pandoc.exe"
    launcher.write_bytes(b"forwards to pandoc, carries no package id")
    monkeypatch.setattr(pe, "_pandoc_path", str(launcher))
    monkeypatch.setattr(pe, "_pandoc_version", "pandoc 3.11")
    monkeypatch.setattr(pe, "_texmath_version", None)
    assert pe.pandoc_binary() == str(launcher)
    assert pe.texmath_version() is None
    assert pe.engine_description() == "pandoc 3.11  (texmath version unknown)"
    report = pe.engine_report().splitlines()
    assert report[:2] == ["pandoc 3.11  (texmath version unknown)", str(launcher)]
    assert "MATHSHUTTLE_PANDOC" in report[2]


def test_engine_report_shows_shim_and_target(tmp_path, monkeypatch):
    real = tmp_path / "real" / "pandoc.exe"
    real.parent.mkdir()
    real.write_bytes(b"texmath-0.13.2.2")
    shim = tmp_path / "pandoc.exe"
    shim.write_bytes(b"launcher")
    (tmp_path / "pandoc.shim").write_text('path = "%s"\n' % real, encoding="utf-8")
    monkeypatch.setattr(pe, "_pandoc_path", str(shim))
    monkeypatch.setattr(pe, "_pandoc_version", "pandoc 3.11")
    monkeypatch.setattr(pe, "_texmath_version", None)
    assert pe.engine_report() == "pandoc 3.11  (texmath 0.13.2.2)\n%s  ->  %s" % (shim, real)


def test_version_command(capsys):
    assert main(["version"]) == 0
    out = capsys.readouterr().out.splitlines()
    assert re.fullmatch(r"pandoc \S+  \(texmath \d+(?:\.\d+)+\)", out[0]), out[0]
    assert os.path.isfile(out[1].split("  ->  ")[0])
    assert len(out) == 2  # no "version unknown" hint for the real binary
