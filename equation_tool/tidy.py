"""Paper-ready LaTeX from equations that come from Word / PowerPoint (MathML or OMML input).

texmath's TeX writer is faithful to the structure but not to what a LaTeX author would write,
and its MathML reader drops some of the meaning:

* ``<mi>OOD</mi>`` (upright, several letters) becomes ``OOD`` - italic in LaTeX; a function
  name such as ``<mi>softmax</mi><mo>&#x2061;</mo>`` becomes ``{softmax}``, which reads
  back as s*o*f*t*m*a*x;
* bold italic symbols (``\\boldsymbol{\\theta}``, ``\\bm{w}``) become ``\\mathbf{θ}`` / ``\\mathbf{w}``
  (bold upright, and a raw θ that pdfLaTeX rejects);
* a binomial with ``linethickness="0"`` becomes ``\\left( \\frac{n}{k} \\right)``;
* a cell of an aligned block becomes ``a & {= b}``: the group loses the spacing of the relation;
* ``cases`` becomes ``\\left\\{ \\begin{matrix} ... \\end{matrix} \\right.``, sums carry
  ``\\limits`` even in display maths, scripts may be empty (``x_{i}^{}``).

:func:`mark_identifiers` replaces the identifiers texmath would misread by placeholder names
before texmath runs and :func:`restore` puts the right LaTeX in their place; :func:`tidy_tex`
cleans the TeX texmath wrote.  The caller checks that the result still converts.
"""
from __future__ import annotations

import re
import unicodedata
import xml.etree.ElementTree as ET
from typing import Dict, List, Optional

# operator names texmath already writes as \log, \max, ...
_KNOWN_FUNCTIONS = frozenset(
    "arccos arcsin arctan arg cos cosh cot coth csc deg det dim exp gcd hom inf ker lg lim liminf limsup "
    "ln log max min Pr sec sin sinh sup tan tanh".split())
_GREEK = {
    "α": "alpha", "β": "beta", "γ": "gamma", "δ": "delta", "ε": "varepsilon", "ϵ": "epsilon", "ζ": "zeta",
    "η": "eta", "θ": "theta", "ϑ": "vartheta", "ι": "iota", "κ": "kappa", "ϰ": "varkappa", "λ": "lambda",
    "μ": "mu", "ν": "nu", "ξ": "xi", "π": "pi", "ϖ": "varpi", "ρ": "rho", "ϱ": "varrho", "σ": "sigma",
    "ς": "varsigma", "τ": "tau", "υ": "upsilon", "φ": "varphi", "ϕ": "phi", "χ": "chi", "ψ": "psi",
    "ω": "omega", "Γ": "Gamma", "Δ": "Delta", "Θ": "Theta", "Λ": "Lambda", "Ξ": "Xi", "Π": "Pi",
    "Σ": "Sigma", "Υ": "Upsilon", "Φ": "Phi", "Ψ": "Psi", "Ω": "Omega", "∇": "nabla", "∂": "partial"}
# Placeholder identifiers: letters only (texmath keeps a multi-letter <mi> as one run of letters), with
# an index alphabet that cannot form the delimiters, so that adjacent placeholders split unambiguously.
_PLACEHOLDER = "QQXJ%sJXQQ"
_PLACEHOLDER_RE = re.compile(r"QQXJ[A-H]+?JXQQ")


def _letters(n: int) -> str:
    s = ""
    while True:
        s = "ABCDEFGH"[n % 8] + s
        n //= 8
        if not n:
            return s


def _base(ch: str) -> str:
    """The plain character behind a mathematical alphanumeric (𝜽 -> θ, 𝐱 -> x)."""
    return unicodedata.normalize("NFKC", ch)


def _style(ch: str) -> str:
    """"bold-italic", "bold" or "" for a mathematical alphanumeric character."""
    try:
        name = unicodedata.name(ch)
    except ValueError:
        return ""
    if name.startswith("MATHEMATICAL BOLD ITALIC "):
        return "bold-italic"
    if name.startswith("MATHEMATICAL BOLD "):
        return "bold"
    return ""


def _symbol_tex(ch: str) -> Optional[str]:
    if "a" <= ch <= "z" or "A" <= ch <= "Z":
        return ch
    name = _GREEK.get(ch)
    return "\\" + name if name else None


