"""texmath's parse errors in plain words: which command it does not know (and what to do about it), a
brace that is never closed, an environment that is no equation.

texmath reports where its parser stopped ("unexpected control sequence \\ket", or ``unexpected "\\\\"``
when an unknown command sits inside an aligned block); :func:`explained` puts a line that says what is
wrong in front of that message, which stays below it.
"""
from __future__ import annotations

import difflib
import json
import re
from typing import Dict, List, Optional

from .compat import _TOKEN, _in, _text_spans

# commands texmath takes (for "did you mean"); not complete, and it need not be
_KNOWN = set(("\\" + n) for n in r"""
alpha beta gamma delta epsilon varepsilon zeta eta theta vartheta iota kappa varkappa lambda mu nu xi pi varpi rho
varrho sigma varsigma tau upsilon phi varphi chi psi omega Gamma Delta Theta Lambda Xi Pi Sigma Upsilon Phi Psi Omega
aleph beth gimel hbar hslash ell wp Re Im partial nabla infty emptyset varnothing forall exists nexists neg lnot top
bot angle triangle square blacksquare Box Diamond lozenge star ast circ bullet cdot cdots ldots vdots ddots dots times
div pm mp oplus ominus otimes oslash odot bigoplus bigotimes bigodot biguplus bigsqcup bigcup bigcap bigvee bigwedge
sum prod coprod int oint iint iiint cup cap sqcup sqcap uplus vee wedge land lor setminus smallsetminus wr amalg
dagger ddagger diamond leq le geq ge neq ne equiv sim simeq approx cong propto ll gg prec succ preceq succeq subset
supset subseteq supseteq subsetneq supsetneq in ni notin vdash dashv models perp parallel mid nmid asymp doteq
triangleq coloneqq lesssim gtrsim leqslant geqslant because therefore to gets rightarrow leftarrow leftrightarrow
Rightarrow Leftarrow Leftrightarrow longrightarrow longleftarrow Longrightarrow Longleftarrow mapsto longmapsto
hookrightarrow uparrow downarrow implies iff langle rangle lfloor rfloor lceil rceil lVert rVert lvert rvert vert Vert
backslash lbrace rbrace prime frac dfrac tfrac binom sqrt left right middle big Big bigg Bigg bigl bigr Bigl Bigr
mathbf mathrm mathit mathsf mathtt mathcal mathbb mathfrak mathscr boldsymbol bm operatorname text textrm textbf
textit mbox hat widehat tilde widetilde bar overline underline vec dot ddot check breve acute grave overbrace
underbrace overset underset stackrel xrightarrow xleftarrow limits nolimits displaystyle textstyle scriptstyle
quad qquad hspace mspace phantom label tag nonumber begin end sin cos tan cot sec csc arcsin arccos arctan sinh cosh
tanh log ln exp lim liminf limsup sup inf max min arg det dim ker deg gcd Pr hom cancel not mathop mathrel mathbin
color boxed substack pmod bmod pod
""".split())


def _known() -> set:
    from . import compat, packs
    return _KNOWN | set(packs.PACKS) | set(compat._RENAMES) | set(compat._STANDARD)


# commands of packages that MathShuttle does not write out, with what to do instead
_PACKAGES: Dict[str, str] = {
    "\\ce": "chemistry from mhchem, which texmath does not write: give the formula as maths (\\mathrm{H_2O})",
    "\\pu": "units from mhchem, which texmath does not write: use \\mathrm{...} or siunitx' \\qty",
    "\\xymatrix": "a diagram (xy-pic), not an equation: Office cannot hold it as an equation",
    "\\tikz": "a TikZ picture, not an equation: Office cannot hold it as an equation",
}
_DIAGRAM_ENVS = {"tikzcd", "tikzpicture", "xy", "pspicture", "CD"}


_PROBED: Dict[tuple, List[str]] = {}


def _probe(names: List[str]) -> List[str]:
    """Which of these control words texmath does not know: one pandoc run with each alone (and with
    three arguments, so that commands that need some are not taken for unknown)."""
    key = tuple(names)
    if key not in _PROBED:
        if len(_PROBED) > 64:
            _PROBED.clear()
        _PROBED[key] = _probe_now(names)
    return _PROBED[key]


def _probe_now(names: List[str]) -> List[str]:
    from .pandoc_engine import _api, _mathml_flags, _run
    if not names:
        return []
    blocks = [{"t": "Para", "c": [{"t": "Math", "c": [{"t": "InlineMath"}, "%s{a}{b}{c}" % n]}]} for n in names]
    source = json.dumps({"pandoc-api-version": _api(), "meta": {}, "blocks": blocks}).encode("utf-8")
    try:
        _, err = _run(["-f", "json", "-t", "html", *_mathml_flags()], source)
    except Exception:  # noqa: BLE001 - the diagnosis is a courtesy; never fail because of it
        return []
    unknown = set(re.findall(r"unexpected control sequence (\\[A-Za-z@]+)", err))
    return [n for n in names if n in unknown]


