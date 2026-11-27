"""A picture of the equation as LaTeX sets it, with the LaTeX installation found on PATH (latex + dvipng).

The preview shows what the LaTeX side looks like - the input when converting from LaTeX, the result when
converting to LaTeX - so a typo or a wrong Office -> LaTeX reading is seen before it goes into a paper.
It uses the user's macros and the usual maths packages; without a LaTeX installation there is no preview.
"""
from __future__ import annotations

import hashlib
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
from typing import List, Optional, Sequence

_FLAGS = 0x08000000 if sys.platform == "win32" else 0  # CREATE_NO_WINDOW
PACKAGES = r"\usepackage{amsmath,amssymb,amsfonts,mathtools,bm}"
# packages that may be missing from a small installation: loaded only if present
OPTIONAL_PACKAGES = ("bbm", "dsfont")
# Each equation is set as $\displaystyle ...$ in the standalone class's own tight box (display maths would
# take the whole line).  dvipng's tight crop loses a stretchy delimiter that is the leftmost ink (\left|),
# so every line starts and ends with an invisible white dot; the picture is on white.
TEMPLATE = r"""\documentclass[border=4pt,12pt]{standalone}
%(packages)s
\usepackage{xcolor}
\newcommand{\MathShuttleEdge}{\textcolor{white}{\rule{0.4pt}{0.4pt}}}
%(definitions)s
\begin{document}
%(body)s
\end{document}
"""
_LINE = r"\MathShuttleEdge\hspace{1pt}%s\hspace{1pt}\MathShuttleEdge"

# An app started from the Finder or the Dock gets launchd's PATH (/usr/bin:/bin:/usr/sbin:/sbin), not
# the shell's: MacTeX's /Library/TeX/texbin, and Homebrew's bin for a TeX Live from there, are searched
# after PATH, and the TeX programs are run with their own directory on PATH (see _tex_env).
_MACOS_DIRS = ("/Library/TeX/texbin", "/opt/homebrew/bin", "/usr/local/bin") if sys.platform == "darwin" else ()

_tools: Optional[dict] = None


def _which(name: str) -> Optional[str]:
    if not _MACOS_DIRS:
        return shutil.which(name)
    return shutil.which(name, path=os.pathsep.join([os.environ.get("PATH", os.defpath), *_MACOS_DIRS]))


def _tex_env(latex: str) -> Optional[dict]:
    """The environment for latex, dvipng and kpsewhich: this process's own (None), but on macOS with
    their directory added to PATH when it is not there.  kpathsea finds mktexpk through PATH when a
    font has no outlines (bbm, for \\mathbbm) and must be made; without it dvipng leaves the glyph blank."""
    path = os.environ.get("PATH", os.defpath)
    directory = os.path.dirname(latex)
    if not _MACOS_DIRS or directory in path.split(os.pathsep):
        return None
    return dict(os.environ, PATH=os.pathsep.join([directory, path]))


def tools() -> Optional[dict]:
    """{"latex": path, "dvipng": path, "miktex": bool, "packages": preamble, "env": for subprocess} or None
    without LaTeX."""
    global _tools
    if _tools is None:
        latex, dvipng = _which("latex"), _which("dvipng")
        if not latex or not dvipng:
            _tools = {}
        else:
            env = _tex_env(latex)
            try:
                version = subprocess.run([latex, "--version"], capture_output=True, timeout=20,
                                         creationflags=_FLAGS, env=env).stdout.decode("utf-8", "replace")
            except (OSError, subprocess.TimeoutExpired):
                version = ""
            packages = PACKAGES
            kpsewhich = _which("kpsewhich")
            for name in OPTIONAL_PACKAGES:
                try:
                    found = kpsewhich and subprocess.run([kpsewhich, name + ".sty"], capture_output=True,
                                                         timeout=20, creationflags=_FLAGS, env=env).stdout.strip()
                except (OSError, subprocess.TimeoutExpired):
                    found = False
                if found:
                    packages += "\n\\usepackage{%s}" % name
            _tools = {"latex": latex, "dvipng": dvipng, "miktex": "MiKTeX" in version, "packages": packages,
                      "env": env}
    return _tools or None


class PreviewError(RuntimeError):
    """The equation could not be set; the message is LaTeX's first error line."""


def cache_dir() -> str:
    path = os.path.join(tempfile.gettempdir(), "MathShuttle-preview")
    os.makedirs(path, exist_ok=True)
    return path


def prune(path: Optional[str] = None, keep: int = 200) -> None:
    """Keep the cache small: the `keep` newest pictures."""
    path = path or cache_dir()
    try:
        names = sorted((os.path.join(path, n) for n in os.listdir(path)), key=os.path.getmtime, reverse=True)
    except OSError:
        return
    pngs = [n for n in names if n.endswith(".png")]
    for name in pngs[keep:]:
        try:
            os.remove(name)
        except OSError:
            pass
    # leftovers of jobs that were cut off (a closed app): only old ones, a running job may be young
    import time
    for name in names:
        if os.path.isdir(name) and time.time() - os.path.getmtime(name) > 3600:
            shutil.rmtree(name, ignore_errors=True)


