"""Re-wrapping LaTeX math: bare / $...$ / \\(...\\) / $$...$$ / \\[...\\] / environments.

pandoc keeps a display environment (equation, align, gather, ...) verbatim inside
the math string it hands us, so a TeX body may be

  * plain math                       ``x^2``
  * plain math in a display env      ``\\begin{equation} x^2 \\end{equation}``
  * an alignment env                 ``\\begin{align} a &= b \\\\ c &= d \\end{align}``
  * plain math holding an inner env  ``\\begin{aligned} ... \\end{aligned}``

:func:`wrap` converts any of those to the wrapper the user asked for.
"""
from __future__ import annotations

import re
from typing import List, Optional, Tuple

# ---- environment classes ------------------------------------------------------------
PLAIN_ENVS = ("equation", "equation*", "displaymath", "math")
ALIGN_ENVS = ("align", "align*", "alignat", "alignat*", "flalign", "flalign*", "eqnarray", "eqnarray*")
GATHER_ENVS = ("gather", "gather*")
MULTLINE_ENVS = ("multline", "multline*")
# nested environments that are valid *inside* math mode
INNER_ALIGN = ("aligned", "split")
INNER_GATHER = ("gathered",)
INNER_MULTLINE = ("multlined",)

_OUTER = PLAIN_ENVS + ALIGN_ENVS + GATHER_ENVS + MULTLINE_ENVS
_INNER = INNER_ALIGN + INNER_GATHER + INNER_MULTLINE

_ENV_RE = re.compile(r"^\s*\\begin\{(?P<env>[a-zA-Z]+\*?)\}(?P<body>[\s\S]*?)\\end\{(?P=env)\}\s*$")
_ALIGNAT_ARG_RE = re.compile(r"^\s*\{\s*\d+\s*\}")

# ---- output wrappers ---------------------------------------------------------------------
# key -> label shown in the UI
WRAPPERS = [
    ("auto", "Auto (keep environment, else by mode)"),
    ("bare", "Bare (no delimiters)"),
    ("dollar", "$ … $"),
    ("paren", "\\( … \\)"),
    ("ddollar", "$$ … $$"),
    ("bracket", "\\[ … \\]"),
    ("equation", "\\begin{equation}"),
    ("equation*", "\\begin{equation*}"),
    ("align", "\\begin{align}"),
    ("align*", "\\begin{align*}"),
    ("gather", "\\begin{gather}"),
    ("gather*", "\\begin{gather*}"),
    ("multline", "\\begin{multline}"),
    ("multline*", "\\begin{multline*}"),
]
WRAPPER_KEYS = [k for k, _ in WRAPPERS]
INLINE_WRAPPERS = ("dollar", "paren")


def split_env(tex: str, envs: Tuple[str, ...]) -> Tuple[Optional[str], str]:
    """If `tex` is exactly one ``\\begin{env}...\\end{env}`` with env in `envs`, return (env, inner)."""
    m = _ENV_RE.match(tex)
    if m and m.group("env") in envs:
        env, body = m.group("env"), m.group("body")
        if env.startswith("alignat"):
            body = _ALIGNAT_ARG_RE.sub("", body, count=1)  # drop the column-count argument
        return env, body.strip("\n").strip()
    return None, tex.strip()


def classify(tex: str) -> Tuple[str, str]:
    """Return (kind, content) with kind in {"plain", "align", "gather", "multline"}.

    `content` is the raw body of the alignment environment (with & and \\\\) for
    the non-plain kinds, and ordinary math-mode TeX for "plain".
    """
    env, inner = split_env(tex, _OUTER + _INNER)
    if env is None:
        return "plain", inner
    if env in PLAIN_ENVS:
        # equation{ aligned/split{...} } -> look one level further down
        env2, inner2 = split_env(inner, _INNER)
        if env2 is None:
            return "plain", inner
        env, inner = env2, inner2
    if env in ALIGN_ENVS or env in INNER_ALIGN:
        return "align", inner
    if env in GATHER_ENVS or env in INNER_GATHER:
        return "gather", inner
    return "multline", inner


def has_outer_env(tex: str) -> bool:
    return split_env(tex, _OUTER)[0] is not None


def _env(name: str, body: str) -> str:
    return "\\begin{%s}\n%s\n\\end{%s}" % (name, body, name)


def to_plain(kind: str, content: str) -> str:
    """Math-mode TeX usable inside any delimiter."""
    if kind == "plain":
        return content
    if kind == "align":
        return _env("aligned", content)
    return _env("gathered", content)  # gather-like and multline-like (multlined needs mathtools)


def plain_form(tex: str) -> str:
    """Math-mode TeX for `tex`, kept verbatim whenever it already is math-mode content."""
    env, inner = split_env(tex, _OUTER + _INNER)
    if env is None:
        return inner
    if env in _INNER:
        return tex.strip()
    if env in PLAIN_ENVS:
        return inner  # may itself be an aligned/split block: keep as written
    return to_plain(*classify(tex))


def _display(open_, close, body: str) -> str:
    if "\n" in body:
        return "%s\n%s\n%s" % (open_, body, close)
    return "%s %s %s" % (open_, body, close)


def wrap(tex: str, wrapper: str, display: bool = True) -> str:
    """Return `tex` re-wrapped as `wrapper` (one of WRAPPER_KEYS)."""
    if wrapper not in WRAPPER_KEYS:
        raise ValueError("unknown wrapper %r" % wrapper)
    if wrapper == "auto":
        if has_outer_env(tex):
            return tex.strip()
        wrapper = "bracket" if display else "dollar"

    if wrapper == "bare":
        return plain_form(tex)
    if wrapper == "dollar":
        return "$%s$" % plain_form(tex)
    if wrapper == "paren":
        return "\\(%s\\)" % plain_form(tex)
    if wrapper == "ddollar":
        return _display("$$", "$$", plain_form(tex))
    if wrapper == "bracket":
        return _display("\\[", "\\]", plain_form(tex))
    if wrapper in ("equation", "equation*"):
        return _env(wrapper, plain_form(tex))

    kind, content = classify(tex)
    if wrapper in ("align", "align*"):
        # any multi-line body slots straight into align (lines without & are still valid)
        return _env(wrapper, content)
    if wrapper in ("gather", "gather*"):
        body = _env("aligned", content) if kind == "align" else content
        return _env(wrapper, body)
    # multline / multline*
    body = _env("aligned", content) if kind == "align" else content
    return _env(wrapper, body)


def wrapper_is_inline(wrapper: str, display: bool) -> bool:
    if wrapper == "auto":
        return not display
    return wrapper in INLINE_WRAPPERS


def join_outputs(parts: List[str]) -> str:
    return "\n\n".join(parts)
