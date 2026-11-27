"""Built-in definitions for packages that papers load and texmath does not know: physics, braket,
slashed, and siunitx quantities with units written as letters.

They are applied by :func:`compat.for_texmath` after the user's and the document's own macros, and only
to commands that neither defines (an author's own ``\\abs`` wins).  A command whose name also means
something standard (physics' ``\\div``, ``\\grad`` with ``\\div`` is ÷ in LaTeX) is rewritten only when
the document loads the package.  Each rewrite gives LaTeX that texmath reads; LaTeX -> LaTeX output
keeps what the author wrote.
"""
from __future__ import annotations

import re
from typing import Callable, Dict, List, Optional, Sequence, Set, Tuple

from .compat import _CS, _GAP, _argument, _find, _group_end, _optional, _unbrace

# ---- reading a command's arguments -----------------------------------------------------------------


class _Args:
    """What follows a command: a star, [options] and {arguments}, or a delimited argument (\\qty(...))."""

    def __init__(self, tex: str, pos: int):
        self.tex = tex
        self.end = pos
        self.star = False

    def take_star(self) -> bool:
        i = _GAP.match(self.tex, self.end).end()
        if self.tex[i:i + 1] == "*":
            self.end = i + 1
            self.star = True
        return self.star

    def option(self) -> Optional[str]:
        j = _optional(self.tex, self.end, 1)
        if j == self.end:
            return None
        text = self.tex[self.end:j].strip()
        self.end = j
        return text[1:-1]

    def brace(self) -> Optional[str]:
        """The next {group} (after spaces), or None if the next thing is not a brace group."""
        i = _GAP.match(self.tex, self.end).end()
        if self.tex[i:i + 1] != "{":
            return None
        j = _group_end(self.tex, i)
        if j < 0:
            return None
        self.end = j
        return self.tex[i + 1:j - 1]

    def first(self) -> Optional[str]:
        """A {group}, or else one token (physics takes \\ket\\psi as \\ket{\\psi})."""
        x = self.brace()
        return self.argument() if x is None else x

    def argument(self) -> Optional[str]:
        """A TeX argument: a group, a control sequence or one character (\\vb r)."""
        a, b = _argument(self.tex, self.end)
        if b <= a:
            return None
        self.end = b
        return _unbrace(self.tex[a:b])

    def delimited(self) -> Optional[Tuple[str, str]]:
        """(opening, content) of \\qty(..), \\qty[..], \\qty|..|, \\qty{..}; None for anything else."""
        i = _GAP.match(self.tex, self.end).end()
        opening = self.tex[i:i + 1]
        if opening == "{":
            content = self.brace()
            return None if content is None else ("{", content)
        closing = {"(": ")", "[": "]", "|": "|"}.get(opening)
        if not closing:
            return None
        depth, j = 0, i + 1
        while j < len(self.tex):
            ch = self.tex[j]
            if ch == "\\":
                j += 2
                continue
            if ch == "{":
                depth += 1
            elif ch == "}":
                depth -= 1
            elif not depth and opening != "|" and ch == opening:
                depth_paren = 1  # a nested pair of the same kind: (a (b) c)
                k = j + 1
                while k < len(self.tex) and depth_paren:
                    if self.tex[k] == "\\":
                        k += 2
                        continue
                    depth_paren += {opening: 1, closing: -1}.get(self.tex[k], 0)
                    k += 1
                j = k
                continue
            elif not depth and ch == closing:
                self.end = j + 1
                return opening, self.tex[i + 1:j]
            j += 1
        return None


def _split_bars(text: str) -> List[str]:
    """`text` split at its own (not nested) | bars: \\braket{a|b} -> [a, b]."""
    parts, depth, last, i = [], 0, 0, 0
    while i < len(text):
        ch = text[i]
        if ch == "\\":
            i += 2
            continue
        if ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
        elif ch == "|" and not depth:
            parts.append(text[last:i])
            last = i + 1
        i += 1
    return parts + [text[last:]]


