"""Conversion engine: drives pandoc (which embeds texmath) through its JSON AST.

Every equation is normalised to pandoc's own representation, a ``Math`` inline
holding TeX plus an inline/display flag.  Reading a format means asking pandoc
to parse a tiny document and collecting the ``Math`` nodes; writing means
feeding a one-paragraph JSON document to the relevant pandoc writer and cutting
the equation back out of its output.

Supported formats:  read  latex | mathml | omml
                    write mathml | omml | eqn | typst   (LaTeX output is handled
                    in :mod:`latex_wrap` because the TeX is already in hand).
"""
from __future__ import annotations

import collections
import copy
import html.entities
import io
import json
import os
import re
import subprocess
import sys
import unicodedata
import uuid
import zipfile
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from typing import List, Optional

from . import tidy as _tidy

MATHML_NS = "http://www.w3.org/1998/Math/MathML"
OMML_NS = "http://schemas.openxmlformats.org/officeDocument/2006/math"
WML_NS = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"
MC_NS = "http://schemas.openxmlformats.org/markup-compatibility/2006"

ET.register_namespace("", MATHML_NS)
ET.register_namespace("m", OMML_NS)
ET.register_namespace("w", WML_NS)

READ_FORMATS = ("latex", "mathml", "omml")
WRITE_FORMATS = ("latex", "mathml", "omml", "eqn", "typst")

_CREATE_NO_WINDOW = 0x08000000 if sys.platform == "win32" else 0
_EXE = "pandoc.exe" if sys.platform == "win32" else "pandoc"
INSTALL_HINT = {"darwin": "brew install pandoc", "win32": "winget install JohnMacFarlane.Pandoc"}.get(
    sys.platform, "your system's package manager")
_DOWNLOAD_HINT = "Click Download in the MathShuttle window or run mathshuttle get-pandoc."
# An app started from the Finder or the Dock gets launchd's PATH (/usr/bin:/bin:/usr/sbin:/sbin), not
# the shell's: Homebrew's bin (Apple silicon, Intel) is searched too, and is where pandoc's own
# installer puts it (/usr/local/bin).
_MACOS_DIRS = ("/opt/homebrew/bin", "/usr/local/bin")


class PandocNotFound(RuntimeError):
    pass


class ConversionError(ValueError):
    """The input could not be converted; the message is meant for the user."""


@dataclass
class Equation:
    tex: str
    display: bool = True


# --- locating / running pandoc ------------------------------------------------------
_pandoc_path: Optional[str] = None
_pandoc_version: Optional[str] = None
_texmath_version: Optional[str] = None
_api_version: Optional[list] = None
_mathml_flag: Optional[str] = None


def reset_pandoc() -> None:
    """Look up the executable, its versions and supported options again on next use."""
    global _pandoc_path, _pandoc_version, _texmath_version, _api_version, _mathml_flag
    _pandoc_path = _pandoc_version = _texmath_version = None
    _api_version = _mathml_flag = None


def _env_pandoc() -> "tuple[str, Optional[str]]":
    """(variable, value) of MATHSHUTTLE_PANDOC, or of EQUATION_TOOL_PANDOC (the tool's former name)."""
    for name in ("MATHSHUTTLE_PANDOC", "EQUATION_TOOL_PANDOC"):
        if os.environ.get(name):
            return name, os.environ[name]
    return "MATHSHUTTLE_PANDOC", None


def _candidate_paths():
    env = _env_pandoc()[1]
    if env:
        yield env
    # next to the frozen executable / this package (lets a copy of pandoc.exe be bundled)
    base = os.path.dirname(sys.executable) if getattr(sys, "frozen", False) else os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    yield os.path.join(base, _EXE)
    yield os.path.join(base, "pandoc", _EXE)
    # PATH, but not the current directory: on Windows shutil.which() looks there first, which
    # would run a pandoc.exe lying in whatever folder the CLI is started from.
    for d in os.environ.get("PATH", "").split(os.pathsep):
        d = d.strip().strip('"')
        if os.path.isabs(d):
            yield os.path.join(d, _EXE)
    for root in (os.environ.get("LOCALAPPDATA"), os.environ.get("ProgramFiles"), os.environ.get("ProgramFiles(x86)")):
        if root:
            yield os.path.join(root, "Pandoc", "pandoc.exe")
    if sys.platform == "darwin":
        for d in _MACOS_DIRS:
            yield os.path.join(d, "pandoc")
    from .pandoc_fetch import installed_pandoc
    installed = installed_pandoc()
    if installed:
        yield installed


def find_pandoc() -> str:
    global _pandoc_path
    if _pandoc_path is None:
        name, env = _env_pandoc()
        if env and not os.path.isfile(env):  # a typo must not silently select some other pandoc
            raise PandocNotFound(f"{name} is set to {env!r}, which is not an existing file. "
                                 f"Correct it, or unset {name} so that MathShuttle looks for pandoc itself.")
        for p in _candidate_paths():
            if p and os.path.isfile(p):
                _pandoc_path = p
                break
        else:
            raise PandocNotFound(
                f"{_EXE} was not found. {_DOWNLOAD_HINT}\n"
                f"You can also install it with {INSTALL_HINT} or set MATHSHUTTLE_PANDOC to the full path of {_EXE}.")
    return _pandoc_path


def _shim_target(path: str) -> Optional[str]:
    """The executable behind a Scoop shim: ``<name>.exe`` is a generic launcher and the
    ``<name>.shim`` file next to it holds ``path = "<real exe>"`` (unquoted in older Scoop)."""
    try:
        with open(os.path.splitext(path)[0] + ".shim", encoding="utf-8-sig") as f:
            for line in f:
                key, sep, value = line.partition("=")
                if sep and key.strip() == "path":
                    target = value.strip().strip('"')
                    return target if os.path.isfile(target) else None
    except OSError:
        pass
    return None


def pandoc_binary() -> str:
    """The pandoc executable itself; `find_pandoc()` (what gets *run*) may be a launcher."""
    path = find_pandoc()
    return _shim_target(path) or path


def pandoc_version() -> str:
    global _pandoc_version
    if _pandoc_version is None:
        out, _ = _run(["--version"], b"")
        _pandoc_version = out.decode("utf-8", "replace").splitlines()[0]
    return _pandoc_version


def _scan_texmath_version(path: str) -> Optional[str]:
    """The ``texmath-<version>`` Haskell package id embedded in a (statically linked) pandoc.  Cabal
    shortens the ids on macOS (vowels dropped, as in ``pndc-cl-3.14-..``): Homebrew's pandoc carries
    ``txmth-<version>``."""
    import mmap
    try:
        with open(path, "rb") as f, mmap.mmap(f.fileno(), 0, access=mmap.ACCESS_READ) as mm:
            m = re.search(rb"(?:texmath|txmth)-(\d+(?:\.\d+)+)", mm)
            return m.group(1).decode() if m else None  # the match reads the mmap: decode before it closes
    except (OSError, ValueError):  # unreadable or empty file
        return None


def texmath_version() -> Optional[str]:
    """Version of the texmath library linked into pandoc (pandoc --version does not report it).

    pandoc is statically linked, so the package id is embedded in the executable; a newer
    texmath only arrives with a newer pandoc.  None when the executable carries no package
    id (a launcher we do not know how to follow, or an unusual build).
    """
    global _texmath_version
    if _texmath_version is None:
        _texmath_version = _scan_texmath_version(pandoc_binary()) or ""
    return _texmath_version or None


def engine_description() -> str:
    """One line for the status bar: ``pandoc 3.11  (texmath 0.13.2.2)``."""
    return "%s  (texmath %s)" % (pandoc_version(), texmath_version() or "version unknown")


def engine_report() -> str:
    """`engine_description()` plus the executable in use (and the binary behind a shim)."""
    from .pandoc_fetch import installed_pandoc
    command, binary = find_pandoc(), pandoc_binary()
    lines = [engine_description(), command if binary == command else "%s  ->  %s" % (command, binary)]
    installed = installed_pandoc()
    if installed and os.path.normcase(os.path.realpath(binary)) == os.path.normcase(os.path.realpath(installed)):
        lines.append("Installed by MathShuttle.")
    if not texmath_version():
        lines.append("texmath version unknown: %s carries no texmath package id (a launcher?). "
                     "Set MATHSHUTTLE_PANDOC to the real %s to inspect it." % (os.path.basename(binary), _EXE))
    return "\n".join(lines)


def _run(args: List[str], stdin: bytes, timeout: float = 60, cwd: Optional[str] = None) -> "tuple[bytes, str]":
    """Run pandoc; `cwd`: where relative paths (\\input{...} in a .tex file) are found."""
    try:
        proc = subprocess.run([find_pandoc(), *args], input=stdin, capture_output=True,
                              timeout=timeout, creationflags=_CREATE_NO_WINDOW, cwd=cwd)
    except subprocess.TimeoutExpired:
        raise ConversionError("pandoc did not finish within %d s" % timeout)
    except OSError as e:  # e.g. pandoc.exe removed or replaced (winget upgrade) while the app runs
        raise ConversionError("Could not run %s: %s" % (find_pandoc(), e))
    err = proc.stderr.decode("utf-8", "replace").replace("\r\n", "\n")
    if proc.returncode != 0:
        raise ConversionError("pandoc failed (exit %d):\n%s" % (proc.returncode, err.strip()))
    return proc.stdout, err


def _api():
    global _api_version
    if _api_version is None:
        out, _ = _run(["-f", "markdown", "-t", "json"], b"")
        _api_version = json.loads(out)["pandoc-api-version"]
    return _api_version


def _mathml_flags() -> List[str]:
    global _mathml_flag
    if _mathml_flag is None:
        try:
            _run(["-f", "markdown", "-t", "html", "--math-method=mathml"], b"")
            _mathml_flag = "--math-method=mathml"
        except ConversionError:
            _mathml_flag = "--mathml"  # pandoc < 3.7 (deprecated spelling)
    return [_mathml_flag]


# The TeX in the message keeps its line breaks (continuation lines indented by two spaces),
# so the part before ", rendering as TeX:" may span several lines.
_TEXMATH_WARNING = re.compile(r"\[WARNING\] Could not convert TeX math [\s\S]*?, rendering as TeX:\n((?:  .*\n?)*)")