def _without_numbering(tex: str) -> str:
    """`tex` without \\label{..}, \\tag{..}, \\tag*{..} (balanced arguments: \\tag{\\text{A}}), \\nonumber, \\notag."""
    from .compat import _argument
    pattern = re.compile(r"\\(?:label|tag\*?|nonumber|notag)(?![a-zA-Z@])")
    while True:
        m = pattern.search(tex)
        if not m:
            return tex
        end = m.end()
        if m.group().startswith(("\\label", "\\tag")):
            _, end = _argument(tex, m.end())
        tex = tex[:m.start()] + tex[end:]


def math_line(tex: str, display: bool) -> str:
    """An equation's TeX as one line of maths: environments become their inner form (align -> aligned),
    numbering commands go (LaTeX rejects \\tag in $...$)."""
    from .latex_wrap import wrap
    inline = wrap(_without_numbering(tex), "dollar", False)  # $...$, with \begin{aligned} for an align
    return "$\\displaystyle %s$" % inline[1:-1] if display else inline


def latex_command(t: dict, name: str) -> List[str]:
    """The latex command line: no shell escape whatever the installation's default (MiKTeX's is
    "restricted"), and no MiKTeX package-install dialog."""
    args = [t["latex"], "-interaction=nonstopmode", "-halt-on-error", "-no-shell-escape"]
    if t["miktex"]:
        args.append("-disable-installer")
    return args + [name]


_INSTALLED: dict = {}


def installed(package: str) -> bool:
    """Whether the LaTeX installation has `package` (kpsewhich; remembered)."""
    t = tools()
    if t is None:
        return False
    if package not in _INSTALLED:
        kpsewhich = _which("kpsewhich")
        try:
            _INSTALLED[package] = bool(kpsewhich and subprocess.run(
                [kpsewhich, package + ".sty"], capture_output=True, timeout=20, creationflags=_FLAGS,
                env=t.get("env")).stdout.strip())
        except (OSError, subprocess.TimeoutExpired):
            _INSTALLED[package] = False
    return _INSTALLED[package]


def render(equations: Sequence, definitions: Sequence[str] = (), dpi: int = 130,
           work: Optional[str] = None, packages: Sequence[str] = ()) -> str:
    """A PNG of `equations` (objects with .tex and .display), one below the other, with `definitions` in
    the preamble, and `packages` (physics, braket...: those the equations' commands come from) where the
    installation has them.  Cached by content."""
    t = tools()
    if t is None:
        raise PreviewError("no LaTeX installation (latex and dvipng) on PATH")
    work = work or cache_dir()
    lines = [_LINE % math_line(eq.tex, eq.display) for eq in equations]
    body = lines[0] if len(lines) == 1 else "\\begin{tabular}{@{}c@{}}%s\\end{tabular}" % "\\\\[8pt]\n".join(lines)
    extra = "".join("\n\\usepackage{%s}" % p for p in packages if installed(p))
    source = TEMPLATE % {"packages": t["packages"] + extra, "definitions": "\n".join(definitions), "body": body}
    key = hashlib.sha1(("%d\0%s" % (dpi, source)).encode("utf-8")).hexdigest()[:20]
    png = os.path.join(work, key + ".png")
    if os.path.exists(png):
        return png
    # each job in a folder of its own (two renders of the same equation must not delete each other's
    # files); the finished picture is moved into the cache in one step
    job = tempfile.mkdtemp(prefix="job-", dir=work)
    with open(os.path.join(job, "eq.tex"), "w", encoding="utf-8") as f:
        f.write(source)
    try:
        p = subprocess.run(latex_command(t, "eq.tex"), cwd=job, capture_output=True, timeout=40, creationflags=_FLAGS,
                           env=t.get("env"))
        if p.returncode:
            log = p.stdout.decode("utf-8", "replace")
            errors = [ln for ln in log.splitlines() if ln.startswith("!")]
            context = re.search(r"^l\.\d+ (.*)$", log, re.M)
            at = context.group(1) if context else ""
            at = at.replace("\\MathShuttleEdge", "").replace("\\hspace{1pt}", "").replace("$\\displaystyle ", "$")
            raise PreviewError("LaTeX: " + (errors[0][2:] if errors else "failed") +
                               (" (at: %s)" % at.strip()[-60:] if at.strip() else ""))
        p = subprocess.run([t["dvipng"], "-D", str(dpi), "-T", "tight", "-bg", "White", "-q", "-o", "eq.png",
                            "eq.dvi"], cwd=job, capture_output=True, timeout=40, creationflags=_FLAGS, env=t.get("env"))
        if p.returncode or not os.path.exists(os.path.join(job, "eq.png")):
            raise PreviewError("dvipng failed: " + p.stderr.decode("utf-8", "replace").strip()[-200:])
        for attempt in range(5):
            try:
                os.replace(os.path.join(job, "eq.png"), png)
                break
            except PermissionError:
                # Windows: another render of the same equation put it there and the window is reading
                # it (or a virus scanner holds it) - that picture is the same one
                if os.path.exists(png):
                    break
                if attempt == 4:
                    raise
                time.sleep(0.1)
        return png
    except subprocess.TimeoutExpired:
        raise PreviewError("LaTeX did not finish within 40 s")
    except OSError as e:
        raise PreviewError("Could not run LaTeX: %s" % e)
    finally:
        shutil.rmtree(job, ignore_errors=True)


def definitions_for(tex: str, macros) -> List[str]:
    """The user's definitions an equation uses, for the preamble (as LaTeX takes them)."""
    return [d.source for d in macros.needed(tex)] if macros else []