def _fence(left: str, right: str, content: str, sized: bool = True) -> str:
    """\\left< content \\right> (or without sizing)."""
    if sized:
        return "\\left%s {%s} \\right%s" % (left, content, right)
    return "%s {%s} %s" % (left, content, right)


def _bracket(parts: Sequence[str], sized: bool, left: str = "\\langle", right: str = "\\rangle") -> str:
    sep = " \\middle| " if sized else " \\mid "
    inner = sep.join("{%s}" % p.strip() for p in parts)
    return ("\\left%s %s \\right%s" % (left, inner, right)) if sized else ("%s %s %s" % (left, inner, right))


# ---- the commands ------------------------------------------------------------------------------------
# A handler gets the parsed arguments and returns the replacement, or None to leave the command alone.
Handler = Callable[[_Args, Set[str]], Optional[str]]


def _bra(a: _Args, _p) -> Optional[str]:
    sized = not a.take_star()
    x = a.first()
    return None if x is None else _fence("\\langle", "|", x, sized)


def _ket(a: _Args, _p) -> Optional[str]:
    sized = not a.take_star()
    x = a.first()
    return None if x is None else _fence("|", "\\rangle", x, sized)


def _braket(a: _Args, packages) -> Optional[str]:
    sized = not a.take_star()
    x = a.first()
    if x is None:
        return None
    y = a.brace()
    if y is not None:  # physics: \braket{a}{b}
        return _bracket([x, y], sized)
    parts = _split_bars(x)
    if len(parts) == 1 and "physics" in packages and "braket" not in packages:
        parts = [x, x]  # physics' \braket{a} is <a|a>
    return _bracket(parts, sized)


def _sized_braket(a: _Args, _p) -> Optional[str]:  # braket's \Braket{a | A | b}
    x = a.brace()
    return None if x is None else _bracket(_split_bars(x), True)


def _set(sized: bool) -> Handler:
    def handler(a: _Args, _p) -> Optional[str]:
        x = a.brace()
        if x is None:
            return None
        parts = _split_bars(x)
        if len(parts) > 2:  # only the first | separates
            parts = [parts[0], "|".join(parts[1:])]
        return _bracket(parts, sized, "\\{", "\\}")
    return handler


def _expval(a: _Args, _p) -> Optional[str]:
    sized = not a.take_star()
    x = a.first()
    if x is None:
        return None
    y = a.brace()
    return _bracket([x] if y is None else [y, x, y], sized)


def _mel(a: _Args, _p) -> Optional[str]:
    sized = not a.take_star()
    n, op, m = a.brace(), a.brace(), a.brace()
    return None if m is None else _bracket([n, op, m], sized)


def _innerproduct(a: _Args, _p) -> Optional[str]:
    sized = not a.take_star()
    x, y = a.brace(), a.brace()
    return None if y is None else _bracket([x, y], sized)


def _outerproduct(a: _Args, _p) -> Optional[str]:
    sized = not a.take_star()
    x = a.first()
    if x is None:
        return None
    y = a.brace()
    y = x if y is None else y
    return _fence("|", "\\rangle", x, sized) + _fence("\\langle", "|", y, sized)


def _derivative(symbol: str) -> Handler:
    def handler(a: _Args, _p) -> Optional[str]:
        star = a.take_star()  # \dv*: in the line (f'/x)
        order = a.option()
        first = a.brace()
        if first is None:
            return None
        second = a.brace()
        third = a.brace() if symbol == "\\partial" and second is not None else None
        power = "^{%s}" % order.strip() if order else ""
        if second is None:  # \dv{x}: the operator d/dx
            num, den = symbol + power, "%s {%s}%s" % (symbol, first, power)
        elif third is not None:  # \pdv{f}{x}{y}: mixed
            num, den = "%s^{2} {%s}" % (symbol, first), "%s {%s} \\, %s {%s}" % (symbol, second, symbol, third)
        else:
            num, den = "%s%s {%s}" % (symbol, power, first), "%s {%s}%s" % (symbol, second, power)
        return "{%s} / {%s}" % (num, den) if star else "\\frac{%s}{%s}" % (num, den)
    return handler