def _identifier_tex(mi: ET.Element, applied: bool) -> Optional[str]:
    """The LaTeX for an <mi> that texmath would misread, or None to leave it to texmath."""
    text = (mi.text or "").strip()
    if len(mi) or not text:
        return None
    variant = mi.get("mathvariant")
    if len(text) > 1:
        if not re.fullmatch(r"[A-Za-z]+", text) or variant not in (None, "normal") or text in _KNOWN_FUNCTIONS:
            return None
        return "\\operatorname{%s}" % text if applied else "\\mathrm{%s}" % text
    if variant == "normal" and re.fullmatch(r"[A-Za-z]", text):
        return "\\mathrm{%s}" % text
    style = _style(text) or (variant if variant in ("bold", "bold-italic") else "")
    base = _base(text)
    symbol = _symbol_tex(base)
    if not style or symbol is None or len(base) != 1:
        return None
    if style == "bold-italic":
        return "\\boldsymbol{%s}" % symbol
    if base in _GREEK and base.islower():  # bold upright lowercase Greek does not exist in LaTeX
        return "\\boldsymbol{%s}" % symbol
    if base in _GREEK:
        return "\\mathbf{%s}" % symbol
    return None  # bold upright Latin: texmath writes \mathbf{x} itself


def mark_identifiers(math: ET.Element, mapping: Dict[str, str]) -> None:
    """Replace the <mi>s texmath would misread by placeholder names (recorded in `mapping`),
    and write zero-thickness fractions as ``0pt``, which texmath reads as a binomial.
    The element must have no namespace (tags ``mi``, ``mo``...)."""
    for frac in math.iter("mfrac"):
        thickness = (frac.get("linethickness") or "").strip()
        if thickness and re.fullmatch(r"0*\.?0*(?:pt|px|em|ex|mm|cm|in)?", thickness) and thickness != "0pt":
            frac.set("linethickness", "0pt")
    for parent in math.iter():
        children = list(parent)
        for i, child in enumerate(children):
            if child.tag != "mi":
                continue
            nxt = children[i + 1] if i + 1 < len(children) else None
            applied = nxt is not None and nxt.tag == "mo" and (nxt.text or "").strip() == "\u2061"
            tex = _identifier_tex(child, applied)
            if tex is None:
                continue
            key = _PLACEHOLDER % _letters(len(mapping))
            mapping[key] = tex
            child.text = key
            child.attrib.pop("mathvariant", None)


def _run_tex(text: str, style: Optional[str], function_name: bool) -> Optional[str]:
    """The LaTeX for an OMML run (`style`: its m:sty, p b i bi) that texmath would misread, or None."""
    if len(text) > 1:
        if not re.fullmatch(r"[A-Za-z]+", text) or text in _KNOWN_FUNCTIONS:
            return None
        if function_name:
            return "\\operatorname{%s}" % text
        return "\\mathrm{%s}" % text if style == "p" else None
    symbol = _symbol_tex(text)
    if symbol is None:
        return None
    if style == "p" and text.isascii():
        return "\\mathrm{%s}" % text
    if style == "bi" or (style == "b" and text in _GREEK and not text.isupper()):
        return "\\boldsymbol{%s}" % symbol
    return None


def mark_omml(fragment: ET.Element, mapping: Dict[str, str], ns: str) -> None:
    """:func:`mark_identifiers` for OMML: upright multi-letter runs, function names (m:fName),
    upright single letters (``\\mathrm{d}``) and bold italic symbols get placeholders."""
    m = "{%s}" % ns
    names = {id(r) for f in fragment.iter(m + "fName") for r in f.iter(m + "r")}
    for run in fragment.iter(m + "r"):
        t = run.find(m + "t")
        if t is None or not (t.text or "").strip():
            continue
        rpr = run.find(m + "rPr")
        if rpr is not None and rpr.find(m + "nor") is not None:
            continue  # normal text (\text{...})
        sty = rpr.find(m + "sty") if rpr is not None else None
        tex = _run_tex(t.text.strip(), sty.get(m + "val") if sty is not None else None, id(run) in names)
        if tex is None:
            continue
        key = _PLACEHOLDER % _letters(len(mapping))
        mapping[key] = tex
        t.text = key
        if sty is not None:
            rpr.remove(sty)


def can_mark(text: str) -> bool:
    return "QQXJ" not in text


def restore(tex: str, mapping: Dict[str, str]) -> Optional[str]:
    """`tex` with the placeholders replaced; None if one of them got lost or split."""
    # texmath groups a multi-letter identifier ({softmax}); the group goes, except as a script argument
    out = re.sub(r"(?<![_^])\{(%s)\}" % _PLACEHOLDER_RE.pattern, lambda m: mapping.get(m.group(1), m.group(0)), tex)
    out = _PLACEHOLDER_RE.sub(lambda m: mapping.get(m.group(0), m.group(0)), out)
    if "QQXJ" in out:
        return None
    # texmath's own MathML for \mathrm{OOD} has one <mi> per letter
    return re.sub(r"(?:\\mathrm\{[A-Za-z]+\}){2,}",
                  lambda m: "\\mathrm{%s}" % "".join(re.findall(r"\{([A-Za-z]+)\}", m.group())), out)


