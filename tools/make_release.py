"""Zip a built MathShuttle for a GitHub release, with its licence, README and third-party notices.

    .venv\\Scripts\\python tools\\make_release.py windows      # after build_exe.ps1: dist\\MathShuttle\\
    .venv-build/bin/python tools/make_release.py macos      # after build_app.sh: dist/MathShuttle.app

Run it with the Python that built the app (its build venv): THIRD-PARTY-NOTICES.txt is assembled from
that Python's licence, the licence of the Tcl/Tk its tkinter uses and PyInstaller's, which is what the
build put into the app.  The zip is dist/MathShuttle-<version>-<windows-x64|macos-arm64>.zip, holding a
folder MathShuttle with the app, LICENSE.txt, README.md and THIRD-PARTY-NOTICES.txt; its SHA-256 is
printed for the release notes.  On macOS the zip is made with ditto, which keeps the app's symbolic
links and signature.
"""
from __future__ import annotations

import hashlib
import os
import platform
import subprocess
import sys
import sysconfig
import tempfile
import zipfile
from importlib import metadata

ROOT = os.path.abspath(os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
sys.path.insert(0, ROOT)

from mathshuttle import __version__  # noqa: E402

RULE = "=" * 78


def _first_file(paths) -> str:
    for p in paths:
        if p and os.path.isfile(p):
            return p
    raise SystemExit("not found, looked at:\n  " + "\n  ".join(p for p in paths if p))


def _python_licence() -> str:
    base = sys.base_prefix
    ver = "python%d.%d" % sys.version_info[:2]
    stdlib = sysconfig.get_path("stdlib", vars={"installed_base": base, "base": base})  # python3.13t too
    return _first_file([os.path.join(base, "LICENSE.txt"), os.path.join(base, "LICENSE_PYTHON.txt"),
                        os.path.join(stdlib, "LICENSE.txt"), os.path.join(base, "lib", ver, "LICENSE.txt"),
                        os.path.join(base, "Lib", "LICENSE.txt"), os.path.join(base, "share", "doc", ver, "LICENSE.txt")])


def _arch(target: str) -> str:
    """The architecture of the build (the app has the build Python's): x64 / arm64 / x86 on Windows,
    the machine on macOS."""
    if target == "windows":
        plat = sysconfig.get_platform()  # win-amd64, win-arm64, win32
        return {"win-amd64": "x64", "win-arm64": "arm64", "win32": "x86"}.get(plat, plat.replace("win-", ""))
    return platform.machine() or "arm64"


def _replace_atomically(out: str, write) -> None:
    """write(path) makes the zip under a temporary name; it replaces `out` only once it is complete."""
    part = out + ".part"
    if os.path.exists(part):
        os.remove(part)
    write(part)
    os.replace(part, out)


def _tcl_tk_licences() -> "tuple[str, str, str]":
    """(Tcl's licence file, Tk's, the Tcl version) for the Tcl/Tk that tkinter uses: the installation's
    license.terms where it has them (Anaconda keeps Tk's), else the copies in tools/licenses, taken from
    the Tcl and Tk 9.0 sources (uv's and other standalone Pythons ship none)."""
    import tkinter
    tcl = tkinter.Tcl()
    library = tcl.eval("info library")  # e.g. .../tcl/tcl8.6 or .../lib/tcl9.0
    parent = os.path.dirname(library)
    tk = os.path.join(parent, os.path.basename(library).replace("tcl", "tk"))
    ours = os.path.join(ROOT, "tools", "licenses")
    found = []
    for candidates, fallback in (
            ([os.path.join(library, "license.terms"), os.path.join(parent, "license.terms")], "tcl-license.terms"),
            ([os.path.join(tk, "license.terms"), os.path.join(tk, "demos", "license.terms")], "tk-license.terms")):
        found.append(next((p for p in candidates if os.path.isfile(p)), os.path.join(ours, fallback)))
    return found[0], found[1], tcl.eval("info patchlevel")


def _pyinstaller_licence() -> "tuple[str, str]":
    dist = metadata.distribution("pyinstaller")
    files = [str(dist.locate_file(f)) for f in (dist.files or []) if os.path.basename(str(f)) == "COPYING.txt"]
    return _first_file(files), dist.version


def notices(target: str) -> str:
    python = _python_licence()
    tcl, tk, tcl_version = _tcl_tk_licences()
    pyinstaller, pyinstaller_version = _pyinstaller_licence()
    if target == "windows":
        install = "winget install JohnMacFarlane.Pandoc"
        where_python = "python%d%d.dll and its standard library" % sys.version_info[:2]
        where_tcl = "the Tcl / Tk DLLs, _tcl_data, _tk_data"
        launcher = "MathShuttle.exe's launcher"
    else:
        install = "brew install pandoc"
        where_python = "the interpreter in MathShuttle.app and its standard library"
        where_tcl = "the Tcl / Tk libraries and scripts in MathShuttle.app"
        launcher = "MathShuttle.app's launcher"
    parts = ["MathShuttle includes the following third-party software in this build.\n"
             "pandoc is NOT included: the window offers to download pandoc's own release, or install it "
             "yourself (%s); it is GPL-2.0-or-later.\n" % install]
    for title, path in (
            ("Python %s (%s)" % (platform.python_version(), where_python), python),
            ("Tcl %s (%s)" % (tcl_version, where_tcl), tcl),
            ("Tk %s (%s)" % (tcl_version, where_tcl), tk),
            ("Sun Valley ttk theme 2.6.1 by rdbende (mathshuttle/sv_theme: the look of the window on Windows "
             "and Linux)", os.path.join(ROOT, "mathshuttle", "sv_theme", "LICENSE")),
            ("PyInstaller %s bootloader (%s): GPL 2.0 with the PyInstaller exception, which allows "
             "distributing it with programs under any licence" % (pyinstaller_version, launcher), pyinstaller)):
        with open(path, encoding="utf-8", errors="replace") as f:
            parts.append("%s\n%s\n%s\n%s" % (RULE, title, RULE, f.read().strip() + "\n"))
    return "\n".join(parts)


def _extras(folder: str, target: str) -> None:
    newline = "\r\n" if target == "windows" else "\n"
    with open(os.path.join(ROOT, "LICENSE"), encoding="utf-8") as f:
        licence = f.read()
    with open(os.path.join(ROOT, "README.md"), encoding="utf-8") as f:
        readme = f.read().replace("](LICENSE)", "](LICENSE.txt)")  # the licence's name in the zip
    for name, text in (("LICENSE.txt", licence), ("THIRD-PARTY-NOTICES.txt", notices(target))):
        with open(os.path.join(folder, name), "w", encoding="utf-8", newline=newline) as f:
            f.write(text)
    with open(os.path.join(folder, "README.md"), "w", encoding="utf-8", newline="\n") as f:
        f.write(readme)


def _sha256(path: str) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def main(argv) -> int:
    if len(argv) != 1 or argv[0] not in ("windows", "macos"):
        print(__doc__)
        return 2
    target = argv[0]
    dist = os.path.join(ROOT, "dist")
    app = os.path.join(dist, "MathShuttle" if target == "windows" else "MathShuttle.app")
    bundled_pandoc = os.path.join(app, "pandoc.exe") if target == "windows" else \
        os.path.join(app, "Contents", "MacOS", "pandoc")
    if os.path.exists(bundled_pandoc):
        # the notices say pandoc is not included, and a zip with pandoc (GPL) in it needs its source
        # offered with it: release zips are built without it
        raise SystemExit("%s holds pandoc: build the release without it (build_exe.ps1 without -BundlePandoc)"
                         % app)
    out = os.path.join(dist, "MathShuttle-%s-%s-%s.zip" % (__version__, target, _arch(target)))
    if target == "windows":
        if not os.path.isfile(os.path.join(app, "MathShuttle.exe")):
            raise SystemExit("build it first: .\\build_exe.ps1")

        def write(path: str) -> None:
            with tempfile.TemporaryDirectory() as tmp:
                _extras(tmp, target)
                with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as z:
                    for folder, _, files in os.walk(app):
                        for name in files:
                            full = os.path.join(folder, name)
                            z.write(full, os.path.join("MathShuttle", os.path.relpath(full, app)))
                    for name in sorted(os.listdir(tmp)):
                        z.write(os.path.join(tmp, name), os.path.join("MathShuttle", name))
    else:
        if not os.path.isdir(app):
            raise SystemExit("build it first: ./build_app.sh")

        def write(path: str) -> None:
            with tempfile.TemporaryDirectory() as tmp:
                folder = os.path.join(tmp, "MathShuttle")
                os.mkdir(folder)
                subprocess.run(["ditto", app, os.path.join(folder, "MathShuttle.app")], check=True)
                _extras(folder, target)
                subprocess.run(["ditto", "-c", "-k", "--sequesterRsrc", "--keepParent", folder, path], check=True)
    _replace_atomically(out, write)
    print("%s\nSHA-256 %s" % (out, _sha256(out)))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