def _pair(left: str, right: str) -> Handler:
    def handler(a: _Args, _p) -> Optional[str]:
        sized = not a.take_star()
        x, y = a.brace(), a.brace()
        return None if y is None else _fence(left, right, "{%s}, {%s}" % (x, y), sized)
    return handler


_QTY = {"(": ("(", ")"), "[": ("[", "]"), "|": ("|", "|"), "{": ("\\{", "\\}")}


def _qty(a: _Args, _p) -> Optional[str]:
    """physics' \\qty(..) / [..] / |..| / {..}; siunitx' \\qty{number}{unit} is left to texmath (or to
    the literal-unit rule below)."""
    start = a.end
    sized = not a.take_star()
    found = a.delimited()
    if found is None:
        return None
    opening, content = found
    if opening == "{":
        probe = _Args(a.tex, a.end)
        if probe.brace() is not None:  # \qty{3.2}{\per\second}: siunitx
            a.end = start
            return None
    left, right = _QTY[opening]
    return _fence(left, right, content, sized)


def _fixed_fence(left: str, right: str) -> Handler:  # \pqty{x}, \abs{x}, \norm{x}
    def handler(a: _Args, _p) -> Optional[str]:
        sized = not a.take_star()
        x = a.brace()
        return None if x is None else _fence(left, right, x, sized)
    return handler


def _font(plain: str, starred: str, accent: str = "") -> Handler:
    def handler(a: _Args, _p) -> Optional[str]:
        star = a.take_star()
        x = a.argument()
        if x is None:
            return None
        inner = "%s{%s}" % (starred if star else plain, x)
        return "%s{%s}" % (accent, inner) if accent else inner
    return handler


def _arrow_vector(a: _Args, _p) -> Optional[str]:  # \va{x}, \va*{x}
    star = a.take_star()
    x = a.argument()
    if x is None:
        return None
    return "\\vec{%s}" % ("\\boldsymbol{%s}" % x if star else x)


def _operator(name: str) -> Handler:
    def handler(a: _Args, _p) -> Optional[str]:
        return "\\operatorname{%s}" % name
    return handler


def _order(a: _Args, _p) -> Optional[str]:
    sized = not a.take_star()
    found = a.delimited()
    if found is None:
        return None
    return "\\mathcal{O}" + _fence("(", ")", found[1], sized)


def _eval(a: _Args, _p) -> Optional[str]:
    a.take_star()
    x = a.brace()
    return None if x is None else "\\left. {%s} \\right|" % x


def _vector_operator(symbol: str) -> Handler:  # \grad, \curl, \laplacian, \div (with an optional argument)
    def handler(a: _Args, _p) -> Optional[str]:
        a.take_star()
        x = a.brace()
        return symbol if x is None else "%s {%s}" % (symbol, x)
    return handler


def _symbol(symbol: str) -> Handler:
    def handler(a: _Args, _p) -> Optional[str]:
        return symbol
    return handler


_MQTY = {"(": "pmatrix", "[": "bmatrix", "|": "vmatrix", "{": "matrix"}


def _mqty(a: _Args, _p) -> Optional[str]:
    a.take_star()
    found = a.delimited()
    if found is None:
        return None
    env = _MQTY[found[0]]
    return "\\begin{%s} %s \\end{%s}" % (env, found[1], env)


def _matrix(env: str) -> Handler:  # \pmqty{..}, \bmqty{..}, \vmqty{..}
    def handler(a: _Args, _p) -> Optional[str]:
        x = a.brace()
        return None if x is None else "\\begin{%s} %s \\end{%s}" % (env, x, env)
    return handler


def _slashed(a: _Args, _p) -> Optional[str]:
    x = a.argument()
    if x is None:
        return None
    x = x.strip()
    if re.fullmatch(r"[A-Za-z]", x):
        return "{\\not %s}" % x  # a slash through the letter (texmath: U+0338)
    return "\\cancel{%s}" % x


