"""Install small local releases and run their version command on the host platform."""
from __future__ import annotations

import concurrent.futures
import functools
import hashlib
import http.server
import io
import os
import ssl
import stat
import subprocess
import sys
import tarfile
import threading
import urllib.error
import zipfile
from pathlib import Path
from types import SimpleNamespace

import pytest

from mathshuttle import pandoc_engine as engine, pandoc_fetch as fetch
from mathshuttle.__main__ import main
from mathshuttle.paths import local_data_dir


EXE = "pandoc.exe" if os.name == "nt" else "pandoc"


@pytest.fixture(scope="session")
def program(tmp_path_factory):
    if os.name != "nt":
        return b'#!/bin/sh\n[ "$1" = "--version" ] || exit 1\necho "pandoc ${TEST_PANDOC_VERSION:-3.14}"\n'
    # Windows includes the .NET Framework compiler. Build a real console executable once.
    folder = tmp_path_factory.mktemp("version-program")
    source = folder / "Version.cs"
    source.write_text('class Version { static int Main(string[] args) {'
                      'if (args.Length != 1 || args[0] != "--version") return 1;'
                      'System.Console.WriteLine("pandoc " + '
                      '(System.Environment.GetEnvironmentVariable("TEST_PANDOC_VERSION") ?? "3.14"));'
                      'return 0; } }', encoding="ascii")
    compiler = Path(os.environ["SystemRoot"]) / "Microsoft.NET/Framework/v4.0.30319/csc.exe"
    exe = folder / EXE
    subprocess.run([str(compiler), "/nologo", "/out:" + str(exe), str(source)], check=True)
    return exe.read_bytes()


@pytest.fixture(autouse=True)
def data_folder(monkeypatch, tmp_path):
    for key in ("LOCALAPPDATA", "HOME", "USERPROFILE", "XDG_DATA_HOME"):
        monkeypatch.setenv(key, str(tmp_path))
    monkeypatch.delenv("TEST_PANDOC_VERSION", raising=False)
    monkeypatch.setenv("no_proxy", "127.0.0.1")
    root = tmp_path / "Library/Application Support" if sys.platform == "darwin" else tmp_path
    assert Path(local_data_dir()) == root / "MathShuttle"
    return root / "MathShuttle/pandoc" / fetch.PANDOC_VERSION