# for writers with text output: pandoc writes CRLF on Windows by default
_LF = "--eol=lf"


def _check_texmath_warning(stderr: str) -> None:
    """Raise if texmath rejected the TeX (pandoc then writes the TeX source as text instead)."""
    m = _TEXMATH_WARNING.search(stderr)
    if m:
        detail = "\n".join(line[2:] for line in m.group(1).rstrip("\n").splitlines())
        raise ConversionError("texmath could not parse this LaTeX:\n" + detail)
    if "Could not convert TeX math" in stderr:  # message layout changed in some pandoc release
        raise ConversionError("texmath could not parse this LaTeX:\n" + stderr.strip())


# --- reading --------------------------------------------------------------------------
def strip_tex(tex: str) -> str:
    """`tex` without surrounding white space - and without a control space ``\\ `` left dangling at the
    end (texmath ends ``\\left\\{ ... \\right.\\ ``): stripping its space would leave a lone backslash,
    which escapes whatever delimiter is written after it (``\\right.\\$``).  A backslash the input ends
    with (no space after it) is kept: that is the user's unfinished TeX, which texmath reports."""
    out = tex.lstrip()
    while True:
        stripped = out.rstrip()
        if stripped == out:
            return out
        out = stripped
        if not (out.endswith("\\") and (len(out) - len(out.rstrip("\\"))) % 2):
            return out
        out = out[:-1]  # the backslash of a control space whose space was just stripped


def _collect_math(node, out: List[Equation]) -> None:
    if isinstance(node, dict):
        if node.get("t") == "Math":
            kind, tex = node["c"]
            out.append(Equation(strip_tex(tex), kind["t"] == "DisplayMath"))
            return
        for v in node.values():
            _collect_math(v, out)
    elif isinstance(node, list):
        for v in node:
            _collect_math(v, out)


def _read_json(fmt: str, source: bytes) -> List[Equation]:
    out, _ = _run(["-f", fmt, "-t", "json"], source)
    eqs: List[Equation] = []
    _collect_math(json.loads(out)["blocks"], eqs)
    return eqs


# environments that pandoc's LaTeX reader turns into maths (checked against pandoc 3.11)
_PANDOC_MATH_ENVS = frozenset(
    "equation equation* displaymath math align align* alignat alignat* flalign flalign* eqnarray eqnarray* "
    "gather gather* multline multline* dmath dmath* dgroup dgroup* darray darray* subequations".split())
# mathtools' cases with a text column (compat.py rewrites them for texmath)
_STARRED_CASES = frozenset(("cases*", "dcases*", "rcases*", "drcases*"))
# spaces and comments, which LaTeX skips between a command and its argument
_TEX_GAP = r"(?:\s|%[^\n]*\n)*"
# (\verb|...| and \url{...} hold their text verbatim: a % in them is no comment)
_LATEX_TOKEN = re.compile(r"\\begin" + _TEX_GAP + r"\{([^{}]*)\}|\\verb\*?([^\sa-zA-Z*])[^\n]*?\2"
                          r"|\\(?:url|href)\s*\{[^{}]*\}|\\[a-zA-Z@]+|\\.|%[^\n]*|[{}$]", re.S)
# commands whose argument is text even inside maths, so that it may hold nested $...$
_TEXT_IN_MATH = frozenset(
    "\\text \\mbox \\hbox \\makebox \\fbox \\framebox \\raisebox \\parbox \\textrm \\textit \\textbf \\textsf "
    "\\texttt \\textnormal \\textup \\textsl \\textsc \\textmd \\textcolor \\colorbox \\fcolorbox".split())
# what may stand between a command and its next argument: spaces, [options], TeX's "to 3cm"
_ARG_GAP = re.compile(r"\s*(?:\[[^\[\]{}]*\]\s*)*(?:(?:to|spread)\b[^{}\\$%\[\]]*)?")


_MACRO_DEF = re.compile(r"\\(?:(?:re|provide)?newcommand|DeclareRobustCommand)\*?" + _TEX_GAP +
                        r"(?:\{\s*(\\[a-zA-Z@]+)\s*\}|(\\[a-zA-Z@]+))|\\[gex]?def\s*(\\[a-zA-Z@]+)")


def _text_macros(text: str) -> frozenset:
    """`_TEXT_IN_MATH` plus the macros that `text` defines with a body using one of them
    (``\\newcommand{\\note}[1]{\\mbox{#1}}``)."""
    bodies = {}
    for m in _MACRO_DEF.finditer(text):
        start = text.find("{", m.end())  # after [n] [default] or \def's parameter text
        if start < 0:
            continue
        depth, i = 0, start
        for i in range(start, len(text)):
            depth += {"{": 1, "}": -1}.get(text[i], 0) if text[i - 1:i] != "\\" else 0
            if not depth:
                break
        bodies[m.group(1) or m.group(2) or m.group(3)] = set(re.findall(r"\\[a-zA-Z@]+", text[start:i + 1]))
    commands = set(_TEXT_IN_MATH)
    while True:  # a macro built from another text macro is one too
        more = {name for name, used in bodies.items() if name not in commands and used & commands}
        if not more:
            return frozenset(commands)
        commands |= more


def _has_math_delimiters(text: str) -> bool:
    """True if `text` has $, \\(, \\[ or a maths environment outside every text-command argument.

    In math-mode TeX those can only occur inside the argument of \\text, \\mbox, ... (text in
    the formula), while prose with equations always has one elsewhere - at the top level, or
    in a group such as ``\\emph{for $n$}`` / ``{\\bf ... $x$ ...}``.  So input without them is a
    single bare equation, even when pandoc would find maths nested in it (``\\mbox{for all $n$}``).
    """
    text_commands = _text_macros(text)
    owners: List[Optional[str]] = []  # per open brace group: the command it is an argument of
    in_text = 0  # open groups that are text-command arguments
    owner, end = None, 0  # command whose argument may start next, end of the previous token
    skip_to = 0
    for m in _LATEX_TOKEN.finditer(text):
        if m.start() < skip_to:
            continue
        tok = m.group()
        if owner is not None and not _ARG_GAP.fullmatch(text, end, m.start()):
            owner = None  # something else came between the command and this token
        end = m.end()
        if tok == "{":
            owners.append(owner)
            in_text += owner in text_commands
            owner = None
        elif tok == "}":
            if owners:
                owner = owners.pop()  # \raisebox{1pt}{$x$}: the next group is the same command's
                in_text -= owner in text_commands
        elif tok.startswith("%"):
            pass
        elif m.group(1) in _STARRED_CASES:
            # mathtools' starred cases: their text column holds $...$, which is maths inside text
            close = re.compile(r"\\end" + _TEX_GAP + r"\{%s\}" % re.escape(m.group(1))).search(text, m.end())
            if close:
                skip_to = end = close.end()
            owner = None
        elif not in_text and (tok in ("$", "\\(", "\\[") or m.group(1) in _PANDOC_MATH_ENVS):
            return True
        else:
            owner = tok if tok.startswith("\\") and tok[1:2].isalpha() else None
    return False


def read_latex(text: str, default_display: bool = True) -> List[Equation]:
    """Extract every equation from LaTeX source (:func:`read_latex_text`)."""
    return read_latex_text(text, default_display)[0]


def read_latex_text(text: str, default_display: bool = True) -> "tuple[List[Equation], bool]":
    """Every equation of LaTeX source, or of Markdown text with LaTeX maths in it (a chat answer, notes),
    and whether it was read as Markdown.

    Delimited maths ($...$, \\(...\\), $$...$$, \\[...\\], equation/align/gather/
    multline/eqnarray/... environments, several of them, with prose in between)
    are recognised by pandoc's LaTeX reader; \\newcommand definitions in the
    input are expanded.  Input with no delimiters at all is taken as one bare
    equation in the `default_display` mode (texmath handles its macros itself).

    Text that looks like Markdown (:func:`looks_like_markdown`: "50% of", "$5 and", `code`, **bold**),
    and text that pandoc's LaTeX reader cannot read at all, is read as Markdown too, where % is a
    percent sign and $ next to a number is money.  The Markdown reading is taken unless the LaTeX
    reading found maths that it lost (`$ a + b $` with spaces, which Markdown does not take as maths,
    or an equation after a "1% comment"): then the text was LaTeX after all.
    """
    if not text.strip():
        raise ConversionError("The input is empty.")
    latex: Optional[List[Equation]] = None  # None: no delimiters (a bare equation)
    failed: Optional[ConversionError] = None
    if _has_math_delimiters(text):
        try:
            latex = _read_json("latex", text.encode("utf-8"))
        except ConversionError as e:
            failed = e
    signs = looks_like_markdown(text)
    prose = _without_code(text)
    if (failed is not None or signs) and not _LATEX_DOCUMENT.search(prose):
        markdown = read_markdown(text)
        # what the LaTeX reader finds outside Markdown's code (a chat answer's `$x$` example is none),
        # a bare equation counting as one
        compared = latex
        if prose != text:
            try:
                compared = _read_json("latex", prose.encode("utf-8")) if _has_math_delimiters(prose) else None
            except ConversionError:
                compared = []
        if compared is None:
            # a bare equation, unless a % hid the $ of the maths ("20% of $x$"): a bare equation has $
            # only inside \text and the like
            hidden = re.search(r"(?<!\\)\$", _TEXT_ARGUMENT.sub("", prose))
            compared = [] if hidden else [Equation(strip_tex(prose), default_display)]
        if markdown and (failed is not None or not _lost(compared, markdown, text)):
            return (latex, False) if markdown == latex else (markdown, True)
        if not markdown and signs and not any(_mathy(e.tex) and not _price(e.tex, text) for e in compared):
            # Markdown without maths ("$5 and $10 per month"): no equation, not a made-up one
            raise ConversionError("No equation found: this reads as Markdown text (a chat answer, notes) "
                                  "without $...$, $$...$$, \\(...\\) or \\[...\\].")
    if failed is not None:
        raise failed
    if latex:
        return latex, False
    return [Equation(strip_tex(text), default_display)], False


# what LaTeX maths has and prose does not
_MATH_SIGN = re.compile(r"\\[a-zA-Z]+|[\^_=<>+|]")
_TEXT_ARGUMENT = re.compile(r"\\(?:text\w*|math(?:rm|it|bf|sf|tt|cal|bb|frak)|operatorname\*?|mbox|begin|end)"
                            r"\s*\{[^{}]*\}")