def _indices(text: str) -> Optional[str]:
    """tensor's index list ^a_b^c: each index on its own, staggered as the package sets them."""
    out, i = [], 0
    while i < len(text):
        ch = text[i]
        if ch.isspace():
            i += 1
            continue
        if ch not in "^_":
            return None
        a = _Args(text, i + 1)
        index = a.argument()
        if index is None:
            return None
        out.append("{}%s{%s}" % (ch, index))
        i = a.end
    return "".join(out)


def _tensor(a: _Args, _p) -> Optional[str]:  # \tensor{T}{^a_b}, \tensor*{T}{^a_b}
    a.take_star()
    base, indices = a.brace(), a.brace()
    written = None if indices is None else _indices(indices)
    return None if written is None else "{%s}%s" % (base, written)


def _index_list(a: _Args, _p) -> Optional[str]:  # T\indices{^a_b}
    indices = a.brace()
    return None if indices is None else _indices(indices)


# siunitx with a unit written as letters (kHz, m/s^2): texmath sets those letters as italic variables
def _literal_unit(unit: str) -> Optional[str]:
    unit = unit.strip()
    if not unit or "\\" in unit:
        return None  # \kilo\hertz: texmath reads siunitx' unit macros itself
    unit = re.sub(r"\s*[.~]\s*", "\\\\,", unit)  # kg.m, kg~m: a thin space between units
    return "\\mathrm{%s}" % unit


def _quantity(a: _Args, _p) -> Optional[str]:  # \qty{12}{kHz}, \SI{12}{kHz}
    start = a.end
    a.option()
    number = a.brace()
    unit = a.brace()
    if unit is None:
        a.end = start
        return None
    written = _literal_unit(unit)
    if written is None:
        a.end = start
        return None
    return "\\num{%s}\\,%s" % (number, written)


def _unit(a: _Args, _p) -> Optional[str]:  # \si{kHz}, \unit{kHz}
    start = a.end
    a.option()
    unit = a.brace()
    written = None if unit is None else _literal_unit(unit)
    if written is None:
        a.end = start
        return None
    return written


def _numlist(a: _Args, _p) -> Optional[str]:  # texmath writes \numlist{1;2;3} as "1, 2, & 3"
    a.option()
    x = a.brace()
    if x is None:
        return None
    items = ["\\num{%s}" % i.strip() for i in x.split(";")]
    if len(items) == 1:
        return items[0]
    return ", ".join(items[:-1]) + "\\text{ and }" + items[-1]


# name -> (package, handler, only when the document loads the package)
PACKS: Dict[str, Tuple[str, Handler, bool]] = {}


def _add(package: str, names: str, handler: Handler, loaded_only: bool = False) -> None:
    for name in names.split():
        PACKS["\\" + name] = (package, handler, loaded_only)


