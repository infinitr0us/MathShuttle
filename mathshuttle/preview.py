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
import struct
import subprocess
import sys
import tempfile
import time
from typing import List, Optional, Sequence, Tuple

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
    """The available LaTeX engines, PNG converters and preamble, or None without a rendering route."""
    global _tools
    if _tools is None:
        latex, dvipng = _which("latex"), _which("dvipng")
        pdflatex = _which("pdflatex")
        gs = next((path for name in ("mgs", "gs", "gswin64c", "gswin32c") if (path := _which(name))), None)
        cairo, ppm = _which("pdftocairo"), _which("pdftoppm")
        if not ((latex and dvipng) or (pdflatex and (gs or cairo or ppm))):
            _tools = {}
        else:
            env = _tex_env(latex or pdflatex)
            try:
                version = subprocess.run([latex or pdflatex, "--version"], capture_output=True, timeout=20,
                                         creationflags=_FLAGS, env=env).stdout.decode("utf-8", "replace")
            except (OSError, subprocess.TimeoutExpired):
                version = ""
            miktex = "MiKTeX" in version or "miktex" in (latex or pdflatex).lower()
            if miktex:
                env = dict(env or os.environ, MIKTEX_MPM_AUTOINSTALL="0")
            # MiKTeX's kpsewhich may install a missing package. Its native findtexmf
            # only looks up files unless explicitly called with -must-exist.
            lookup = _which("findtexmf" if miktex else "kpsewhich")
            packages = PACKAGES
            for name in OPTIONAL_PACKAGES:
                if _has_package(lookup, name, env):
                    packages += "\n\\usepackage{%s}" % name
            _tools = {"latex": latex, "dvipng": dvipng, "pdflatex": pdflatex, "gs": gs, "cairo": cairo, "ppm": ppm,
                      "miktex": miktex, "packages": packages, "lookup": lookup, "env": env}
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
        args.append("--disable-installer")
    return args + [name]


_INSTALLED: dict = {}


def _has_package(lookup, package, env):
    if not lookup:
        return False
    try:
        p = subprocess.run([lookup, package + ".sty"], cwd=tempfile.gettempdir(), capture_output=True, timeout=20,
                           creationflags=_FLAGS, env=env)
        return p.returncode == 0 and os.path.isfile(os.fsdecode(p.stdout.strip()))
    except (OSError, ValueError, subprocess.TimeoutExpired):
        return False


def installed(package: str) -> bool:
    """Whether the LaTeX installation already has the package, remembered between renders."""
    t = tools()
    if t is None:
        return False
    if package not in _INSTALLED:
        _INSTALLED[package] = _has_package(t["lookup"], package, t.get("env"))
    return _INSTALLED[package]


def render(equations: Sequence, definitions: Sequence[str] = (), dpi: int = 130,
           work: Optional[str] = None, packages: Sequence[str] = ()) -> str:
    """A PNG of `equations` (objects with .tex and .display), one below the other, with `definitions` in
    the preamble, and `packages` (physics, braket...: those the equations' commands come from) where the
    installation has them.  Cached by content."""
    t = tools()
    if t is None:
        raise PreviewError("no LaTeX installation for drawing a preview was found")
    lines = [_LINE % math_line(eq.tex, eq.display) for eq in equations]
    body = lines[0] if len(lines) == 1 else "\\begin{tabular}{@{}c@{}}%s\\end{tabular}" % "\\\\[8pt]\n".join(lines)
    extra = "".join("\n\\usepackage{%s}" % p for p in packages if installed(p))
    source = TEMPLATE % {"packages": t["packages"] + extra, "definitions": "\n".join(definitions), "body": body}
    return _render_source(t, source, dpi, work or cache_dir(), pdf=not (t["latex"] and t["dvipng"]))


_PICTURE_DPI = 300
_DRAWING_PACKAGES = frozenset("""
amsmath amssymb amsfonts mathtools bm mathrsfs dsfont bbm esint stmaryrd physics braket
siunitx mhchem tikz-cd tikz xy xcolor cancel slashed tensor amscd amsbsy amstext
""".split())
_PICTURE_PACKAGES = ((r"\\begin\s*\{tikzcd\}", "tikz-cd", ""),
                     (r"\\(?:ce|pu)(?![A-Za-z@])", "mhchem", "version=4"),
                     (r"\\xymatrix(?![A-Za-z@])", "xy", "all"))
_FILE_COMMAND = re.compile(r"\\(?:@*input|include|subfile|import|subimport|endinput|InputIfFileExists|IfFileExists|"
                           r"includegraphics|verbatiminput|lstinputlisting|openin|openout|read|write|"
                           r"pdf[a-z]*(?:file|image)[a-z]*|usepackage|RequirePackage|catcode|csname)"
                           r"(?![A-Za-z@])|\^\^")