def _mathy(tex: str) -> bool:
    """Whether what the LaTeX reader took for an equation looks like maths, not like prose that "$5 and
    $10" put between dollars: a command or an operator, or a lone variable (x, x1), and no word of three
    letters or more outside \\text and the like."""
    tex = re.sub(r"(?<!\\)((?:\\\\)*)%[^\n]*", r"\1", tex)  # without TeX comments (x = 1 % initial value)
    bare = _TEXT_ARGUMENT.sub("", tex)
    if re.search(r"(?<![\\A-Za-z])[A-Za-z]{3,}", bare):
        return False
    pairs = re.findall(r"(?<![\\A-Za-z])[A-Za-z]{2}(?![A-Za-z])", bare)  # xy, nm (not "to", "of")
    return bool(_MATH_SIGN.search(tex) or re.fullmatch(r"\s*\d*[A-Za-z]\d*\s*", tex)  # x, x1, 2x
                or re.fullmatch(r"\s*-?\d[\d.,;:\s+\-*/]*\d\s*|\s*\d\s*", tex)      # 42, 1/2, 0, 1, 10 - 3
                or re.search(r"(?<![\\A-Za-z0-9])[A-Za-z](?![A-Za-z])", bare)     # 2 x (not 5M)
                or any(p.lower() not in _SHORT_WORDS for p in pairs))


# two-letter English words: prose between two prices ("$5M to $10M"), not a product like xy
_SHORT_WORDS = frozenset("to of in on or is at by as an if it be we so no up do my me us he am per".split())


def _price(tex: str, text: str) -> bool:
    """Whether the LaTeX reader's "equation" `tex` is what lies between two prices in `text`: its
    closing $ is the start of an amount ("$5 + $10")."""
    if re.fullmatch(r"\s*\d[\d.,]*\s*\|.*", tex, re.S):  # "$10 | $y$ |": a price in a Markdown table
        return True
    if "|" in tex and _TABLE_RULE.search(text):  # "| | B |": what lies between two cells' dollars
        return True
    pattern = r"\$\s*" + r"\s*".join(re.escape(t) for t in tex.split()) + r"\s*\$\d"
    return bool(tex.split()) and bool(re.search(pattern, text))


def _lost(latex: List[Equation], markdown: List[Equation], text: str) -> bool:
    """Whether the Markdown reading lost an equation of the LaTeX reading (`text`'s) that looks like
    maths; each occurrence counts ($x$ twice is two)."""
    left = collections.Counter(" ".join(e.tex.split()) for e in markdown)
    for e in latex:
        # (the LaTeX reader keeps the > of a Markdown quote or Obsidian callout in display maths)
        key = " ".join(re.sub(r"(?m)^[ \t]*>[ \t]?", "", e.tex).split())
        if left[key]:
            left[key] -= 1
        elif _mathy(e.tex) and not _price(e.tex, text):
            return True
    return False


# Markdown, as chat answers and notes write it: $...$ and $$...$$, and \(...\) / \[...\] (MathJax's)
MARKDOWN = "markdown+tex_math_single_backslash"
_LATEX_DOCUMENT = re.compile(r"\\documentclass\b|\\begin\s*\{document\}")
_MARKDOWN_SIGNS = re.compile(
    # 50% of, 50 % of, (in %): a percent sign, not a comment (nor %20 in a URL)
    r"(?<![\\\w.={])\d+(?:[.,]\d+)?[ \t]?%(?![0-9A-Fa-f]{2})[ \t]*[^\s%]|\((?:in[ \t]+)?%\)"
    r"|(?:(?<![\\\w])[A-Za-z]|\))%[ \t]+[A-Za-z]{2,}"                   # p% confidence, (100-p)% of
    r"|\b[A-Z]{1,3}\$\d"                                                # US$5 (other money: _money)
    # `code`, ``code``; not TeX's quotes (`inner' and `outer', ``fast'' and ``slow'')
    r"|(?<![`\\])`(?![^`\n]*\w'['\s,.;:)!?])[^`\n]+(?<!\\)`(?!`)|(?<!`)``[^`'\n]+``(?!`)"
    r"|^[ \t]*\|?[ \t]*:?-{3,}:?[ \t]*\|[ \t]*:?-{3,}"                  # a table's | --- | --- |
    r"|^[ \t]*(?:>[ \t]?)*(?:```[^`\n]*|~~~[\w+.-]*[ \t]*(?:\{[^}\n]*\})?[ \t]*)\r?$"  # a code fence
    r"|^#{1,6}[ \t]+\S"                                                 # # a heading
    r"|(?<![\w\\^_{*])\*\*[^\s*$\\][^*\n]*?\*\*",                       # **bold**
    re.M)
_COMMENT_LINE = re.compile(r"^[ \t]*%.*$", re.M)
_TABLE_RULE = re.compile(r"^[ \t]*\|?[ \t]*:?-{3,}:?[ \t]*\|[ \t]*:?-{3,}", re.M)


# LaTeX's verbatim text, where a % or # is no sign of anything
_VERBATIM = re.compile(r"\\verb\*?([^\sa-zA-Z*])[^\n]*?\1|\\(?:url|href)\s*\{[^{}]*\}"
                       r"|\\begin\s*\{(verbatim\*?|lstlisting|minted)\}.*?\\end\s*\{\2\}", re.S)


# Markdown's code: a fenced block (``` or ~~~ to the same fence) and `inline code`
_FENCE = re.compile(r"^[ \t]*(?:>[ \t]?)*(`{3,}|~{3,})(?:(?<=`)[^`\n]*|(?<=~)[^\n]*)\n.*?"
                    r"^[ \t]*(?:>[ \t]?)*\1[`~]*[ \t]*\r?$", re.M | re.S)  # (also in a > quote)
_INLINE_CODE = re.compile(r"(?<![`\\])(`+)(?!`)(?![^`\n]*\w'['\s,.;:)!?])[^\n]+?(?<!`)\1(?!`)")


def _without_code(text: str) -> str:
    """The text without Markdown code: a LaTeX example in a chat answer's code block is no LaTeX input."""
    return _INLINE_CODE.sub(" ", _FENCE.sub(" ", text))


def looks_like_markdown(text: str) -> bool:
    """Whether pasted text may be Markdown rather than LaTeX: outside LaTeX comment lines and verbatim
    text it has what LaTeX source rarely does (a percent sign after a number, money, `code`, a code
    fence, a # heading, **bold**); a line that is a LaTeX comment, or a LaTeX document, outside
    Markdown's code says LaTeX."""
    prose = _without_code(text)
    if _COMMENT_LINE.search(_OBSIDIAN_COMMENT.sub(" ", prose)) or _LATEX_DOCUMENT.search(prose):
        return False
    plain = _without_tex_comments(_VERBATIM.sub(" ", text))
    return bool(_MARKDOWN_SIGNS.search(plain)) or _money(plain)


_MONEY = re.compile(r"(?<![\\$\w])\$(\d[\d.,]*)")


def _money(text: str) -> bool:
    """Whether the text has a dollar amount, which LaTeX would take for the start of maths: a $ and a
    number whose next $ does not close it as Markdown maths would (that $ comes after a space, or
    before a digit: "$5 and $10", "$5M to $10M", "$5-$10"; or there is none), or that a unit or a
    bracket with maths follows ("$5/mo", "$5/1M tokens", "$5.00 ($p$"); not $2 n$, $10 nm$, $42$,
    $1/2$, $2(x+1)$, $2k+1$."""
    for m in _MONEY.finditer(text):
        after = text[m.end():]
        if re.match(r"/(?:[A-Za-z]|\d+[kKmMbB]\b)|[ \t]+\(\s*\$", after):
            return True
        close = re.search(r"(?<!\\)\$", after)
        if close is None or (close.start() and after[close.start() - 1].isspace()) \
                or after[close.end():close.end() + 1].isdigit():
            return True
    return False


# Obsidian's comments (%% ... %%), not shown
_OBSIDIAN_COMMENT = re.compile(r"(?<!\\)%%.*?%%", re.S)


def _without_tex_comments(text: str) -> str:
    """The text without what can only be a TeX comment: an unescaped % after something that a percent
    sign does not follow ("$x=1$% old", "a word % note", "\\\\ % row"); a % after a number ("50%",
    "50 %"), after a lone letter or a ")" ("p%", "(100-p)%") or before ")" ("(in %)") stays."""
    lines = []
    for line in text.split("\n"):
        at = 0
        while True:
            j = line.find("%", at)
            if j < 0:
                break
            slashes = len(line[:j]) - len(line[:j].rstrip("\\"))
            before = line[:j].rstrip()
            spaced = len(before) < j
            percent = (slashes % 2 == 1 or line[j + 1:j + 2] == ")" or before[-1:].isdigit()
                       or (not spaced and (before.endswith(")") or re.search(r"(?<![A-Za-z\\])[A-Za-z]$", before))))
            if not percent:
                line = line[:j]  # a comment: the rest of the line
                break
            at = j + 1
        lines.append(line)
    return "\n".join(lines)


# environments that only occur inside maths (an LLM writes them into Markdown without $$ at times)
_INNER_MATH_ENV = re.compile(r"\s*\\begin\s*\{(?:aligned|alignedat|gathered|split|cases|dcases|rcases|array|"
                             r"[pbBvV]?matrix|smallmatrix)\}")


# maths in raw TeX: in Markdown that is TeX of a document (\textbf{Use $x$}), not a lone equation
_RAW_MATH = re.compile(r"\$|\\\(|\\\[|\\begin\s*\{(?:%s)\}" % "|".join(re.escape(e) for e in sorted(_PANDOC_MATH_ENVS)))