@pytest.fixture
def server(tmp_path):
    folder = tmp_path / "served"
    folder.mkdir()

    class Handler(http.server.SimpleHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def send_header(self, key, value):
            if key == "Content-Length" and self.path.startswith("/no-length"):
                return
            super().send_header(key, value)

        def copyfile(self, source, outputfile):
            try:
                super().copyfile(source, outputfile)
            except (BrokenPipeError, ConnectionResetError, ConnectionAbortedError):
                pass  # an installer cancelled its request

    with http.server.ThreadingHTTPServer(("127.0.0.1", 0), functools.partial(Handler, directory=str(folder))) as httpd:
        thread = threading.Thread(target=httpd.serve_forever, kwargs={"poll_interval": 0.02})
        thread.start()
        try:
            yield folder, "http://127.0.0.1:%d/" % httpd.server_port
        finally:
            httpd.shutdown()
            thread.join()


def release(server, filename, members):
    """Serve a ZIP or tar.gz. A member with None for its content is a symbolic link."""
    folder, base = server
    path = folder / filename
    if filename.endswith(".zip"):
        with zipfile.ZipFile(path, "w") as archive:
            for name, content in members:
                info = zipfile.ZipInfo(name)
                info.create_system = 3
                info.external_attr = (stat.S_IFLNK if content is None else stat.S_IFREG) << 16
                archive.writestr(info, b"elsewhere" if content is None else content)
    else:
        with tarfile.open(path, "w:gz") as archive:
            for name, content in members:
                info = tarfile.TarInfo(name)
                if content is None:
                    info.type = tarfile.SYMTYPE
                    info.linkname = "elsewhere"
                    archive.addfile(info)
                else:
                    info.size = len(content)
                    archive.addfile(info, io.BytesIO(content))
    body = path.read_bytes()
    return {"url": base + filename, "asset": fetch.Asset(filename, len(body), hashlib.sha256(body).hexdigest())}


@pytest.mark.parametrize("filename,prefix", [("release.zip", "pandoc-3.14/"), ("release.tar.gz", "pandoc-3.14/bin/")])
def test_installs_only_binary_and_licences(server, data_folder, filename, prefix, program):
    assert fetch.installed_pandoc() is None
    for version in ("3.9", "3.14.1", "notes"):
        other = data_folder.parent / version
        other.mkdir(parents=True)
        (other / "keep").touch()
    source = release(server, filename, [(prefix + EXE, program), ("share/COPYRIGHT.txt", b"copyright"),
                                        ("COPYING", b"licence"), ("extras/unrelated", b"skip")])
    exe = Path(fetch.install(**source))
    assert exe == data_folder / EXE
    assert fetch.installed_pandoc() == str(exe)
    assert subprocess.check_output([str(exe), "--version"]).splitlines()[0] == b"pandoc 3.14"
    if os.name != "nt":
        assert stat.S_IMODE(exe.stat().st_mode) == 0o755
    assert {p.name for p in data_folder.iterdir()} == {EXE, "COPYRIGHT.txt", "COPYING"}
    assert (data_folder / "COPYING").read_bytes() == b"licence"
    assert not (data_folder.parent / "3.9").exists()
    assert all((data_folder.parent / name / "keep").exists() for name in ("3.14.1", "notes"))


@pytest.mark.parametrize("field", ["size", "sha256"])
def test_mismatch_leaves_no_files(server, data_folder, field, program):
    source = release(server, "release.zip", [(EXE, program)])
    source["asset"] = source["asset"]._replace(**{field: 1 if field == "size" else "0" * 64})
    with pytest.raises(fetch.FetchError, match="release file.*[Nn]othing was installed"):
        fetch.install(**source)
    assert list(data_folder.iterdir()) == []


@pytest.mark.parametrize("filename", ["release.zip", "no-length.zip"])
def test_download_limit_with_or_without_header(server, data_folder, monkeypatch, filename, program):
    source = release(server, filename, [(EXE, program)])
    monkeypatch.setattr(fetch, "_MAX_DOWNLOAD", 100)
    with pytest.raises(fetch.FetchError, match="too large"):
        fetch.install(**source)
    assert list(data_folder.iterdir()) == []


@pytest.mark.parametrize("cancel_early", [True, False])
def test_progress_and_cancellation(server, data_folder, cancel_early, program):
    source = release(server, "padded.zip", [(EXE, program), ("padding", bytes(1024 * 1024))])
    calls = []
    cancel = threading.Event()

    def progress(done, total):
        calls.append((done, total))
        if cancel_early and 0 < done < total:
            cancel.set()

    if cancel_early:
        with pytest.raises(fetch.FetchCancelled):
            fetch.install(progress, cancel, **source)
        assert list(data_folder.iterdir()) == []
    else:
        fetch.install(progress, cancel, **source)
        assert calls[-1][0] == source["asset"].size
    assert calls == sorted(calls)
    assert all(0 <= done <= total == source["asset"].size for done, total in calls)


@pytest.mark.parametrize("filename", ["release.zip", "release.tar.gz"])
def test_unsafe_members_are_never_installed(server, data_folder, filename, program, tmp_path):
    outside = tmp_path / "outside"
    source = release(server, filename, [("../../evil", b"escape"), (outside.as_posix(), b"escape"),
                                       ("../../" + EXE, program), ("/" + EXE, program),
                                       ("one/two/three/four/" + EXE, program), ("bin/" + EXE, None),
                                       ("COPYING", b"licence")])
    with pytest.raises(fetch.FetchError):
        fetch.install(**source)
    assert not outside.exists() and not list(tmp_path.rglob("evil"))
    assert list(data_folder.iterdir()) == []


def test_invalid_version_does_not_publish_or_remove_a_working_copy(server, data_folder, program, monkeypatch):
    source = release(server, "release.zip", [(EXE, program), ("COPYING", b"licence")])
    monkeypatch.setenv("TEST_PANDOC_VERSION", "3.140")
    with pytest.raises(fetch.FetchError, match="3.14"):
        fetch.install(**source)
    assert list(data_folder.iterdir()) == []
    (data_folder / EXE).write_bytes(b"previous copy")
    with pytest.raises(fetch.FetchError):
        fetch.install(**source)
    assert (data_folder / EXE).read_bytes() == b"previous copy"


def test_concurrent_installers_publish_a_working_binary(server, data_folder, program):
    source = release(server, "release.zip", [(EXE, program), ("COPYING", b"licence")])
    with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:
        jobs = [pool.submit(fetch.install, **source) for _ in range(2)]
        assert all(job.result() == str(data_folder / EXE) for job in jobs)
    assert (data_folder / EXE).read_bytes() == program
    assert {p.name for p in data_folder.iterdir()} == {EXE, "COPYING"}


@pytest.mark.parametrize("system,machine,filename", [
    ("win32", "AMD64", "windows-x86_64.zip"), ("win32", "ARM64", "windows-x86_64.zip"),
    ("darwin", "arm64", "arm64-macOS.zip"), ("darwin", "x86_64", "x86_64-macOS.zip"),
    ("linux", "AMD64", "linux-amd64.tar.gz"), ("linux", "aarch64", "linux-arm64.tar.gz"),
])
def test_platform_assets(monkeypatch, system, machine, filename):
    monkeypatch.setattr(sys, "platform", system)
    monkeypatch.setattr(fetch.platform, "machine", lambda: machine)
    assert fetch._release_asset().name == "pandoc-3.14-" + filename


def test_unsupported_platform(monkeypatch):
    monkeypatch.setattr(fetch.platform, "machine", lambda: "mips")
    with pytest.raises(fetch.FetchError, match="manually.*MATHSHUTTLE_PANDOC"):
        fetch.install(url="http://127.0.0.1:1/")


def test_not_enough_space(data_folder, monkeypatch):
    monkeypatch.setattr(fetch.shutil, "disk_usage", lambda _: SimpleNamespace(free=0))
    with pytest.raises(fetch.FetchError, match="400 MB"):
        fetch.install(url="http://127.0.0.1:1/")
    assert not list(data_folder.glob("*"))


def test_certificate_error_without_curl(server, data_folder, program, monkeypatch):
    source = release(server, "release.zip", [(EXE, program)])

    def reject(url):
        raise urllib.error.URLError(ssl.SSLCertVerificationError("certificate verify failed"))

    monkeypatch.setattr(fetch, "_open_url", reject)
    monkeypatch.setattr(fetch, "_system_curl", lambda: None)
    with pytest.raises(fetch.FetchError, match="certificate"):
        fetch.install(**source)
    assert list(data_folder.iterdir()) == []


def test_curl_fallback_checks_the_same_release(server, data_folder, program, monkeypatch):
    source = release(server, "release.zip", [(EXE, program)])
    archive = server[0] / "release.zip"
    popen = subprocess.Popen
    calls = []

    def reject(url):
        raise urllib.error.URLError(ssl.SSLCertVerificationError("certificate verify failed"))

    def local_curl(args, **kwargs):
        if args[0] == "test-curl":
            calls.append(args)
            args = [sys.executable, "-c", "import pathlib, sys; sys.stdout.buffer.write("
                    "pathlib.Path(sys.argv[1]).read_bytes())", str(archive)]
        return popen(args, **kwargs)

    monkeypatch.setattr(fetch, "_open_url", reject)
    monkeypatch.setattr(fetch, "_system_curl", lambda: "test-curl")
    monkeypatch.setattr(subprocess, "Popen", local_curl)
    assert fetch.install(**source) == str(data_folder / EXE)
    args, = calls
    assert "-fL" in args and args[args.index("--proto") + 1] == "=https"
    source["asset"] = source["asset"]._replace(sha256="0" * 64)
    with pytest.raises(fetch.FetchError, match="release file"):
        fetch.install(**source)
    assert [p.name for p in data_folder.iterdir()] == [EXE]


@pytest.mark.parametrize("system,subfolder", [("win32", "AppData/Local"), ("darwin", "Library/Application Support"),
                                             ("linux", ".local/share")])
def test_default_data_folder(system, subfolder, tmp_path, monkeypatch):
    monkeypatch.delenv("LOCALAPPDATA", raising=False)
    monkeypatch.delenv("XDG_DATA_HOME", raising=False)
    monkeypatch.setattr(sys, "platform", system)
    assert Path(local_data_dir()) == tmp_path / subfolder / "MathShuttle"


@pytest.fixture
def isolated_engine(monkeypatch, tmp_path):
    for key in ("MATHSHUTTLE_PANDOC", "EQUATION_TOOL_PANDOC", "ProgramFiles", "ProgramFiles(x86)"):
        monkeypatch.delenv(key, raising=False)
    monkeypatch.setenv("PATH", str(tmp_path))
    monkeypatch.setattr(engine, "_MACOS_DIRS", ())
    monkeypatch.setattr(engine, "__file__", str(tmp_path / "package/engine.py"))
    engine.reset_pandoc()
    yield
    engine.reset_pandoc()


def test_engine_priority_report_and_reset(server, isolated_engine, tmp_path, data_folder, program):
    with pytest.raises(engine.PandocNotFound, match=r"(?s)Download.*mathshuttle get-pandoc"):
        engine.find_pandoc()
    managed = fetch.install(**release(server, "release.zip", [(EXE, program)]))
    assert engine.find_pandoc() == managed
    assert "installed by mathshuttle" in engine.engine_report().lower()
    directory = tmp_path / "on-path"
    directory.mkdir()
    external = directory / EXE
    external.write_bytes(program)
    external.chmod(0o755)
    with pytest.MonkeyPatch.context() as patch:
        patch.setenv("PATH", str(directory))
        patch.setenv("TEST_PANDOC_VERSION", "4.0")
        engine._api_version = [1]
        engine._mathml_flag = "--mathml"
        assert engine.find_pandoc() == managed
        engine.reset_pandoc()
        assert engine._api_version is None and engine._mathml_flag is None and engine._texmath_version is None
        assert engine.find_pandoc() == str(external)
        assert engine.pandoc_version() == "pandoc 4.0"
        assert "installed by mathshuttle" not in engine.engine_report().lower()


@pytest.mark.parametrize("force", [True, False])
def test_cli_installs_local_release(server, data_folder, isolated_engine, monkeypatch, capsys, program, force, tmp_path):
    if force:
        external = tmp_path / EXE
        external.write_bytes(program)
        monkeypatch.setenv("MATHSHUTTLE_PANDOC", str(external))
    source = release(server, "release.zip", [(EXE, program)])
    install = fetch.install
    monkeypatch.setattr(fetch, "install", lambda **kwargs: install(**source, **kwargs))
    assert main(["get-pandoc"] + (["--force"] if force else [])) == 0
    output = capsys.readouterr()
    assert "pandoc 3.14" in output.out and str(data_folder / EXE) in output.out
    assert "100%" in output.err
    assert fetch.installed_pandoc() == str(data_folder / EXE)


def test_cli_already_installed(isolated_engine, tmp_path, monkeypatch, capsys):
    external = tmp_path / EXE
    external.touch()
    monkeypatch.setattr(fetch, "install", lambda **kwargs: pytest.fail("unexpected download"))
    assert main(["get-pandoc"]) == 0
    out = capsys.readouterr().out
    assert "already" in out and str(external) in out


@pytest.mark.parametrize("failure", [fetch.FetchError("download failed"), KeyboardInterrupt()])
def test_cli_download_errors(isolated_engine, monkeypatch, capsys, failure):
    def fail(**kwargs):
        raise failure

    monkeypatch.setattr(fetch, "install", fail)
    status = main(["get-pandoc"])
    assert status == (1 if isinstance(failure, fetch.FetchError) else 130)
    output = capsys.readouterr()
    assert output.out == ""
    assert ("download failed" if isinstance(failure, fetch.FetchError) else "cancel") in output.err.lower()