def _unclosed(tex: str) -> Optional[str]:
    """A brace, \\left or \\begin that is never closed (or closed once too often), in words."""
    opened: List[int] = []
    for m in _TOKEN.finditer(tex):
        tok = m.group()
        if tok.startswith("%"):
            continue
        if tok == "{":
            opened.append(m.start())
        elif tok == "}":
            if not opened:
                return "a } closes a group that was never opened (after \"%s\")" % _before(tex, m.start())
            opened.pop()
    if opened:
        return "a { is never closed (it opens before \"%s\")" % _after(tex, opened[-1])
    tokens = [m for m in _TOKEN.finditer(tex) if not m.group().startswith("%")]  # (a comment is ignored)
    lefts = sum(1 for m in tokens if m.group() == "\\left")
    rights = sum(1 for m in tokens if m.group() == "\\right")
    if lefts != rights:
        return "%d \\left but %d \\right: each \\left needs its \\right (\\right. for none)" % (lefts, rights)
    begins = [m.group("env") for m in tokens if m.group("be") == "begin"]
    ends = [m.group("env") for m in tokens if m.group("be") == "end"]
    for env in begins + ends:  # (also an \end{align} without its \begin)
        if begins.count(env) != ends.count(env):
            return "\\begin{%s} and \\end{%s} do not pair up" % (env, env)
    return None


def _before(tex: str, pos: int) -> str:
    return " ".join(tex[max(0, pos - 25):pos].split())


def _after(tex: str, pos: int) -> str:
    return " ".join(tex[pos:pos + 25].split())


def _control_words(tex: str) -> List[str]:
    """The control words of `tex` outside text arguments, in order, each once."""
    spans = _text_spans(tex)
    out: List[str] = []
    for m in re.finditer(r"\\[A-Za-z@]+", tex):
        if not _in(spans, m.start()) and m.group() not in out and m.group() not in ("\\begin", "\\end"):
            out.append(m.group())
    return out


def explain(original: str, prepared: str, message: str, short: bool = False) -> List[str]:
    """Lines in plain words for texmath's `message` on `prepared` (the TeX it was given, which came from
    the user's `original`); `short`: one line without the advice (for a mark in an exported file)."""
    lines: List[str] = []
    problem = _unclosed(original)
    if problem:
        return ["the LaTeX is not complete: %s" % problem if short else "The LaTeX is not complete: %s." % problem]
    for env in re.findall(r"\\begin\s*\{([^{}]*)\}", prepared):
        if env in _DIAGRAM_ENVS:
            return ["%s is a diagram, not an equation" % env if short else
                    "%s is a diagram, not an equation: texmath (and Office's equations) cannot hold it." % env]
    stated = re.findall(r"unexpected control sequence (\\[A-Za-z@]+)", message)
    if stated:
        unknown = stated
    else:  # the parser stopped elsewhere (inside an aligned block it reports the \\ of the row)
        candidates = [c for c in _control_words(prepared) if c not in _KNOWN]
        unknown = _probe(candidates[:40])
    if not unknown:
        return lines
    known = sorted(_known())
    for name in unknown[:3]:
        if name in _PACKAGES:
            lines.append("%s: %s%s" % (name, _PACKAGES[name].split(", which")[0] if short else _PACKAGES[name],
                                       "" if short else "."))
            continue
        guess = [g for g in difflib.get_close_matches(name, known, n=3, cutoff=0.8) if g != name][:2] \
            if len(name) > 3 else []
        if short:
            lines.append("texmath does not know %s%s" % (name, " (%s?)" % " or ".join(guess) if guess else ""))
            continue
        hint = " Did you mean %s?" % " or ".join(guess) if guess and name not in guess else ""
        lines.append("texmath does not know %s.%s If it is a macro of yours or of the paper's, give its "
                     "definition: Macros... in the window (or --macros FILE), or a \\newcommand before the "
                     "equation." % (name, hint))
    return ["; ".join(lines)] if short and lines else lines


def explained(error: Exception, original: str, prepared: str, which: str = ""):
    """`error` (a ConversionError from texmath) with the plain-words lines in front; `which`:
    "Equation 3 of 5" when the input held several."""
    from .pandoc_engine import ConversionError
    message = str(error)
    if not message.startswith("texmath could not parse"):
        return error
    try:
        lines = explain(original, prepared, message)
        short = explain(original, prepared, message, short=True)
    except Exception:  # noqa: BLE001 - never hide texmath's own message because of a diagnosis bug
        lines, short = [], []
    if which:
        lines = [which + ":"] + lines
    if not lines:
        return error
    explained_error = ConversionError("\n".join(lines) + "\n\n" + message)
    explained_error.short = short[0] if short else ""  # for a one-line list (extract, a file's mark)
    return explained_error