def markdown_maths(node, out: List[Equation], definitions: Optional[List[str]] = None,
                   cwd: Optional[str] = None) -> None:
    """The equations of pandoc JSON read from Markdown, in order: its Math, and the maths of raw TeX in it
    (a \\begin{align} ... \\end{align} written straight into the text), read by the LaTeX reader (in
    `cwd`, where \\input finds its files) after the definitions (\\newcommand ...) of the raw TeX
    before it; `definitions` collects those as the walk goes."""
    definitions = [] if definitions is None else definitions
    if isinstance(node, dict):
        if node.get("t") == "Math":
            kind, tex = node["c"]
            if tex.strip():  # not the \[ \] of a task list
                out.append(Equation(strip_tex(tex), kind["t"] == "DisplayMath"))
            return
        if node.get("t") in ("RawBlock", "RawInline") and node["c"][0] in ("tex", "latex"):
            raw = node["c"][1]
            if _INNER_MATH_ENV.match(raw):
                raw = "\\[" + raw + "\\]"  # \begin{aligned} ... written without $$: display maths
            includes = re.search(r"\\(?:input|include)(?![a-zA-Z@])", raw)
            if _RAW_MATH.search(raw) or includes:
                try:
                    # after the definitions so far; their own maths (an \input file's) was found already
                    before = len(_latex_maths("\n".join(definitions), cwd)) if definitions else 0
                    out.extend(_latex_maths("\n".join(definitions + [raw]), cwd)[before:])
                except ConversionError:
                    pass  # raw TeX that is not maths after all
            if includes or _MACRO_DEF.search(raw) or re.search(r"\\(?:DeclareMathOperator|let)(?![a-zA-Z@])", raw):
                definitions.append(raw)  # for the raw TeX after it
            return
        for v in node.values():
            markdown_maths(v, out, definitions, cwd)
    elif isinstance(node, list):
        for v in node:
            markdown_maths(v, out, definitions, cwd)


_LATEX_MATHS_CACHE: dict = {}


def _latex_maths(source: str, cwd: Optional[str]) -> List[Equation]:
    """The equations pandoc's LaTeX reader finds in `source` (run in `cwd`); remembered for the walk."""
    key = (source, cwd)
    if key not in _LATEX_MATHS_CACHE:
        if len(_LATEX_MATHS_CACHE) > 64:
            _LATEX_MATHS_CACHE.clear()
        found: List[Equation] = []
        _collect_math(json.loads(_run(["-f", "latex", "-t", "json"], source.encode("utf-8"), cwd=cwd)[0])["blocks"],
                      found)
        _LATEX_MATHS_CACHE[key] = found
    return list(_LATEX_MATHS_CACHE[key])


def _money_math(tex: str) -> bool:
    """Whether what pandoc's Markdown reader took for inline maths is money: "$5.00 ($p$ after tax)"
    gives "5.00 (", a number up to an opening bracket, or a number and prose; $42$, $1/2$, $2 x$,
    $10 nm$ are maths."""
    tex = tex.strip()
    return bool(re.match(r"\d", tex)) and (tex.endswith(("(", "[")) or bool(
        re.search(r"(?<![\\A-Za-z])[A-Za-z]{3,}", _TEXT_ARGUMENT.sub("", tex))))


def _money_dollar(text: str, tex: str) -> Optional[int]:
    """Where in `text` the inline "equation" `tex` that is money starts: a $ that is no \\$ nor part of
    $$, followed by `tex` and a $."""
    pattern = r"(?<![\\$])\$\s*" + r"\s*".join(re.escape(t) for t in tex.split()) + r"\s*\$(?!\$)"
    found = re.search(pattern, text)
    return found.start() if found else None


def _without_obsidian_comments(text: str) -> str:
    """Markdown without Obsidian's %% comments %% (outside code), which a note does not show."""
    if "%%" not in text:
        return text
    spans = [(m.start(), m.end()) for m in _FENCE.finditer(text)] + \
            [(m.start(), m.end()) for m in _INLINE_CODE.finditer(text)]

    def drop(m):
        return m.group(0) if any(a <= m.start() < b for a, b in spans) else " "
    return _OBSIDIAN_COMMENT.sub(drop, text)


def markdown_blocks(text: str, fmt: str = MARKDOWN, cwd: Optional[str] = None,
                    meta: Optional[dict] = None) -> "tuple[list, str]":
    """pandoc's JSON blocks of Markdown text and its warnings (`meta`, if given, gets its metadata: the
    title and authors of its YAML header).  An inline "equation" that is money (a number, then prose:
    "$5.00 ($p$ after tax)" gives "5.00 (") is read again with its $ escaped, so that the dollar it took
    as its end starts the equation it belongs to."""
    _LATEX_MATHS_CACHE.clear()  # (an \input file may have changed since the last reading)
    text = _without_obsidian_comments(text)
    rounds = 8
    while True:
        out, err = _run(["-f", fmt, "-t", "json"], text.encode("utf-8"), cwd=cwd)
        document = json.loads(out)
        blocks = document["blocks"]
        if meta is not None:
            meta.clear()
            meta.update(document.get("meta") or {})
        maths: List[Equation] = []
        _collect_math(blocks, maths)
        escaped = text
        for e in maths:
            if not e.display and _money_math(e.tex):
                at = _money_dollar(escaped, e.tex)
                if at is not None:
                    escaped = escaped[:at] + "\\" + escaped[at:]
        if escaped == text or not rounds:
            return blocks, err  # (the reading of the text as it is now)
        text, rounds = escaped, rounds - 1


def read_markdown(text: str) -> List[Equation]:
    """The equations of Markdown text (:data:`MARKDOWN`), in order (:func:`markdown_blocks`)."""
    eqs: List[Equation] = []
    markdown_maths(markdown_blocks(text)[0], eqs)
    return eqs


_XML_ENTITIES = ("lt", "gt", "amp", "quot", "apos")
_NAMED_ENTITY = re.compile(r"&([A-Za-z][A-Za-z0-9]*);")
# CDATA sections and comments: literal text that the markup clean-ups below must not touch
_XML_LITERAL = re.compile(r"(<!\[CDATA\[[\s\S]*?\]\]>|<!--[\s\S]*?-->)")
# group 1: a literal (kept); otherwise an XML declaration or a DOCTYPE, whose internal subset
# [...] may hold comments and quoted strings with any characters
_XML_PROLOG = re.compile(_XML_LITERAL.pattern + r"|<\?xml\s[^>]*\?>"
                         r"|<!DOCTYPE[^\[>]*(?:\[(?:<!--[\s\S]*?-->|\"[^\"]*\"|'[^']*'|[^\]\"'])*\]\s*)?>", re.I)


def _outside_literals(text: str, fix) -> str:
    """Apply `fix` to the markup of `text`, leaving CDATA sections and comments alone."""
    parts = _XML_LITERAL.split(text)
    return "".join(part if i % 2 else fix(part) for i, part in enumerate(parts))


def _strip_xml_prolog(text: str) -> str:
    """Drop a BOM and every XML declaration / DOCTYPE: several pasted documents carry one
    each, and inside the wrapper element we parse them in, any of them is a syntax error."""
    return _XML_PROLOG.sub(lambda m: m.group(1) or "", text.lstrip("\ufeff"))


def _named_entities_to_numeric(text: str) -> str:
    """``&InvisibleTimes;``, ``&nbsp;``... (MathML / HTML names, common in exported MathML)
    as numeric references, which a plain XML parser understands."""
    def numeric(m):
        value = None if m.group(1) in _XML_ENTITIES else html.entities.html5.get(m.group(1) + ";")
        return "".join("&#x%X;" % ord(c) for c in value) if value else m.group(0)
    return _outside_literals(text, lambda s: _NAMED_ENTITY.sub(numeric, s))


def _local(name: str) -> str:
    """``{namespace}tag`` -> ``tag``."""
    return name.rsplit("}", 1)[-1]


def _outermost(root: ET.Element, match) -> List[ET.Element]:
    """Descendants of `root` satisfying `match`, in document order, not descending into a match."""
    found: List[ET.Element] = []

    def walk(el):
        for child in el:
            if match(child):
                found.append(child)
            else:
                walk(child)

    walk(root)
    return found


# characters that make up relation symbols (used to spot an equation array's alignment column):
# arrows, and the relations among the mathematical operators (not ∪ ⊕ ⊗ ... between them)
_RELATION_CHARS = frozenset("=<>:∈∉∊∋∌∍∝∣∤∥∦⋈⋔").union(map(chr, (
    *range(0x2190, 0x2200), *range(0x27F0, 0x2800), *range(0x2900, 0x2980),  # arrows
    *range(0x2236, 0x223E), *range(0x2241, 0x228C), *range(0x228F, 0x2293),  # ∶ ∼, ≁ ... ⊋, ⊏ ... ⊒
    *range(0x22A2, 0x22B9), *range(0x22D0, 0x22D2), *range(0x22D6, 0x22EE),  # ⊢ ... ⊸, ⋐ ⋑, ⋖ ... ⋭
    *range(0x22F2, 0x2300), *range(0x2A66, 0x2AFB)))).difference(          # ⋲ ... ⋿, ⩦ ... ⫺
    "⊤∸∺⩱⩲⫵⫶")  # an ordinary symbol and binary operators inside those ranges


def _leading_mo(el: ET.Element, ns: str) -> Optional[ET.Element]:
    """The <mo> an element starts with: itself, or the base of an over/under-decorated operator
    (``\\overset{!}{=}`` is an <mover> over <mo>=</mo>)."""
    while el.tag in (ns + "mover", ns + "munder", ns + "munderover") and len(el):
        el = el[0]
    return el if el.tag == ns + "mo" else None


def _is_relation(mo: ET.Element) -> bool:
    text = (mo.text or "").strip()
    return bool(text) and set(text) <= _RELATION_CHARS


def _is_empty(el: ET.Element) -> bool:
    """No text and nothing but <mrow>s: an empty table cell (not ``\\sqrt{}``, not spacing)."""
    return not "".join(el.itertext()).strip() and all(_local(e.tag) in ("mtd", "mrow") for e in el.iter())


def _cell_start(cell: ET.Element, ns: str) -> Optional[str]:
    """How the right-hand cell of an aligned block starts: "empty", "relation" (``&= b``), "operator"
    (a continuation line, ``&+ c``), "space" (spacing followed by anything else, ``&\\quad x``), or
    None for anything else (a table of values).  Spacing before an operator is looked past
    (``&\\quad = b``; Word exports ``&\\quad + c`` as ``<mo> </mo><mo>+</mo>``)."""
    if _is_empty(cell):
        return "empty"
    spaced = False
    for child in cell:
        if child.tag == ns + "mspace" or _is_empty(child) or (child.tag == ns + "mo" and not (child.text or "").strip()):
            spaced = True
            continue
        mo = _leading_mo(child, ns)
        if mo is not None:
            return "relation" if _is_relation(mo) else "operator"
        break
    return "space" if spaced else None