# ---- the TeX written by texmath ------------------------------------------------------------------
_TOKEN = re.compile(r"\\(?P<be>begin|end)\s*\{(?P<env>[^{}]*)\}|\\[a-zA-Z@]+|\\.|%[^\n]*|[{}&_^]", re.S)
_CELL_ENVS = frozenset("matrix pmatrix bmatrix Bmatrix vmatrix Vmatrix smallmatrix aligned alignedat gathered "
                       "split cases array subarray align align* alignat alignat* gather gather* eqnarray "
                       "eqnarray* flalign flalign*".split())


def _group_end(tex: str, start: int) -> int:
    depth = 0
    for m in _TOKEN.finditer(tex, start):
        if m.group() == "{":
            depth += 1
        elif m.group() == "}":
            depth -= 1
            if not depth:
                return m.end()
    return -1


def _env_end(tex: str, pos: int, env: str) -> "tuple[int, int]":
    """(start, end) of the \\end{env} matching a \\begin{env} that ends at `pos`; (-1, -1) if none."""
    depth = 1
    for m in _TOKEN.finditer(tex, pos):
        if m.group("be") and m.group("env") == env:
            depth += 1 if m.group("be") == "begin" else -1
            if not depth:
                return m.start(), m.end()
    return -1, -1


def _cells(body: str) -> "List[tuple[int, int]]":
    """(start, end) of the cells of an environment body: split at its own & and \\\\."""
    spans, start, depth = [], 0, 0
    for m in _TOKEN.finditer(body):
        tok = m.group()
        if tok == "{" or m.group("be") == "begin":
            depth += 1
        elif tok == "}" or m.group("be") == "end":
            depth -= 1
        elif not depth and tok in ("&", "\\\\", "\\cr"):
            spans.append((start, m.start()))
            start = m.end()
    spans.append((start, len(body)))
    return spans


def _unwrap_cells(tex: str) -> str:
    """``a & {= b + c}`` -> ``a & = b + c``: a cell is a group already, so braces around the
    whole cell only take the spacing away from a relation at its start."""
    changed = True
    while changed:
        changed = False
        for m in _TOKEN.finditer(tex):
            if m.group("be") != "begin" or m.group("env") not in _CELL_ENVS:
                continue
            open_end = m.end()
            if m.group("env") in ("array", "subarray", "alignat", "alignat*", "alignedat"):
                arg = re.compile(r"\s*(?:\[[^\[\]]*\]\s*)?\{").match(tex, open_end)
                if arg:
                    open_end = _group_end(tex, arg.end() - 1)
                    if open_end < 0:
                        continue
            end, _ = _env_end(tex, open_end, m.group("env"))
            if end < 0:
                continue
            body = tex[open_end:end]
            for a, b in reversed(_cells(body)):
                cell = body[a:b]
                inner = cell.strip()
                if len(inner) > 2 and inner[0] == "{" and _group_end(inner, 0) == len(inner):
                    lead = cell[:len(cell) - len(cell.lstrip())]
                    trail = cell[len(cell.rstrip()):]
                    body = body[:a] + lead + inner[1:-1].strip() + trail + body[b:]
                    changed = True
            if changed:
                tex = tex[:open_end] + body + tex[end:]
                break
    return tex


_RELATION_START = re.compile(
    r"\s*(?:\{\s*)*(?:[=<>:]|\\(?:leq?|geq?|neq?|ne|approx|equiv|sim|simeq|cong|propto|in|notin|ni|subset|subseteq|"
    r"supset|supseteq|to|rightarrow|leftarrow|Rightarrow|Leftarrow|iff|implies|mapsto|coloneqq|triangleq|ll|gg|"
    r"prec|succ|preceq|succeq|perp|mid|parallel|models|vdash)(?![a-zA-Z@]))")


