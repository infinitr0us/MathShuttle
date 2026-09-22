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
# vertical position of aligned / gathered ([t], [b], [c]); multlined also takes a width: [t][6cm]
_POSITION_ARG_RE = {"aligned": re.compile(r"^\s*\[\s*[tbc]\s*\]"), "gathered": re.compile(r"^\s*\[\s*[tbc]\s*\]"),
                    "multlined": re.compile(r"^\s*(?:\[[^\[\]]*\]\s*){1,2}")}
_TOKEN_RE = re.compile(r"\\[a-zA-Z@]+|\\.|%[^\n]*|[{}&]", re.S)

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


def _balanced(body: str, env: str) -> bool:
    """True if the \\begin{env} / \\end{env} inside `body` pair up, i.e. the outer begin and end
    enclose all of `tex` - not ``\\begin{aligned}..\\end{aligned} \\quad \\begin{aligned}..\\end{aligned}``."""
    depth = 0
    for m in re.finditer(r"\\(begin|end)\{%s\}" % re.escape(env), body):
        depth += 1 if m.group(1) == "begin" else -1
        if depth < 0:
            return False
    return depth == 0


def _eqnarray_to_align(body: str) -> str:
    """eqnarray rows have three columns (``a &=& b``); align / aligned have pairs (``a &= b``):
    drop the second top-level ``&`` of every row."""
    out, depth, amps, last = [], 0, 0, 0
    for m in _TOKEN_RE.finditer(body):
        tok = m.group()
        if tok == "{":
            depth += 1
        elif tok == "}":
            depth -= 1
        elif depth == 0 and tok == "\\\\":
            amps = 0
        elif depth == 0 and tok == "&":
            amps += 1
            if amps == 2:
                out.append(body[last:m.start()])
                last = m.end()
    return "".join(out) + body[last:]


def split_env(tex: str, envs: Tuple[str, ...]) -> Tuple[Optional[str], str]:
    """If `tex` is exactly one ``\\begin{env}...\\end{env}`` with env in `envs`, return (env, inner)."""
    m = _ENV_RE.match(tex)
    if m and m.group("env") in envs and _balanced(m.group("body"), m.group("env")):
        env, body = m.group("env"), m.group("body")
        if env.startswith("alignat"):
            body = _ALIGNAT_ARG_RE.sub("", body, count=1)  # drop the column-count argument
        elif env.startswith("eqnarray"):
            body = _eqnarray_to_align(body)
        elif env in _POSITION_ARG_RE:
            body = _POSITION_ARG_RE[env].sub("", body, count=1)  # meaningless outside the enclosing formula
        return env, body.strip("\n").strip()
    return None, tex.strip()


def _ends_in_comment(body: str) -> bool:
    """True if the last line of `body` ends in a % comment, which would swallow a closing delimiter."""
    return any(m.group().startswith("%") for m in _TOKEN_RE.finditer(body.rsplit("\n", 1)[-1]))


def _no_split(body: str) -> str:
    """split only works directly inside a display formula: inline, it becomes aligned."""
    env, inner = split_env(body, ("split",))
    return _env("aligned", inner) if env else body


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
    if "\n" in body or _ends_in_comment(body):
        return "%s\n%s\n%s" % (open_, body, close)
    return "%s %s %s" % (open_, body, close)


def _inline(open_, close, body: str) -> str:
    body = _no_split(body)
    return "%s%s%s%s" % (open_, body, "\n" if _ends_in_comment(body) else "", close)


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
        return _inline("$", "$", plain_form(tex))
    if wrapper == "paren":
        return _inline("\\(", "\\)", plain_form(tex))
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


def numbering_conflict(tex: str, wrapper: str) -> bool:
    """True if `wrap(tex, wrapper)` moves an environment's per-line ``\\tag`` / ``\\label``
    commands somewhere LaTeX accepts no ``\\tag`` and at most one ``\\label`` (inside
    aligned / gathered, in a single-equation or inline wrapper, or several tags in multline)."""
    if wrapper == "auto" and has_outer_env(tex):
        return False  # kept verbatim
    kind, content = classify(tex)
    target = wrapper.rstrip("*")
    if (kind == "plain" or target == "align" or (target == "gather" and kind != "align")
            or (target == "multline" and kind == "multline")):
        return False
    return bool(re.search(r"\\tag(?![a-zA-Z])", content)) or len(re.findall(r"\\label(?![a-zA-Z])", content)) > 1


def wrapper_is_inline(wrapper: str, display: bool) -> bool:
    if wrapper == "auto":
        return not display
    return wrapper in INLINE_WRAPPERS


def join_outputs(parts: List[str]) -> str:
    return "\n\n".join(parts)