def _mark_equation_array_alignment(math: ET.Element) -> bool:
    """Older Word / PowerPoint (and other programs) export an equation array as an <mtable> without
    any alignment attributes, which texmath turns into ``\\begin{matrix}``.  (Word 16.0.20527 writes
    columnalign right / left on each cell itself.)  When the table has the shape of an aligned block,
    add ``columnalign`` so that texmath emits ``\\begin{aligned}``: every row has the same even number
    of cells, read as right / left pairs (``a &= b & c &= d``), and in each pair the left-aligned cell
    of every row is empty or starts with an operator or a space (a continuation line such as ``&+ c``)
    and in at least one row with a relation.  Returns True if changed."""
    ns = math.tag[:math.tag.index("}") + 1] if math.tag.startswith("{") else ""
    node = math
    while len(node) == 1 and node[0].tag in (ns + "mrow", ns + "semantics", ns + "mstyle"):
        node = node[0]
    tables = [c for c in node if c.tag == ns + "mtable"]
    if len(node) != 1 or not tables:
        return False
    table = tables[0]
    # a table that says how it looks (its alignment, a frame, ruled lines) is laid out as it says
    ruled = any(set((table.get(a) or "none").split()) - {"none"} for a in ("frame", "columnlines", "rowlines"))
    if ruled or table.get("columnalign") or any(r.get("columnalign") for r in table if r.tag == ns + "mtr"):
        return False
    rows = [[c for c in r if c.tag == ns + "mtd"] for r in table if r.tag == ns + "mtr"]
    if not rows:
        return False
    width = len(rows[0])
    if width < 2 or width % 2 or any(len(cells) != width or any(c.get("columnalign") for c in cells)
                                     for cells in rows):
        return False
    for left in range(1, width, 2):  # each right / left pair: its left-aligned cells
        starts = [_cell_start(cells[left], ns) for cells in rows]
        if None in starts or "relation" not in starts:
            return False
    for cells in rows:
        for i, cell in enumerate(cells):
            cell.set("columnalign", "left" if i % 2 else "right")
    return True


def _strip_namespaces(el: ET.Element) -> None:
    for e in el.iter():
        if isinstance(e.tag, str):
            e.tag = _local(e.tag)
        for key in [k for k in e.attrib if k.startswith("{")]:
            e.attrib[_local(key)] = e.attrib.pop(key)


def _serialize_math(math: ET.Element) -> bytes:
    math.tail = None  # tostring() would include the text that follows the element
    return ET.tostring(math, encoding="unicode").encode("utf-8")


def _converts(tex: str, display: bool) -> bool:
    try:
        write_mathml(Equation(tex, display))
        return True
    except ConversionError:
        return False


def parse_mathml(text: str) -> List[ET.Element]:
    """The outermost <math> elements of MathML input (prefixes and namespaces stripped, as pandoc
    wants them; named entities as numeric ones)."""
    body = _named_entities_to_numeric(_strip_xml_prolog(text))
    try:
        # the mml prefix is declared so that a pasted <mml:math> fragment parses without its declaration
        root = ET.fromstring('<r xmlns:mml="%s">%s</r>' % (MATHML_NS, body))
    except ET.ParseError as e:
        raise ConversionError("The MathML is not well-formed XML: %s" % e)
    maths = _outermost(root, lambda el: isinstance(el.tag, str) and _local(el.tag) == "math")
    if not maths:
        raise ConversionError("No <math> element found in the MathML input.")
    for math in maths:
        _strip_namespaces(math)  # pandoc only recognises a bare <math>, not <mml:math>
    return maths


def drop_annotations(math: ET.Element) -> Optional[str]:
    """Remove the annotations of a parsed <math> (:func:`parse_mathml`); its TeX annotation, if any."""
    return _drop_annotations(math)


def read_mathml(text: str, fix_office_alignment: bool = True, tidy: bool = True) -> List[Equation]:
    """Equations in MathML.  With `tidy`, identifiers that texmath would misread (upright names,
    function names, bold italic symbols, binomials) come back as the LaTeX an author writes
    (:mod:`tidy`); an equation for which that does not give valid TeX is read as texmath reads it."""
    if not text.strip():
        raise ConversionError("The input is empty.")
    maths = parse_mathml(text)
    annotations: List[Optional[str]] = []
    for math in maths:
        annotations.append(_drop_annotations(math))
        _unicode_spaces(math)
        if fix_office_alignment:
            _mark_equation_array_alignment(math)
    raw = [_serialize_math(m) for m in maths]
    mappings: List[dict] = [{} for _ in maths]
    count = 0
    for math, mapping in zip(maths, mappings):
        # placeholders must not occur in the equation - nor appear once texmath joins its identifiers
        # (<mi>Q</mi><mi>Q</mi>...); checked per equation, each with its own placeholders
        if tidy and _tidy.can_mark(_serialize_math(math).decode("utf-8") + "".join(math.itertext())):
            _tidy.mark_identifiers(math, mapping, count)
            count += len(mapping)
    marked = [_serialize_math(m) for m in maths] if tidy else raw  # also when only a fraction or a bar changed
    eqs = _read_json("html", b"".join(marked))
    if len(eqs) < len(maths):  # pandoc turns a <math> that texmath rejects into plain text, silently
        eqs, mappings = _read_one_by_one(marked, raw, annotations, mappings)
    for i, (eq, mapping) in enumerate(zip(eqs, mappings)):
        if not mapping:
            continue
        tex = _tidy.restore(eq.tex, mapping)
        if tex is None or not _converts(tex, eq.display):
            plain = _read_json("html", raw[i])  # this equation as texmath reads it
            if plain:
                tex = plain[0].tex
            elif annotations[i]:
                tex = strip_tex(annotations[i])
            else:
                raise ConversionError("texmath could not interpret <math> element %d of the input." % (i + 1))
        eqs[i] = Equation(tex, eq.display)
    _check_not_empty(eqs, "MathML")
    return eqs


def _drop_annotations(math: ET.Element) -> Optional[str]:
    """Read what the MathML shows, not a TeX annotation that texmath would prefer: it may be stale, or
    use macros texmath does not know (the user's, kept by "TeX annotation").  Returns the TeX annotation
    of the whole equation, if any: the fallback when the presentation cannot be read (Content MathML)."""
    tex = None
    for parent in [math] + list(math.iter()):
        for child in list(parent):
            if child.tag == "semantics":
                shown = [c for c in child if c.tag not in ("annotation", "annotation-xml")]
                if parent is math and tex is None:
                    for ann in child.findall("annotation"):  # the whole equation's, not a part's
                        if (ann.get("encoding") or "").lower() in ("application/x-tex", "tex", "latex") and ann.text:
                            tex = ann.text
                            break
                index = list(parent).index(child)
                parent.remove(child)
                if shown:
                    shown[0].tail = child.tail
                    parent.insert(index, shown[0])
    return tex


def _read_one_by_one(marked: List[bytes], raw: List[bytes], annotations: List[Optional[str]],
                     mappings: List[dict]) -> "tuple[List[Equation], List[dict]]":
    """The equations of several <math> elements when texmath rejected one: each read on its own; one that
    texmath cannot read comes from its TeX annotation, else that is an error."""
    eqs: List[Equation] = []
    bad = []
    for i, (m, r, ann) in enumerate(zip(marked, raw, annotations)):
        found = _read_json("html", m) or _read_json("html", r)
        if found:
            if not _read_json("html", m):
                mappings[i] = {}  # read without placeholders
            eqs.append(found[0])
        elif ann is not None:
            display = b'display="block"' in r
            eqs.append(Equation(strip_tex(ann), display))
            mappings[i] = {}
        else:
            bad.append(str(i + 1))
    if bad:
        if len(raw) == 1:
            raise ConversionError("texmath could not interpret this MathML (unsupported or malformed elements).")
        raise ConversionError("texmath could not interpret <math> element %s of the %d in the input "
                              "(unsupported or malformed elements)." % (", ".join(bad), len(raw)))
    return eqs, mappings


_DOCX_CONTENT_TYPES = (
    '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
    '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
    '<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>'
    '<Default Extension="xml" ContentType="application/xml"/>'
    '<Override PartName="/word/document.xml" '
    'ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml"/>'
    '</Types>')
_DOCX_RELS = (
    '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
    '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
    '<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" '
    'Target="word/document.xml"/></Relationships>')


def _alignment_mark(math: ET.Element) -> "Optional[tuple[ET.Element, ET.Element]]":
    """(element, its m:aln) of the first alignment mark that is on, directly in `math`: on a run
    (m:rPr/m:aln) or on an operator box (m:boxPr/m:aln, Word's "operator emulator"); None if none.
    A line has one alignment point in Word; a mark with m:val 0 / off / false is off."""
    q = lambda n: "{%s}%s" % (OMML_NS, n)  # noqa: E731
    for child in math:
        props = {q("r"): q("rPr"), q("box"): q("boxPr")}.get(child.tag)
        mark = child.find("%s/%s" % (props, q("aln"))) if props else None
        if mark is not None and mark.get(q("val"), "on").strip().lower() not in ("0", "off", "false"):
            return child, mark
    return None


def _q(name: str) -> str:
    return "{%s}%s" % (OMML_NS, name)


def _separator() -> ET.Element:
    """A run "&" of its own: outside \\text, whatever the style of the run it goes before."""
    run = ET.Element(_q("r"))
    ET.SubElement(run, _q("t")).text = "&"
    return run


def _without_line_breaks(line: List[ET.Element]) -> List[ET.Element]:
    """The line without Word's line breaks (<w:br/> in a run: the end of a line of a math paragraph): a
    run that held only the break goes, from a run with more in it only the break."""
    out = []
    for el in line:
        if el.tag == _q("r"):
            breaks = [c for c in el if c.tag == "{%s}br" % WML_NS]
            if breaks:
                for br in breaks:
                    el.remove(br)
                if not "".join(el.itertext()).strip():
                    continue
        out.append(el)
    return out