def _cases(tex: str) -> str:
    """``\\left\\{ \\begin{matrix} a & b \\\\ c & d \\end{matrix} \\right.`` -> ``\\begin{cases}``."""
    pattern = re.compile(r"\\left\s*\\\{\s*\\begin\{(matrix|aligned|array)\}(\s*\{l{1,2}\})?")
    pos = 0
    while True:
        m = pattern.search(tex, pos)
        if not m:
            return tex
        env = m.group(1)
        if env == "array" and not m.group(2):
            pos = m.end()
            continue
        end, end2 = _env_end(tex, m.end(), env)
        close = re.compile(r"\s*\\right\s*\.").match(tex, end2) if end >= 0 else None
        body = tex[m.end():end] if end >= 0 else ""
        rows = [[body[a:b] for a, b in _cells(r)] for r in _rows(body)]
        # a system of equations ({ x + y &= 1 \\ x - y &= 0) has relations at the start of its right-hand
        # cells; the cells of cases are values and conditions
        if (not close or any(len(r) > 2 for r in rows)
                or any(len(r) == 2 and _RELATION_START.match(r[1]) for r in rows)):
            pos = m.end()
            continue
        new = "\\begin{cases}" + body + "\\end{cases}"
        tex = tex[:m.start()] + new + tex[close.end():]
        pos = m.start() + len(new)


def _rows(body: str) -> List[str]:
    """The rows of an environment body: split at its own \\\\."""
    rows, start, depth = [], 0, 0
    for m in _TOKEN.finditer(body):
        tok = m.group()
        if tok == "{" or m.group("be") == "begin":
            depth += 1
        elif tok == "}" or m.group("be") == "end":
            depth -= 1
        elif not depth and tok in ("\\\\", "\\cr"):
            rows.append(body[start:m.start()])
            start = m.end()
    rows.append(body[start:])
    return rows


_BIG_OPS = "sum prod coprod bigcup bigcap bigoplus bigotimes bigodot biguplus bigsqcup bigvee bigwedge".split()
# commands whose arguments are set in a smaller style (\limits matters there)
_SMALL_ARGS = {"\\frac": 2, "\\tfrac": 2, "\\cfrac": 2, "\\binom": 2, "\\tbinom": 2, "\\genfrac": 6,
               "\\stackrel": 2, "\\overset": 2, "\\underset": 2}
# commands whose arguments keep the style
_SAME_STYLE_ARGS = {"\\sqrt": 1, "\\overline": 1, "\\underline": 1, "\\mathbf": 1, "\\mathrm": 1, "\\mathit": 1,
                    "\\mathcal": 1, "\\boldsymbol": 1, "\\operatorname": 1, "\\left": 0, "\\right": 0,
                    "\\widehat": 1, "\\widetilde": 1, "\\hat": 1, "\\tilde": 1, "\\bar": 1, "\\vec": 1,
                    "\\overbrace": 1, "\\underbrace": 1, "\\mathbb": 1}
_DISPLAY_ENVS = frozenset("aligned gathered split align align* gather gather* equation equation* multline "
                          "multline* flalign flalign* alignat alignat* alignedat eqnarray eqnarray*".split())


def _drop_display_limits(tex: str) -> str:
    """``\\sum\\limits_{i}`` -> ``\\sum_{i}`` where the sum is in display style (its limits go
    above and below anyway); kept inside fractions, scripts, matrices and unknown arguments."""
    big = {"\\" + op for op in _BIG_OPS}
    out = []
    last = 0
    stack: List[bool] = []  # per open group / environment: display style inside it?
    pending: "tuple[int, List[bool]]" = (-1, [])  # (group level, styles) of the next arguments of a command
    prev = ""
    for m in _TOKEN.finditer(tex):
        tok = m.group()
        if tok.startswith("%"):
            continue
        level = len(stack)
        if m.group("be") == "begin":
            stack.append(m.group("env") in _DISPLAY_ENVS)
        elif m.group("be") == "end":
            if stack:
                stack.pop()
        elif tok == "{":
            if pending[0] == level and pending[1]:
                stack.append(pending[1].pop(0))
            elif prev in ("_", "^") or (prev.startswith("\\") and prev[1:2].isalpha()):
                stack.append(False)  # a script, or an argument of a command we do not know
            else:
                stack.append(True)
        elif tok == "}":
            if stack:
                stack.pop()
        elif tok in _SMALL_ARGS:
            pending = (level, [False] * _SMALL_ARGS[tok])
        elif tok in _SAME_STYLE_ARGS:
            pending = (level, [True] * _SAME_STYLE_ARGS[tok])
        elif tok == "\\limits" and prev in big and all(stack):
            out.append(tex[last:m.start()])
            last = m.end()
        prev = tok
    out.append(tex[last:])
    return "".join(out)


