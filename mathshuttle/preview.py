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

_tools: Optional[dict] = None


def tools() -> Optional[dict]:
    """{"latex": path, "dvipng": path, "miktex": bool, "packages": preamble} or None without LaTeX."""
    global _tools
    if _tools is None:
        latex, dvipng = shutil.which("latex"), shutil.which("dvipng")
        if not latex or not dvipng:
            _tools = {}
        else:
            try:
                version = subprocess.run([latex, "--version"], capture_output=True, timeout=20,
                                         creationflags=_FLAGS).stdout.decode("utf-8", "replace")
            except (OSError, subprocess.TimeoutExpired):
                version = ""
            packages = PACKAGES
            kpsewhich = shutil.which("kpsewhich")
            for name in OPTIONAL_PACKAGES:
                try:
                    found = kpsewhich and subprocess.run([kpsewhich, name + ".sty"], capture_output=True,
                                                         timeout=20, creationflags=_FLAGS).stdout.strip()
                except (OSError, subprocess.TimeoutExpired):
                    found = False
                if found:
                    packages += "\n\\usepackage{%s}" % name
            _tools = {"latex": latex, "dvipng": dvipng, "miktex": "MiKTeX" in version, "packages": packages}
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
    for name in pngs[keep:] + [n for n in names if not n.endswith(".png")]:
        try:
            os.remove(name)
        except OSError:
            pass


def math_line(tex: str, display: bool) -> str:
    """An equation's TeX as one line of maths: environments become their inner form (align -> aligned),
    numbering commands go (LaTeX rejects \\tag in $...$)."""
    from .latex_wrap import wrap
    tex = re.sub(r"\\(?:label|tag\*?)\s*\{[^{}]*\}|\\(?:nonumber|notag)(?![a-zA-Z@])", "", tex)
    inline = wrap(tex, "dollar", False)  # $...$, with \begin{aligned} for an align
    return "$\\displaystyle %s$" % inline[1:-1] if display else inline


def render(equations: Sequence, definitions: Sequence[str] = (), dpi: int = 130,
           work: Optional[str] = None) -> str:
    """A PNG of `equations` (objects with .tex and .display), one below the other, with `definitions` in
    the preamble.  Cached by content."""
    t = tools()
    if t is None:
        raise PreviewError("no LaTeX installation (latex and dvipng) on PATH")
    work = work or cache_dir()
    lines = [_LINE % math_line(eq.tex, eq.display) for eq in equations]
    body = lines[0] if len(lines) == 1 else "\\begin{tabular}{@{}c@{}}%s\\end{tabular}" % "\\\\[8pt]\n".join(lines)
    source = TEMPLATE % {"packages": t["packages"], "definitions": "\n".join(definitions), "body": body}
    key = hashlib.sha1(("%d\0%s" % (dpi, source)).encode("utf-8")).hexdigest()[:20]
    png = os.path.join(work, key + ".png")
    if os.path.exists(png):
        return png
    tex = os.path.join(work, key + ".tex")
    with open(tex, "w", encoding="utf-8") as f:
        f.write(source)
    args = [t["latex"], "-interaction=nonstopmode", "-halt-on-error"]
    if t["miktex"]:
        args.append("-disable-installer")  # never a package-install dialog
    try:
        p = subprocess.run(args + [key + ".tex"], cwd=work, capture_output=True, timeout=40, creationflags=_FLAGS)
        if p.returncode:
            log = p.stdout.decode("utf-8", "replace")
            errors = [ln for ln in log.splitlines() if ln.startswith("!")]
            context = re.search(r"^l\.\d+ (.*)$", log, re.M)
            at = context.group(1) if context else ""
            at = at.replace("\\MathShuttleEdge", "").replace("\\hspace{1pt}", "").replace("$\\displaystyle ", "$")
            raise PreviewError("LaTeX: " + (errors[0][2:] if errors else "failed") +
                               (" (at: %s)" % at.strip()[-60:] if at.strip() else ""))
        p = subprocess.run([t["dvipng"], "-D", str(dpi), "-T", "tight", "-bg", "White", "-q", "-o", png,
                            key + ".dvi"], cwd=work, capture_output=True, timeout=40, creationflags=_FLAGS)
        if p.returncode or not os.path.exists(png):
            raise PreviewError("dvipng failed: " + p.stderr.decode("utf-8", "replace").strip()[-200:])
        return png
    except subprocess.TimeoutExpired:
        raise PreviewError("LaTeX did not finish within 40 s")
    except OSError as e:
        raise PreviewError("Could not run LaTeX: %s" % e)
    finally:
        for ext in (".aux", ".log", ".dvi", ".tex"):
            try:
                os.remove(os.path.join(work, key + ext))
            except OSError:
                pass


def definitions_for(tex: str, macros) -> List[str]:
    """The user's definitions an equation uses, for the preamble (as LaTeX takes them)."""
    return [d.source for d in macros.needed(tex)] if macros else []