def _take(el: ET.Element, holder: str, name: str) -> Optional[ET.Element]:
    """Remove and return the property `name` (m:brk, m:aln) from el's `holder` (m:rPr, m:boxPr), dropping
    the holder when nothing is left in it; None if it has none."""
    props = el.find(_q(holder))
    found = props.find(_q(name)) if props is not None else None
    if found is not None:
        props.remove(found)
        if not len(props) and not props.attrib:
            el.remove(props)
    return found


def _line_break_of(el: ET.Element) -> Optional[ET.Element]:
    """The manual line break (m:brk, Word's "Insert Manual Break") with which el starts a new line, taken
    out of its run (m:rPr) or operator box (m:boxPr)."""
    if el.tag == _q("r"):
        return _take(el, "rPr", "brk")
    if el.tag == _q("box"):
        return _take(el, "boxPr", "brk")
    return None


class _Line:
    """A line of an equation: its elements (copies), and the m:alnAt of the manual break it starts with,
    if any (the number of the operator of the equation's first line it aligns to)."""

    def __init__(self, broken: bool = False, aln_at: Optional[int] = None):
        self.elements: List[ET.Element] = []
        self.broken = broken
        self.aln_at = aln_at


def _lines(math: ET.Element) -> List[_Line]:
    """The lines of one m:oMath, split at its manual line breaks (m:brk directly in it)."""
    lines = [_Line()]
    for child in math:
        child = copy.deepcopy(child)
        brk = _line_break_of(child)
        if brk is not None:
            try:
                aln_at = int(brk.get(_q("alnAt"), "")) or None
            except ValueError:
                aln_at = None
            lines.append(_Line(True, aln_at))
        lines[-1].elements.append(child)
    return lines


def _is_operator(ch: str) -> bool:
    return unicodedata.category(ch) == "Sm" or ch in "-/*!·"  # Word's MathML: <mo>·</mo>


def _is_on(el: ET.Element, path: str) -> bool:
    """Whether the OMML property at `path` (m:rPr/m:nor, m:boxPr/m:opEmu) is there and on (m:val absent,
    or not 0 / off / false)."""
    found = el.find(path)
    return found is not None and found.get(_q("val"), "on").strip().lower() not in ("0", "off", "false")


def _split_run(run: ET.Element, text: ET.Element, pos: int) -> "tuple[ET.Element, ET.Element]":
    """A run cut before character `pos` of its m:t `text`: what comes before (with the run's
    properties) and what follows (with a copy of them); each piece of text in one of them only."""
    first, second = ET.Element(run.tag, run.attrib), ET.Element(run.tag, run.attrib)
    props = run.find(_q("rPr"))
    if props is not None:
        second.append(copy.deepcopy(props))
    target = first
    for child in run:
        if child is text:
            head, tail = copy.deepcopy(child), copy.deepcopy(child)
            head.text, tail.text = (child.text or "")[:pos], (child.text or "")[pos:]
            if head.text:
                first.append(head)
            second.append(tail)
            target = second
        else:
            target.append(child)
    return first, second


def _before_operator(line: List[ET.Element], n: int) -> bool:
    """Put "&" before the n-th operator of the line: an operator character in a run of maths (not one of
    upright text, m:nor), or an operator box (m:opEmu) as one; a run "a=b" is cut at its "="."""
    count = 0
    texts = (_q("t"), "{%s}t" % WML_NS)
    for index, child in enumerate(line):
        if child.tag == _q("box") and _is_on(child, "%s/%s" % (_q("boxPr"), _q("opEmu"))):
            count += 1
            if count == n:
                line.insert(index, _separator())
                return True
            continue
        if child.tag != _q("r") or _is_on(child, "%s/%s" % (_q("rPr"), _q("nor"))):
            continue
        before = False  # text of this run before the operator
        for text in [c for c in child if c.tag in texts]:  # m:t, and w:t that Word also reads
            for pos, ch in enumerate(text.text or ""):
                if not _is_operator(ch):
                    before = True
                    continue
                count += 1
                if count != n:
                    before = True
                    continue
                if before or pos:
                    first, second = _split_run(child, text, pos)
                    line[index:index + 1] = [first, _separator(), second]
                else:
                    line.insert(index, _separator())
                return True
    return False


def _aligned_block(equations: List[List[_Line]]) -> ET.Element:
    """The lines of one or more equations (each m:oMath's, see _lines) as one m:oMath holding an
    m:eqArr whose rows have a run "&" at the alignment point: texmath reads that as an aligned block.
    A row's point is its m:aln mark (Word: "Align at =").  Rows without one: a line that a manual break
    started begins at the point; an equation's first line has it before the operator its first break
    names (m:alnAt), else at its start when no line has a mark."""
    math = ET.Element(_q("oMath"))
    arr = ET.SubElement(math, _q("eqArr"))
    marked = any(_alignment_mark(line.elements) is not None for lines in equations for line in lines)
    for lines in equations:
        target = next((line.aln_at for line in lines if line.broken), None)  # for this equation's line 1
        for number, line in enumerate(lines):
            row = _without_line_breaks(line.elements)
            found = _alignment_mark(row)
            if found is not None:
                element, mark = found
                props = next(p for p in element if mark in list(p))
                props.remove(mark)
                if not len(props) and not props.attrib:
                    element.remove(props)
                row.insert(row.index(element), _separator())
            elif line.broken:
                row.insert(0, _separator())
            elif number == 0 and not (target and _before_operator(row, target)) and not marked:
                row.insert(0, _separator())
            ET.SubElement(arr, _q("e")).extend(row)
    return math


def split_math_paragraph(el: ET.Element) -> List[ET.Element]:
    """Word's math paragraphs and line breaks, as texmath can read them:
    * an m:oMathPara holding several m:oMath (the lines of Shift+Enter) whose lines are aligned at marks
      (m:aln, "Align at =") as one aligned block; without marks, as one m:oMathPara per equation
      (texmath would read them as one product, x y);
    * an equation with manual line breaks (m:brk, "Insert Manual Break") as an aligned block, display
      or inline as it was;
    * anything else as it is.
    Lines with an "&" of their own are left as they are: in an equation array texmath would take it
    for an alignment point."""
    para, math, props = _q("oMathPara"), _q("oMath"), _q("oMathParaPr")
    maths = [c for c in el if c.tag == math] if el.tag == para else ([el] if el.tag == math else [])
    if not maths:
        return [el]

    def own_amp(*ms: ET.Element) -> bool:
        return any("&" in (t.text or "") for m in ms for t in m.iter(_q("t")))

    def in_para(*children: ET.Element) -> ET.Element:
        piece = ET.Element(para)
        piece.extend(copy.deepcopy(c) for c in el if c.tag == props)
        piece.extend(children)
        return piece

    if el.tag == para and len(maths) > 1 and any(_alignment_mark(m) is not None for m in maths) \
            and not own_amp(*maths):
        return [in_para(_aligned_block([_lines(m) for m in maths]))]
    out = []
    changed = False
    for m in maths:
        lines = _lines(m)
        if len(lines) > 1 and not own_amp(m):
            block = _aligned_block([lines])
            changed = True
            out.append(in_para(block) if el.tag == para else block)
        else:
            out.append(in_para(m) if el.tag == para else m)
    if not changed and len(maths) == 1:
        return [el]
    return out


def _parse_omml_fragments(text: str) -> List[ET.Element]:
    """Return the top-level <m:oMathPara>/<m:oMath> elements found in arbitrary OMML-ish XML."""
    body = _strip_xml_prolog(text)
    # Wrap so that undeclared m:/w: prefixes (common when OMML is pasted from a snippet) resolve.
    wrapped = ('<r xmlns:m="%s" xmlns:w="%s" xmlns:mc="%s" '
               'xmlns:w14="http://schemas.microsoft.com/office/word/2010/wordml" '
               'xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships">%s</r>') % (
                   OMML_NS, WML_NS, MC_NS, body)
    try:
        root = ET.fromstring(wrapped)
    except ET.ParseError as e:
        raise ConversionError("The OMML is not well-formed XML: %s" % e)
    tags = ("{%s}oMathPara" % OMML_NS, "{%s}oMath" % OMML_NS)
    alternatives, choice = "{%s}AlternateContent" % MC_NS, "{%s}Choice" % MC_NS
    found: List[ET.Element] = []

    def walk(el):
        for child in el:
            if child.tag in tags:
                found.append(child)  # an oMath inside an oMathPara belongs to it
            elif child.tag == alternatives:
                # the same content once per branch (e.g. an equation in a text box: DrawingML choice +
                # VML fallback, possibly several choices): read the first choice, else the fallback
                branches = list(child)
                branch = next((b for b in branches if b.tag == choice), branches[0] if branches else None)
                if branch is not None:
                    walk(branch)
            else:
                walk(child)

    walk(root)
    if not found:
        raise ConversionError("No <m:oMath> or <m:oMathPara> element found in the OMML input.")
    return [piece for f in found for piece in split_math_paragraph(f)]


def _check_not_empty(eqs: List[Equation], what: str) -> None:
    """texmath skips elements it does not know (OMML) and yields an empty equation for an empty one:
    report that instead of converting it to an empty result."""
    empty = [str(i) for i, e in enumerate(eqs, 1) if not e.tex]
    if empty:
        where = "this %s" % what if len(eqs) == 1 else "%s equation %s of the %d in the input" % (
            what, ", ".join(empty), len(eqs))
        raise ConversionError("texmath could not interpret %s: it is empty or holds only elements "
                              "texmath does not support." % where)


def _omml_docx(paras: List[str]) -> bytes:
    """A minimal .docx holding one paragraph per OMML fragment (pandoc reads OMML only inside a docx)."""
    document = ('<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
                '<w:document xmlns:w="%s" xmlns:m="%s"><w:body>%s</w:body></w:document>') % (
                    WML_NS, OMML_NS, "".join("<w:p>%s</w:p>" % p for p in paras))
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("[Content_Types].xml", _DOCX_CONTENT_TYPES)
        z.writestr("_rels/.rels", _DOCX_RELS)
        z.writestr("word/document.xml", document)
    return buf.getvalue()