def _picture_preamble(tex, packages, defaults):
    """Load installed maths packages, their literal options and the paper's TikZ libraries."""
    from .compat import _TIKZ_LIBRARIES, _group_end, _package_requests
    names = {}
    libraries = []
    for line in packages:
        if _TIKZ_LIBRARIES.fullmatch(line):
            if line not in libraries:
                libraries.append(line)
            continue
        _, requested, options = next(_package_requests(line), ("", line, ""))
        if options and (not re.fullmatch(r"[\w=,.\s:{}+-]+", options)
                        or _group_end("{" + options + "}", 0) != len(options) + 2):
            options = ""
        for name in requested.split(","):
            name = name.strip()
            if name not in _DRAWING_PACKAGES or not installed(name):
                continue
            opts = names.setdefault(name, [])
            if options and options not in opts:
                opts.append(options)
    for pattern, name, options in _PICTURE_PACKAGES:
        if re.search(pattern, tex):
            if not installed(name):
                raise PreviewError("the LaTeX package %s is missing" % name)
            if not names.get(name):
                names[name] = [options] if options else []
    before = [r"\PassOptionsToPackage{%s}{%s}" % (",".join(opts), name)
              for name, opts in names.items() if opts]
    loads = [r"\usepackage{%s}" % name for name in names]
    if "tikz" in names or "tikz-cd" in names:
        loads.extend(libraries)
    return "\n".join(before + [r"\documentclass[border=1pt,12pt]{standalone}", defaults] + loads)


def render_picture(tex: str, display: bool, definitions: Sequence[str] = (), packages: Sequence[str] = (),
                   work: Optional[str] = None) -> Tuple[str, float, float]:
    """A print-resolution PNG and its width and height in inches, including diagram arrows and labels."""
    t = tools()
    if t is None:
        raise PreviewError("no LaTeX installation was found")
    if not (t["pdflatex"] and (t["gs"] or t["cairo"] or t["ppm"])):
        raise PreviewError("drawing a picture needs pdfLaTeX and Ghostscript or Poppler")
    used = "\n".join([*definitions, tex])
    if _FILE_COMMAND.search(used):
        raise PreviewError("the equation needs a file or a command that cannot be used in a picture")
    preamble = _picture_preamble(used, packages, t["packages"])
    source = "\n".join([preamble, *definitions, r"\begin{document}", math_line(tex, display), r"\end{document}"])
    png = _render_source(t, source, _PICTURE_DPI, work or cache_dir(), pdf=True)
    width, height = _png_size(png)
    return png, width / _PICTURE_DPI, height / _PICTURE_DPI


def _png_size(path):
    with open(path, "rb") as f:
        header = f.read(24)
    if len(header) != 24 or header[:8] != b"\x89PNG\r\n\x1a\n" or header[12:16] != b"IHDR":
        raise PreviewError("the converter did not write a PNG")
    return struct.unpack(">II", header[16:])


def _pdf_to_png(t, dpi, job):
    """Rasterise the cropped PDF page, trying the other installed converter if the first fails."""
    commands = []
    if t["gs"]:
        commands.append([t["gs"], "-dSAFER", "-dBATCH", "-dNOPAUSE", "-dFirstPage=1", "-dLastPage=1",
                         "-sDEVICE=png16m", "-dTextAlphaBits=4", "-dGraphicsAlphaBits=4", "-r%d" % dpi,
                         "-sOutputFile=eq.png", "eq.pdf"])
    for converter in (t["cairo"], t["ppm"]):
        if converter:
            commands.append([converter, "-png", "-singlefile", "-r", str(dpi), "-f", "1", "-l", "1", "eq.pdf", "eq"])
    for args in commands:
        try:
            p = subprocess.run(args, cwd=job, capture_output=True, timeout=40, creationflags=_FLAGS, env=t.get("env"))
            if p.returncode == 0 and all(_png_size(os.path.join(job, "eq.png"))):
                return
        except (OSError, subprocess.TimeoutExpired, PreviewError):
            continue
    raise PreviewError("the PDF could not be drawn as a PNG by Ghostscript or Poppler")


def _render_source(t, source, dpi, work, pdf):
    key = hashlib.sha1(("%s\0%d\0%s" % ("pdf" if pdf else "dvi", dpi, source)).encode("utf-8")).hexdigest()[:20]
    png = os.path.join(work, key + ".png")
    if os.path.exists(png):
        return png
    # each job in a folder of its own (two renders of the same equation must not delete each other's
    # files); the finished picture is moved into the cache in one step
    job = tempfile.mkdtemp(prefix="job-", dir=work)
    try:
        with open(os.path.join(job, "eq.tex"), "w", encoding="utf-8") as f:
            f.write(source)
        engine = dict(t, latex=t["pdflatex"]) if pdf else t
        env = t.get("env")
        if pdf:
            env = dict(env or os.environ, openin_any="p", openout_any="p",
                       MIKTEX_CORE_ALLOWUNSAFEINPUTFILES="false", MIKTEX_CORE_ALLOWUNSAFEOUTPUTFILES="false")
        p = subprocess.run(latex_command(engine, "eq.tex"), cwd=job, capture_output=True, timeout=40,
                           creationflags=_FLAGS, env=env)
        if p.returncode:
            log = p.stdout.decode("utf-8", "replace")
            errors = [ln for ln in log.splitlines() if ln.startswith("!")]
            context = re.search(r"^l\.\d+ (.*)$", log, re.M)
            at = context.group(1) if context else ""
            at = at.replace("\\MathShuttleEdge", "").replace("\\hspace{1pt}", "").replace("$\\displaystyle ", "$")
            raise PreviewError("LaTeX: " + (errors[0][2:] if errors else "failed") +
                               (" (at: %s)" % at.strip()[-60:] if at.strip() else ""))
        if pdf:
            _pdf_to_png(t, dpi, job)
        else:
            p = subprocess.run([t["dvipng"], "-D", str(dpi), "-T", "tight", "-bg", "White", "-q", "-o", "eq.png",
                                "eq.dvi"], cwd=job, capture_output=True, timeout=40, creationflags=_FLAGS,
                               env=t.get("env"))
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