_add("physics", "bra", _bra)
_add("physics", "ket", _ket)
_add("physics", "braket", _braket)
_add("physics", "ip innerproduct", _innerproduct)
_add("physics", "op outerproduct dyad ketbra", _outerproduct)
_add("physics", "expval ev expectationvalue", _expval)
_add("physics", "mel matrixel matrixelement", _mel)
_add("physics", "dv derivative", _derivative("\\mathrm{d}"))
_add("physics", "pdv partialderivative pderivative", _derivative("\\partial"))
_add("physics", "comm commutator", _pair("[", "]"))
_add("physics", "acomm anticommutator pb poissonbracket", _pair("\\{", "\\}"))
_add("physics", "qty quantity", _qty)
_add("physics", "pqty", _fixed_fence("(", ")"))
_add("physics", "bqty", _fixed_fence("[", "]"))
_add("physics", "Bqty", _fixed_fence("\\{", "\\}"))
_add("physics", "vqty", _fixed_fence("|", "|"))
_add("physics", "abs absolutevalue", _fixed_fence("|", "|"))
_add("physics", "norm", _fixed_fence("\\Vert", "\\Vert"))
_add("physics", "vb vectorbold", _font("\\mathbf", "\\boldsymbol"))
_add("physics", "vu vectorunit", _font("\\mathbf", "\\boldsymbol", "\\hat"))
_add("physics", "va vectorarrow", _arrow_vector)
_add("physics", "tr trace", _operator("tr"))
_add("physics", "Tr Trace", _operator("Tr"))
_add("physics", "rank", _operator("rank"))
_add("physics", "erf", _operator("erf"))
_add("physics", "Res Residue", _operator("Res"))
_add("physics", "pv principalvalue", _operator("P.V."))
_add("physics", "order", _order)
_add("physics", "eval evaluated", _eval)
_add("physics", "grad gradient", _vector_operator("\\nabla"))
_add("physics", "curl", _vector_operator("\\nabla\\times"))
_add("physics", "laplacian", _vector_operator("\\nabla^{2}"))
_add("physics", "divergence", _vector_operator("\\nabla\\cdot"))
_add("physics", "div", _vector_operator("\\nabla\\cdot"), loaded_only=True)  # \div is ÷ in LaTeX
_add("physics", "cross crossproduct", _symbol("\\times"))
_add("physics", "vdot dotproduct", _symbol("\\cdot"))
_add("physics", "mqty matrixquantity", _mqty)
_add("physics", "pmqty", _matrix("pmatrix"))
_add("physics", "bmqty", _matrix("bmatrix"))
_add("physics", "vmqty", _matrix("vmatrix"))
_add("physics", "smqty", _matrix("smallmatrix"))
_add("braket", "Bra", _fixed_fence("\\langle", "|"))
_add("braket", "Ket", _fixed_fence("|", "\\rangle"))
_add("braket", "Braket", _sized_braket)
_add("braket", "set", _set(False))
_add("braket", "Set", _set(True))
_add("slashed", "slashed", _slashed)
_add("tensor", "tensor", _tensor)
_add("tensor", "indices", _index_list)
# AASTeX (aastex*.cls, which pandoc does not read): decimal arc units 6\farcs15
_add("aastex", "farcs", _symbol("{.}^{\\prime\\prime}"))
_add("aastex", "farcm", _symbol("{.}^{\\prime}"))
_add("aastex", "fdg", _symbol("{.}^{\\circ}"))
_add("aastex", "arcsec", _symbol("{}^{\\prime\\prime}"))
_add("aastex", "arcmin", _symbol("{}^{\\prime}"))
_add("aastex", "micron", _symbol("\\mu\\mathrm{m}"))
_add("siunitx", "SI", _quantity)
_add("siunitx", "si unit", _unit)
_add("siunitx", "numlist", _numlist)
# siunitx' \qty{12}{kHz} goes through physics' \qty handler first (which leaves the two-group form
# alone); the literal-unit rule for it is applied separately (see apply)
_QUANTITY_NAMES = ("\\qty", "\\SI")


def apply(tex: str, defined: Set[str], packages: Set[str], used: List[str]) -> str:
    """`tex` with the commands of the built-in packs written out; `defined`: names the input or the
    user's / document's macros define (left alone); `packages`: what the document loads; `used`
    collects "package: \\name" for the note."""
    names = [n for n, (pkg, _, loaded_only) in PACKS.items()
             if n not in defined and (not loaded_only or pkg in packages)]
    if not names:
        return tex
    pattern = re.compile(r"(%s)%s" % ("|".join(re.escape(n) for n in sorted(names, key=len, reverse=True)), _CS))
    pos = 0
    guard = 0
    while guard < 10000:
        guard += 1
        m = _find(tex, pattern, pos)
        if not m:
            break
        name = m.group(1)
        package, handler, _ = PACKS[name]
        args = _Args(tex, m.end())
        new = handler(args, packages)
        if new is None and name in _QUANTITY_NAMES:  # \qty{12}{kHz}: siunitx with a literal unit
            args = _Args(tex, m.end())
            new = _quantity(args, packages)
            package = "siunitx"
        if new is None:
            pos = m.end()
            continue
        used.append("%s: %s" % (package, name))
        tex = tex[:m.start()] + new + tex[args.end:]
        pos = m.start()  # the arguments may hold more of them
    return tex