def read_omml(text: str, tidy: bool = True) -> List[Equation]:
    """Equations in OMML; `tidy` as for :func:`read_mathml` (upright runs, function names, bold italic)."""
    if not text.strip():
        raise ConversionError("The input is empty.")
    fragments = _parse_omml_fragments(text)
    for f in fragments:
        f.tail = None  # tostring() would copy the text that follows the element into the paragraph
    raw = [ET.tostring(f, encoding="unicode") for f in fragments]
    mappings: List[dict] = [{} for _ in fragments]
    paras = raw
    if tidy and _tidy.can_mark(text + "".join("".join(f.itertext()) for f in fragments)):
        count = 0
        for f, mapping in zip(fragments, mappings):
            _tidy.mark_omml(f, mapping, OMML_NS, count)
            count += len(mapping)
        paras = [ET.tostring(f, encoding="unicode") for f in fragments]  # runs may have been split
    eqs = _read_json("docx", _omml_docx(paras))
    if not eqs:
        raise ConversionError("texmath could not interpret this OMML.")
    if any(mappings) and len(eqs) != len(fragments):
        eqs = _read_json("docx", _omml_docx(raw))  # equations and fragments cannot be paired: texmath's reading
    elif any(mappings):
        plain: Optional[List[Equation]] = None
        for i, (eq, mapping) in enumerate(zip(eqs, mappings)):
            if not mapping:
                continue
            tex = _tidy.restore(eq.tex, mapping)
            if tex is None or not _converts(tex, eq.display):
                if plain is None:
                    plain = _read_json("docx", _omml_docx(raw))
                if len(plain) != len(eqs):
                    eqs = plain  # cannot be paired: texmath's own reading for all
                    break
                tex = plain[i].tex
            eqs[i] = Equation(tex, eq.display)
    _check_not_empty(eqs, "OMML")
    return eqs


def read_equations(text: str, fmt: str, default_display: bool = True, tidy: bool = True) -> List[Equation]:
    if fmt == "latex":
        return read_latex(text, default_display)
    if fmt == "mathml":
        return read_mathml(text, tidy=tidy)
    if fmt == "omml":
        return read_omml(text, tidy=tidy)
    raise ValueError("unknown input format %r" % fmt)


# --- writing --------------------------------------------------------------------------
def _math_para(eq: Equation) -> dict:
    mode = "DisplayMath" if eq.display else "InlineMath"
    return {"t": "Para", "c": [{"t": "Math", "c": [{"t": mode}, eq.tex]}]}


def _doc_json(eq: Equation) -> bytes:
    doc = {"pandoc-api-version": _api(), "meta": {}, "blocks": [_math_para(eq)]}
    return json.dumps(doc).encode("utf-8")


def _batch_json(eqs: List[Equation], boundary: Optional[dict] = None) -> bytes:
    blocks = []
    for i, eq in enumerate(eqs):
        body = [_math_para(eq)] if boundary is None else [boundary, _math_para(eq), boundary]
        blocks.append({"t": "Div", "c": [["mseq%d" % i, [], []], body]})
    doc = {"pandoc-api-version": _api(), "meta": {}, "blocks": blocks}
    return json.dumps(doc).encode("utf-8")


def _pretty(el: ET.Element) -> str:
    if hasattr(ET, "indent"):
        ET.indent(el, space="  ")
    return ET.tostring(el, encoding="unicode")


_MATH_RE = re.compile(r"<math[\s>][\s\S]*?</math>")


_PAIRS = ("right", "left")
_EQNARRAY = ["right", "center", "left"]


def _table_shape(rows: List[ET.Element]) -> Optional[str]:
    """"pairs" for right/left cell pairs (align, aligned, split, alignat), "eqnarray" for eqnarray's
    right/center/left, None for anything else (matrix, cases, gather...).  Rows may be short (a
    trailing \\\\, a line without &): only the cells they have must fit the pattern."""
    aligns = [[c.get("columnalign") for c in r] for r in rows]
    width = max(map(len, aligns))
    if width >= 2 and all(a == _PAIRS[i % 2] for row in aligns for i, a in enumerate(row)):
        return "pairs"
    if width == 3 and all(row == _EQNARRAY[:len(row)] for row in aligns):
        return "eqnarray"
    return None


def _reads_as_alignment(rows: List[ET.Element], shape: str, ns: str) -> bool:
    """For a table that comes from `array`: True if its cells read like an aligned block - every
    right-hand cell (pairs) empty or starting with an operator or a space, every middle cell
    (eqnarray) empty or a relation, and at least one relation - rather than a table of values."""
    relations = 0
    for row in rows:
        cells = list(row)
        for cell in (cells[1::2] if shape == "pairs" else cells[1:2]):
            start = _cell_start(cell, ns)
            if start is None or (shape == "eqnarray" and start not in ("empty", "relation")):
                return False
            relations += start == "relation"
    return relations > 0