def _raw_greek(tex: str) -> str:
    """Raw Greek letters (texmath leaves them inside \\mathbf) as control words; a bold
    lowercase Greek letter becomes \\boldsymbol, since \\mathbf does not embolden it."""
    from .compat import _text_spans  # text arguments keep their characters

    letters = "|".join(map(re.escape, _GREEK))

    def bold(m):
        ch = m.group(1)
        return ("\\mathbf{\\%s}" if ch.isupper() else "\\boldsymbol{\\%s}") % _GREEK[ch]

    def outside_text(pattern, repl, s):
        spans = _text_spans(s)
        return re.sub(pattern, lambda m: m.group(0) if any(a <= m.start() < b for a, b in spans) else repl(m), s)

    tex = outside_text(r"\\mathbf\{(%s)\}" % letters, bold, tex)
    tex = outside_text(letters, lambda m: "\\" + _GREEK[m.group()] +
                       (" " if m.string[m.end():m.end() + 1].isalpha() else ""), tex)
    # \mathbf leaves lowercase Greek (and \nabla, \partial) as it is
    lower = "|".join(n for ch, n in _GREEK.items() if not ch.isupper())
    tex = re.sub(r"\\mathbf\{\\(%s)\}" % lower, r"\\boldsymbol{\\\1}", tex)
    # ℓ: texmath writes \mathcal{l}, but calligraphic letters are capitals only
    return re.sub(r"\{\\mathcal\{l\}\}|\\mathcal\{l\}", r"\\ell", tex)


_RELATION_IN = re.compile(r"[=<>]|\\(?:leq?|geq?|neq?|approx|equiv|sim|in|subset|to|rightarrow|Rightarrow|mapsto|"
                          r"parallel|perp|mid)(?![a-zA-Z@])|&|,|\\\\")


def _norm_bars(tex: str) -> str:
    """``\\parallel x \\parallel`` -> ``\\| x \\|``: a norm written with U+2225 PARALLEL TO (texmath 0.13
    writes \\| as that character, so equations pasted into Office by earlier versions hold it).
    Only pairs at the same group level whose content has no relation, comma or alignment point:
    ``a \\parallel b`` stays a relation."""
    out, last = [], 0
    opened: Dict[int, int] = {}  # group level -> start of an unmatched \parallel
    depth = 0
    pairs = []
    for m in _TOKEN.finditer(tex):
        tok = m.group()
        if tok == "{":
            depth += 1
        elif tok == "}":
            opened.pop(depth, None)
            depth -= 1
        elif tok == "\\parallel":
            if depth in opened:
                start = opened.pop(depth)
                if not _RELATION_IN.search(tex, start + len("\\parallel"), m.start()):
                    pairs.append((start, m.start()))
                else:
                    opened[depth] = m.start()
            else:
                opened[depth] = m.start()
    for a, b in pairs:
        out.append(tex[last:a] + "\\|")
        out.append(tex[a + len("\\parallel"):b] + "\\|")
        last = b + len("\\parallel")
    out.append(tex[last:])
    return re.sub(r"\\\|(?=[a-zA-Z])", r"\\| ", "".join(out))


def _bold_numbers(tex: str) -> str:
    """``\\mathbf{2}\\mathbf{5}.\\mathbf{2}`` (texmath reading a bold <mn>) -> ``\\mathbf{25.2}``."""
    def merge(m):
        return "\\mathbf{%s}" % re.sub(r"\\mathbf\{([0-9]+)\}", r"\1", m.group())
    return re.sub(r"\\mathbf\{[0-9]+\}(?:\.?\\mathbf\{[0-9]+\})+", merge, tex)


def tidy_tex(tex: str, display: bool) -> str:
    """Clean up the TeX that texmath wrote for an equation read from MathML / OMML."""
    out = _unwrap_cells(tex)
    out = _cases(out)
    if display:
        out = _drop_display_limits(out)
    out = re.sub(r"(?<!\\)[_^]\{\s*\}", "", out)  # x_{i}^{} -> x_{i}
    out = re.sub(r"\\lbrack(?![a-zA-Z@]) ?", "[", out)
    out = re.sub(r"\\rbrack(?![a-zA-Z@]) ?", "]", out)
    # set minus: Word and texmath's own MathML give a backslash or U+2216 (\smallsetminus)
    out = re.sub(r"\\(?:backslash|smallsetminus)(?![a-zA-Z@])", r"\\setminus", out)
    out = re.sub(r"\\varnothing(?![a-zA-Z@])", r"\\emptyset", out)  # U+2205 EMPTY SET
    out = _norm_bars(out)
    out = _bold_numbers(out)
    out = _raw_greek(out)
    return out