def _office_equation_arrays(root: ET.Element, arrays: Optional[set] = frozenset()) -> None:
    """Mark aligned blocks so that Word / PowerPoint paste them as an equation array.

    Office turns a pasted <mtable> into a matrix with centred columns - the = signs no longer line
    up - unless it has ``columnspacing="0em"``: that is how Word itself exports an equation array
    (right/left cell pairs, as texmath writes them, in a table without column gaps), and such a
    table becomes an <m:eqArr> aligned at the & points.  Checked with Word / PowerPoint 16.
    Short rows are padded to the table's width and a trailing empty row (a final \\\\) is dropped.
    eqnarray's relation column is merged into the right-hand side (``a & = b``): as a third cell it
    would open a new alignment column.

    `arrays` holds the indices (document order) of the tables that come from an `array`
    environment, None if unknown: such a table is marked only if it reads like an alignment.
    """
    ns = "{%s}" % MATHML_NS
    for index, table in enumerate(list(root.iter(ns + "mtable"))):
        if any(table.get(a, "none") != "none" for a in ("frame", "rowlines", "columnlines")):
            continue  # an array with rules is not an alignment (texmath 0.13 writes none of these)
        rows = list(table)
        if not rows or any(r.tag != ns + "mtr" or any(c.tag != ns + "mtd" for c in r) for r in rows):
            continue  # labelled rows (mlabeledtr) or other content
        shape = _table_shape(rows)
        if shape is None:
            continue
        if (arrays is None or index in arrays) and not _reads_as_alignment(rows, shape, ns):
            continue
        while len(rows) > 1 and all(_is_empty(c) for c in rows[-1]):
            table.remove(rows.pop())
        pattern = _EQNARRAY if shape == "eqnarray" else _PAIRS * ((max(len(r) for r in rows) + 1) // 2)
        for row in rows:
            for align in pattern[len(row):]:
                ET.SubElement(row, ns + "mtd", columnalign=align)
            if shape == "eqnarray":
                _, relation, rhs = list(row)
                rhs[:0] = list(relation)
                row.remove(relation)
        table.set("columnspacing", "0em")


_ARRAY_BEGIN = re.compile(r"\\begin%s\{(?:array|subarray|darray)\}%s(?:\[[^\[\]]*\]%s)?\{" % ((_TEX_GAP,) * 3))


def _centre_array_columns(tex: str) -> str:
    """`tex` with the l / r columns of every array environment made c (control words such as
    \\linewidth in the column spec are left alone)."""
    out, last = [], 0
    for m in _ARRAY_BEGIN.finditer(tex):
        depth, i = 1, m.end()
        while i < len(tex) and depth:
            depth += {"{": 1, "}": -1}.get(tex[i], 0)
            i += 1
        spec = re.sub(r"\\[a-zA-Z@]+|[lr]", lambda t: "c" if len(t.group()) == 1 else t.group(), tex[m.end():i])
        out.append(tex[last:m.end()] + spec)
        last = i
    return "".join(out) + tex[last:]


def _array_tables(eq: Equation, root: ET.Element) -> Optional[set]:
    """Indices (document order) of the <mtable>s of `root` (texmath's MathML for `eq`) that come from
    an array environment, which texmath renders exactly like aligned: the TeX is converted once more
    with the arrays' columns centred, and the tables whose alignment changed are the arrays.
    None when that cannot be told."""
    if not _ARRAY_BEGIN.search(eq.tex):
        return set()
    try:
        centred = ET.fromstring(write_mathml(Equation(_centre_array_columns(eq.tex), eq.display)))
    except (ConversionError, ET.ParseError):
        return None

    def alignments(tree):
        return [[[c.get("columnalign") for c in r] for r in t] for t in tree.iter("{%s}mtable" % MATHML_NS)]

    before, after = alignments(root), alignments(centred)
    if len(before) != len(after):
        return None
    return {i for i, (a, b) in enumerate(zip(before, after)) if a != b}


def write_mathml(eq: Equation, annotate: bool = False, for_office: bool = False,
                 source: Optional[str] = None) -> str:
    """MathML for the equation.  With `annotate`, the TeX source is kept in a <semantics>/<annotation>
    (`source` when given: the user's TeX, if `eq` holds a rewritten version of it); `for_office` shapes
    aligned blocks for Word / PowerPoint (see `_office_equation_arrays`)."""
    out, err = _run(["-f", "json", "-t", "html", _LF, *_mathml_flags()], _doc_json(eq))
    _check_texmath_warning(err)
    m = _MATH_RE.search(out.decode("utf-8"))
    if not m:
        raise ConversionError("pandoc produced no MathML:\n" + err.strip())
    root = ET.fromstring(m.group(0))
    return _format_mathml(root, eq, annotate, for_office, source)


def _format_mathml(root: ET.Element, eq: Equation, annotate: bool = False, for_office: bool = False,
                   source: Optional[str] = None) -> str:
    sem = root.find("{%s}semantics" % MATHML_NS)
    if sem is not None and not annotate:
        root.remove(sem)
        for child in list(sem):
            if child.tag != "{%s}annotation" % MATHML_NS:
                root.append(child)
    elif sem is not None and source is not None:
        for ann in sem.findall("{%s}annotation" % MATHML_NS):
            ann.text = source
    if for_office:
        _office_equation_arrays(root, _array_tables(eq, root))
        _office_spaces(root)
        _office_commas(root)
    return _pretty(root)


# Word's MathML import makes a comma or semicolon between brackets a separator of one delimiter with one
# separator character, and gets nested ones wrong: max(cos(a,b),0) came out max(cos,(a,b)0) and
# q(x;mu,sigma) as q(x;mu;sigma) (Word 16.0.20527).  It follows separator="false", and the content stays.
def _office_commas(root: ET.Element) -> None:
    for el in root.iter("{%s}mo" % MATHML_NS):
        if (el.text or "").strip() in (",", ";") and el.get("separator") is None:
            el.set("separator", "false")


# Word's MathML import turns every <mspace> into one no-break space (\quad as narrow as \,), but keeps the
# Unicode spaces Office's own \thinsp, \medsp, \thicksp, \emsp give; read back by width (_unicode_spaces)
_SPACE_CHARS = [(1.0, " "), (0.5, " "), (0.333, " "), (0.278, " "), (0.222, " "),
                (0.167, " ")]
_SPACE_WIDTHS = {" ": 1.0, " ": 0.5, " ": 0.333, " ": 0.278, " ": 0.222,
                 " ": 0.167, " ": 0.167}


# elements whose children are arguments in place (base, script...): a child is never just removed
_FIXED_ARITY = frozenset("msub msup msubsup munder mover munderover mfrac mroot mmultiscripts".split())


def _office_spaces(root: ET.Element) -> None:
    """<mspace width="1.0em"/> -> <mo>U+2003</mo> (and so on) for Word / PowerPoint; a negative space,
    which Office cannot show, is left out (Word would make it a positive one): an empty <mrow/> where it
    is an argument (\\!^{-x} is a script on the space: e\\!^{-x})."""
    for parent in list(root.iter()):
        for child in list(parent):
            if child.tag != "{%s}mspace" % MATHML_NS:
                continue
            m = re.fullmatch(r"\s*(-?\d*\.?\d+)\s*em\s*", child.get("width") or "")
            if not m:
                continue
            width, chars = float(m.group(1)), ""
            if width > 0:
                chars = " " * int(width + 0.02)
                rest = width - int(width + 0.02)
                if rest > 0.08:
                    chars += min(_SPACE_CHARS, key=lambda wc: abs(wc[0] - rest))[1]
            if not chars:
                if _local(parent.tag) in _FIXED_ARITY:
                    empty = ET.Element("{%s}mrow" % MATHML_NS)
                    empty.tail = child.tail
                    parent[list(parent).index(child)] = empty
                else:
                    parent.remove(child)
                continue
            mo = ET.Element("{%s}mo" % MATHML_NS)
            mo.text, mo.tail = chars, child.tail
            parent[list(parent).index(child)] = mo


def _unicode_spaces(math: ET.Element) -> None:
    """<mo> / <mtext> holding only Unicode spaces (what Office keeps, see _office_spaces) -> <mspace> of
    their width, which texmath reads as \\, \\; \\quad \\qquad... (namespaces already stripped)."""
    converted = set()
    for el in math.iter():
        if el.tag in ("mo", "mtext") and el.text and len(el) == 0 and all(c in _SPACE_WIDTHS for c in el.text):
            width = sum(_SPACE_WIDTHS[c] for c in el.text)
            el.tag, el.text = "mspace", None
            el.attrib.clear()
            el.set("width", ("%.3f" % width).rstrip("0").rstrip(".") + "em")
            converted.add(el)
    # Word writes \qquad's two em spaces as two elements: one space of their width (\qquad, not \quad\quad);
    # only em spaces in a row of a sequence (spaces MathML writes itself, \ \ for instance, stay as they
    # are, and so do a \quad\, and the arguments of a fraction or script, each a space of its own)
    for parent in math.iter():
        if parent.tag in _FIXED_ARITY:
            continue
        children = list(parent)
        for a, b in zip(children, children[1:]):
            if a in converted and b in converted and a in list(parent):
                wa = re.fullmatch(r"(\d+)em", a.get("width") or "")
                if wa and int(wa.group(1)) >= 1 and b.get("width") == "1em":
                    b.set("width", "%dem" % (int(wa.group(1)) + 1))
                    parent.remove(a)


def _batch_mathml(eqs: List[Equation]) -> List[Optional[ET.Element]]:
    if not eqs:
        return []
    out, _ = _run(["-f", "json", "-t", "html", _LF, *_mathml_flags()], _batch_json(eqs))
    results: List[Optional[ET.Element]] = [None] * len(eqs)
    for div in re.finditer(r'<div id="mseq(\d+)">(.*?)</div>', out.decode("utf-8"), re.S):
        i, found = int(div.group(1)), _MATH_RE.search(div.group(2))
        if found and i < len(eqs):
            try:
                results[i] = ET.fromstring(found.group(0))
            except ET.ParseError:
                pass
    return results


def mathml_elements(eqs: List[Equation]) -> List[Optional[ET.Element]]:
    """Each equation as a parsed <math> (texmath's MathML, namespaces stripped), in one pandoc run; None
    for an equation texmath cannot write.  For comparing renderings (convert.annotation_notes)."""
    results = _batch_mathml(eqs)
    for root in results:
        if root is not None:
            _strip_namespaces(root)
            _drop_annotations(root)
    return results


def readable_mathml(maths: List[ET.Element]) -> List[bool]:
    """Whether texmath can read each parsed <math> (:func:`parse_mathml`, annotations dropped): one
    pandoc run when it reads them all, else one each (pandoc turns a <math> it rejects into text)."""
    raw = [_serialize_math(copy.deepcopy(m)) for m in maths]
    if not raw:
        return []
    if len(_read_json("html", b"".join(raw))) == len(raw):
        return [True] * len(raw)
    return [bool(_read_json("html", r)) for r in raw]


def write_omml(eq: Equation) -> str:
    out, err = _run(["-f", "json", "-t", "docx", "-o", "-"], _doc_json(eq))
    _check_texmath_warning(err)
    text = _omml_body(_word_document(out))
    if text is not None:
        return text
    raise ConversionError("pandoc produced no OMML:\n" + err.strip())


def _word_document(out: bytes) -> ET.Element:
    with zipfile.ZipFile(io.BytesIO(out)) as z:
        return ET.fromstring(z.read("word/document.xml"))


def _omml_body(paragraph: ET.Element) -> Optional[str]:
    for el in paragraph.iter():
        if el.tag in ("{%s}oMathPara" % OMML_NS, "{%s}oMath" % OMML_NS):
            return _pretty(el)
    return None


def write_eqn(eq: Equation) -> str:
    """groff eqn source (body only, without the .EQ/.EN macros)."""
    out, err = _run(["-f", "json", "-t", "ms", _LF], _doc_json(eq))
    _check_texmath_warning(err)
    body = _eqn_body(out.decode("utf-8"), eq.display)
    if body is None:
        raise ConversionError("pandoc produced no eqn output:\n" + err.strip())
    return body


def _eqn_body(text: str, display: bool) -> Optional[str]:
    if display:
        m = re.search(r"^\.EQ\n([\s\S]*?)\n\.EN", text, re.M)
        if m:
            return m.group(1)
    lines = [ln for ln in text.splitlines() if not ln.startswith(".")]
    body = "\n".join(lines).strip()
    if body.startswith("@") and body.endswith("@"):
        body = body[1:-1]
    return body or None


# pandoc writes a \label as a Typst label after the closing $:  $ x = 1 $<eq:a>, or, when the name is not
# valid label syntax (eq/loss, eq(1), a space...):  $ x = 1 $#label("eq/loss")
_TYPST_MATH = re.compile(r'^\$([\s\S]*)\$\s*(?:<[^<>\s]+>|#label\("(?:[^"\\]|\\.)*"\))?$')


def write_typst(eq: Equation) -> str:
    """Typst math (body only, without the $ delimiters or a trailing <label>)."""
    out, err = _run(["-f", "json", "-t", "typst", _LF], _doc_json(eq))
    _check_texmath_warning(err)
    text = _typst_body(out.decode("utf-8"))
    if not text:
        raise ConversionError("pandoc produced no Typst output:\n" + err.strip())
    return text


def _typst_body(text: str) -> str:
    text = text.strip()
    m = _TYPST_MATH.match(text)
    if m:
        text = m.group(1).strip()
    return text


def write_equations(eqs: List[Equation], fmt: str) -> List[Optional[str]]:
    """Write a batch of prepared equations. A missing result needs the single-equation diagnostic."""
    if not eqs:
        return []
    if fmt == "mathml":
        return [_format_mathml(root, eq) if root is not None else None
                for eq, root in zip(eqs, _batch_mathml(eqs))]
    if fmt == "omml":
        out, _ = _run(["-f", "json", "-t", "docx", "-o", "-"], _batch_json(eqs))
        # Each Div contains exactly one paragraph, including when pandoc writes fallback TeX.
        paragraphs = _word_document(out).findall("w:body/w:p", {"w": WML_NS})
        if len(paragraphs) != len(eqs):
            return [None] * len(eqs)
        return [_omml_body(p) for p in paragraphs]
    writer = {"typst": "typst", "eqn": "ms"}[fmt]
    # ms drops Div identifiers, and Typst adds its own #block wrapper. Raw blocks delimit the
    # exact writer output for the paragraph, including embedded newlines and labels.
    marker = "MathShuttle" + uuid.uuid4().hex
    doc = _batch_json(eqs, {"t": "RawBlock", "c": [writer, marker]})
    out, _ = _run(["-f", "json", "-t", writer, _LF], doc)
    parts = out.decode("utf-8").split(marker)
    if len(parts) != 2 * len(eqs) + 1:
        return [None] * len(eqs)
    results = []
    for eq, text in zip(eqs, parts[1::2]):
        if fmt == "typst":
            # A rejected equation begins with an escaped dollar sign, a converted one with $.
            body = (_typst_body(text) or None) if text.lstrip().startswith("$") else None
        else:
            # Fallback TeX uses dollar signs here too. Converted maths uses .EQ or @...@.
            math = re.match(r"\s*(?:\.(?:LP|PP)\n)?(?:\.EQ\n|@)", text)
            body = _eqn_body(text, eq.display) if math else None
        results.append(body)
    return results


def write_equation(eq: Equation, fmt: str, **opts) -> str:
    if fmt == "mathml":
        return write_mathml(eq, annotate=opts.get("annotate", False), for_office=opts.get("for_office", False),
                            source=opts.get("source"))
    if fmt == "omml":
        return write_omml(eq)
    if fmt == "eqn":
        return write_eqn(eq)
    if fmt == "typst":
        return write_typst(eq)
    raise ValueError("unknown output format %r" % fmt)
