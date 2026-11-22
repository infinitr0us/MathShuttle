"""LaTeX that texmath does not take, rewritten into LaTeX it does - for conversions *out of* LaTeX.

texmath (inside pandoc) implements amsmath and a few packages, but real papers also use
plain-TeX font switches (``{\\rm OOD}``, ``\\cal L``), commands from other packages
(``\\notag``, ``\\mathbbm``, ``\\smash``, ``dcases``...) and ``\\def`` macros.  :func:`for_texmath`
rewrites those into constructs texmath parses, and prepends the definitions an equation
needs from the user's macro files (:class:`Macros`).

Only the TeX handed to texmath is rewritten: LaTeX -> LaTeX output keeps the user's source.
Rewrites that lose something (a colour, a strut) are reported as notes.
"""
from __future__ import annotations

import os
import re
from dataclasses import dataclass
from typing import Dict, List, Optional, Sequence, Tuple

# a control word, a control symbol, a comment, a brace, &; \begin / \end with their argument
_TOKEN = re.compile(r"\\(?P<be>begin|end)\s*\{(?P<env>[^{}]*)\}|\\[a-zA-Z@]+|\\.|%[^\n]*|[{}&]", re.S)
_CS = r"(?![a-zA-Z@])"  # end of a control word

# commands whose argument is text: nothing is rewritten inside it
_TEXT_ARG = frozenset(
    "\\text \\mbox \\hbox \\makebox \\fbox \\framebox \\textrm \\textit \\textbf \\textsf \\texttt \\textnormal "
    "\\textup \\textsl \\textsc \\textmd \\emph \\label \\tag \\ref \\eqref \\operatorname \\DeclareMathOperator".split())

# plain-TeX font switches (scope: the rest of the group) -> the LaTeX command taking an argument
_SWITCHES = {"\\rm": "\\mathrm", "\\bf": "\\mathbf", "\\it": "\\mathit", "\\sl": "\\mathit", "\\sf": "\\mathsf",
             "\\tt": "\\mathtt", "\\cal": "\\mathcal"}
# where the scope of a switch ends at the current group level
_SCOPE_ENDS = frozenset(("&", "\\\\", "\\cr", "\\right", "\\middle"))

# exact or near-exact equivalents (renamed in place)
_RENAMES = {"\\notag": "\\nonumber", "\\mathbbm": "\\mathbb", "\\mathbbmss": "\\mathbb", "\\Bbb": "\\mathbb",
            "\\frak": "\\mathfrak", "\\textup": "\\textrm", "\\textsc": "\\textrm", "\\hbox": "\\mbox",
            "\\hphantom": "\\phantom"}
# symbols texmath 0.13 writes as the wrong character: \| as U+2225 PARALLEL TO (not U+2016, a norm),
# \setminus as a backslash, \varnothing as U+2300 DIAMETER SIGN
_SYMBOLS = {"\\|": "\\Vert", "\\setminus": "\\smallsetminus", "\\varnothing": "\\emptyset"}
# same maths, different size: renamed, with a note
_RESIZED = {"\\tbinom": "\\binom", "\\dbinom": "\\binom", "\\nicefrac": "\\tfrac"}
# commands that only adjust the layout: \cmd[opt]{x} -> {x}
_UNWRAP = ("\\smash", "\\mathclap", "\\mathllap", "\\mathrlap", "\\clap", "\\llap", "\\rlap")
# struts: dropped with their argument
_DROP_WITH_ARG = ("\\vphantom",)
_DROP = ("\\mathstrut", "\\strut", "\\middle")
# environments: name -> (texmath environment, opening extra, closing extra)
_ENVS = {"dcases": ("cases", "", ""), "dcases*": ("cases", "", ""), "cases*": ("cases", "", ""),
         "rcases": ("array}{ll", "\\left.", "\\right\\}"), "drcases": ("array}{ll", "\\left.", "\\right\\}"),
         "matrix*": ("matrix", "", ""), "pmatrix*": ("pmatrix", "", ""), "bmatrix*": ("bmatrix", "", ""),
         "Bmatrix*": ("Bmatrix", "", ""), "vmatrix*": ("vmatrix", "", ""), "Vmatrix*": ("Vmatrix", "", ""),
         "smallmatrix*": ("smallmatrix", "", "")}


def _group_end(tex: str, start: int) -> int:
    """Index just after the brace group opening at `start` (``tex[start] == "{"``), or len(tex)."""
    depth = 0
    for m in _TOKEN.finditer(tex, start):
        tok = m.group()
        if tok == "{":
            depth += 1
        elif tok == "}":
            depth -= 1
            if not depth:
                return m.end()
    return len(tex)


def _argument(tex: str, pos: int) -> Tuple[int, int]:
    """(start, end) of the TeX argument at `pos` (after spaces): a brace group, a control
    sequence or one character.  (pos, pos) if there is none."""
    m = re.compile(r"\s*").match(tex, pos)
    i = m.end()
    if i >= len(tex):
        return pos, pos
    if tex[i] == "{":
        return i, _group_end(tex, i)
    if tex[i] == "}":
        return pos, pos
    t = _TOKEN.match(tex, i)
    return i, t.end() if t and t.start() == i else i + 1


def _optional(tex: str, pos: int) -> int:
    """End of the [..] optional arguments at `pos` (after spaces), or `pos`."""
    m = re.compile(r"(?:\s*\[[^\[\]{}]*\])*").match(tex, pos)
    return m.end() if m.group().strip() else pos


def _text_spans(tex: str) -> List[Tuple[int, int]]:
    """The arguments of text commands (\\text{..}, \\label{..}...), where nothing is rewritten."""
    spans = []
    for m in _TOKEN.finditer(tex):
        if m.group() in _TEXT_ARG:
            start, end = _argument(tex, _optional(tex, m.end()))
            if end > start:
                spans.append((start, end))
    return spans


def _find(tex: str, pattern: "re.Pattern", start: int = 0):
    """The first match of `pattern` at a token boundary, outside comments and text arguments."""
    spans = _text_spans(tex)
    for m in _TOKEN.finditer(tex, start):
        if m.group().startswith("%") or any(a <= m.start() < b for a, b in spans):
            continue
        hit = pattern.match(tex, m.start())
        if hit:
            return hit
    return None


def _scope_end(tex: str, pos: int) -> int:
    """Where the scope of a font switch ending at `pos` ends: the close of the current group,
    the next & or \\\\ of the current level, \\right, \\end{...}, or the end of the TeX."""
    depth = 0
    for m in _TOKEN.finditer(tex, pos):
        tok = m.group()
        if tok.startswith("%"):
            continue
        if tok == "{" or m.group("be") == "begin" or tok == "\\left":
            depth += 1
        elif tok == "}" or m.group("be") == "end" or tok == "\\right":
            if not depth:
                return m.start()
            depth -= 1
        elif not depth and tok in _SCOPE_ENDS:
            return m.start()
    return len(tex)


_SWITCH_RE = re.compile(r"(%s)%s" % ("|".join(re.escape(s) for s in _SWITCHES), _CS))


def _switches_in_text(tex: str) -> str:
    """Inside a text argument (``\\text{for {\\rm all}}``) a switch is a text font change that
    texmath does not parse: dropped (Office shows that text upright anyway)."""
    spans = _text_spans(tex)
    if not spans:
        return tex
    out, last = [], 0
    for m in _SWITCH_RE.finditer(tex):
        if any(a <= m.start() < b for a, b in spans):
            out.append(tex[last:m.start()])
            last = m.end()
            if tex[last:last + 1] == " ":
                last += 1
    return "".join(out) + tex[last:]


def _font_switches(tex: str) -> str:
    """``{\\rm OOD}`` -> ``{\\mathrm{OOD}}``, ``\\rm{x}`` -> ``\\mathrm{x}``, ``\\cal L`` -> ``\\mathcal{L}``."""
    tex = _switches_in_text(tex)
    pos = 0
    while True:
        m = _find(tex, _SWITCH_RE, pos)
        if not m:
            return tex
        end = _scope_end(tex, m.end())
        scope = tex[m.end():end]
        body = scope.strip()
        if body.startswith("{") and _group_end(body, 0) == len(body):
            body = body[1:-1]  # \rm{OOD}: the group is the whole scope
        new = "%s{%s}" % (_SWITCHES[m.group(1)], body) + scope[len(scope.rstrip()):] if body else ""
        tex = tex[:m.start()] + new + tex[end:]
        pos = m.start() + (len(_SWITCHES[m.group(1)]) + 1 if new else 0)  # nested switches inside the new group


def _bold_upright(tex: str) -> str:
    """``\\mathbf{x}`` -> 𝐱: texmath 0.13 writes \\mathbf on a Latin letter as bold *italic* MathML /
    OMML, while LaTeX sets it upright; given the bold character itself it writes ``<mi>𝐱</mi>``,
    which is also what Word exports for a bold upright letter.  Only for plain letters and digits."""
    pattern = re.compile(r"\\mathbf" + _CS)
    pos = 0
    while True:
        m = _find(tex, pattern, pos)
        if not m:
            return tex
        start, end = _argument(tex, m.end())
        arg = tex[start:end]
        inner = arg[1:-1] if arg.startswith("{") else arg
        if not re.fullmatch(r"[A-Za-z0-9 ]+", inner or "") or not inner.strip():
            pos = m.end()
            continue
        bold = "".join(_BOLD.get(ch, "") for ch in inner)
        tex = tex[:m.start()] + "{%s}" % bold + tex[end:]
        pos = m.start() + len(bold) + 2


_BOLD = {**{chr(ord("A") + i): chr(0x1D400 + i) for i in range(26)},
         **{chr(ord("a") + i): chr(0x1D41A + i) for i in range(26)},
         **{chr(ord("0") + i): chr(0x1D7CE + i) for i in range(10)}}


def _rename(tex: str, table: Dict[str, str], hits: List[str]) -> str:
    # a control word ends at a non-letter; a control symbol (\|) at once
    pattern = re.compile(r"(%s)" % "|".join(re.escape(k) + (_CS if k[1:].isalpha() else "") for k in table))
    pos = 0
    while True:
        m = _find(tex, pattern, pos)
        if not m:
            return tex
        hits.append(m.group(1))
        new = table[m.group(1)]
        if new[-1:].isalpha() and tex[m.end():m.end() + 1].isalpha():
            new += " "  # \|g -> \Vert g, not the control word \Vertg
        tex = tex[:m.start()] + new + tex[m.end():]
        pos = m.start() + len(new)


def _commands(tex: str, names: Sequence[str], how: str, hits: List[str]) -> str:
    """`how`: "unwrap" (\\cmd[opt]{x} -> {x}), "drop_arg" (\\cmd{x} -> nothing), "drop" (\\cmd -> nothing)."""
    pattern = re.compile(r"(%s)%s" % ("|".join(re.escape(k) for k in names), _CS))
    pos = 0
    while True:
        m = _find(tex, pattern, pos)
        if not m:
            return tex
        hits.append(m.group(1))
        if how == "drop":
            new, end = "", m.end()
        else:
            start, end = _argument(tex, _optional(tex, m.end()))
            arg = tex[start:end]
            new = "" if how == "drop_arg" else (arg if arg.startswith("{") else "{%s}" % arg)
        tex = tex[:m.start()] + new + tex[end:]
        pos = m.start()


def _textcolor(tex: str, hits: List[str]) -> str:
    """\\textcolor{c}{x} -> {\\color{c}x} (texmath reads \\color and drops the colour)."""
    pattern = re.compile(r"\\textcolor" + _CS)
    while True:
        m = _find(tex, pattern)
        if not m:
            return tex
        hits.append("\\textcolor")
        c0, c1 = _argument(tex, _optional(tex, m.end()))
        a0, a1 = _argument(tex, c1)
        colour, arg = tex[c0:c1], tex[a0:a1]
        tex = tex[:m.start()] + "{\\color%s %s}" % (colour, arg) + tex[a1:]


def _environments(tex: str, hits: List[str]) -> str:
    def begin(m):
        env = m.group(1)
        hits.append(env)
        name, before, _ = _ENVS[env]
        return before + "\\begin{%s}" % name

    def end(m):
        name, _, after = _ENVS[m.group(1)]
        return "\\end{%s}" % name.split("}")[0] + after

    names = "|".join(re.escape(e) for e in sorted(_ENVS, key=len, reverse=True))
    # matrix* takes an optional column alignment [r], which texmath would read as maths
    tex = re.sub(r"\\begin\s*\{(%s)\}(?:\s*\[[^\[\]]*\])?" % names, begin, tex)
    return re.sub(r"\\end\s*\{(%s)\}" % names, end, tex)


# ---- definitions ----------------------------------------------------------------------------------
_DEF_START = re.compile(
    r"\\(?P<cmd>(?:re|provide)?newcommand\*?|DeclareRobustCommand\*?|DeclareMathOperator\*?|[gex]?def|let"
    r"|DeclarePairedDelimiter)%s" % _CS)


@dataclass
class Definition:
    name: str      # \foo
    source: str    # the definition as texmath takes it (\newcommand..., \DeclareMathOperator...)
    uses: frozenset  # control words in its body


def _control_words(tex: str) -> set:
    return set(re.findall(r"\\[a-zA-Z@]+", tex))


def _parse_definition(text: str, m) -> Optional[Tuple[Definition, int]]:
    """The definition starting at match `m` of _DEF_START, as texmath takes it, and where it ends."""
    cmd = m.group("cmd")
    pos = m.end()
    if cmd == "let":
        lm = re.compile(r"\s*(\\[a-zA-Z@]+)\s*=?\s*(\\[a-zA-Z@]+|\\.|[^\s\\])").match(text, pos)
        if not lm:
            return None
        name, target = lm.groups()
        return Definition(name, "\\newcommand{%s}{%s}" % (name, target), frozenset({target})), lm.end()
    if cmd.endswith("def"):
        dm = re.compile(r"\s*(\\[a-zA-Z@]+)((?:#\d)*)\s*\{").match(text, pos)
        if not dm:  # delimited parameters (\def\foo#1.{...}) have no \newcommand equivalent
            return None
        name, params = dm.group(1), dm.group(2)
        start = dm.end() - 1
        end = _group_end(text, start)
        body = text[start + 1:end - 1]
        n = len(params) // 2
        source = "\\newcommand{%s}%s{%s}" % (name, "[%d]" % n if n else "", body)
        return Definition(name, source, frozenset(_control_words(body))), end
    # \newcommand{\foo}[n][default]{body}, \newcommand\foo..., \DeclareMathOperator*{\foo}{text}
    nm = re.compile(r"\s*(?:\{\s*(\\[a-zA-Z@]+)\s*\}|(\\[a-zA-Z@]+))").match(text, pos)
    if not nm:
        return None
    name = nm.group(1) or nm.group(2)
    pos = nm.end()
    if cmd == "DeclarePairedDelimiter":
        l0, l1 = _argument(text, pos)
        r0, r1 = _argument(text, l1)
        if r1 <= r0:
            return None
        left, right = text[l0:l1].strip("{}"), text[r0:r1].strip("{}")
        source = "\\newcommand{%s}[1]{%s #1 %s}" % (name, left, right)
        return Definition(name, source, frozenset(_control_words(left + right))), r1
    om = re.compile(r"(\s*\[[^\[\]]*\])*").match(text, pos)
    b0, b1 = _argument(text, om.end())
    if b1 <= b0 or text[b0] != "{":
        return None
    star = "*" if cmd.startswith("DeclareMathOperator*") else ""
    if cmd.startswith("DeclareMathOperator"):
        source = "\\DeclareMathOperator%s{%s}%s" % (star, name, text[b0:b1])
    else:
        source = "\\newcommand{%s}%s%s" % (name, text[pos:om.end()].strip(), text[b0:b1])
    return Definition(name, source, frozenset(_control_words(text[pos:b1]))), b1


def _strip_comments(text: str) -> str:
    return re.sub(r"(?<!\\)%[^\n]*", "", text)


def parse_definitions(text: str) -> "Tuple[List[Definition], List[str]]":
    """The macro definitions in LaTeX source (a preamble, a macro file), and the names of those
    that cannot be used (delimited \\def parameters, malformed)."""
    text = _strip_comments(text)
    defs: List[Definition] = []
    skipped: List[str] = []
    pos = 0
    while True:
        m = _DEF_START.search(text, pos)
        if not m:
            return defs, skipped
        parsed = _parse_definition(text, m)
        if parsed is None:
            nm = re.compile(r"\s*\{?\s*(\\[a-zA-Z@]+)").match(text, m.end())
            skipped.append(nm.group(1) if nm else "\\" + m.group("cmd"))
            pos = m.end()
            continue
        defs.append(parsed[0])
        pos = parsed[1]


def normalize_definitions(tex: str) -> str:
    """\\def, \\let, \\DeclarePairedDelimiter in the TeX itself, rewritten as \\newcommand
    (texmath only knows \\newcommand, \\renewcommand, \\providecommand and \\DeclareMathOperator)."""
    out, last, pos = [], 0, 0
    while True:
        m = _DEF_START.search(tex, pos)
        if not m:
            break
        if m.group("cmd") in ("let", "DeclarePairedDelimiter") or m.group("cmd").endswith("def"):
            parsed = _parse_definition(tex, m)
            if parsed:
                out.append(tex[last:m.start()] + parsed[0].source)
                last = pos = parsed[1]
                continue
        pos = m.end()
    return "".join(out) + tex[last:]


class Macros:
    """Macro definitions from the user's files and text, applied to every LaTeX input."""

    def __init__(self, files: Sequence[str] = (), text: str = ""):
        self.files = list(files)
        self.text = text
        self.definitions: Dict[str, Definition] = {}
        self.skipped: List[str] = []
        self.errors: List[str] = []
        for path in self.files:
            try:
                with open(path, "rb") as f:
                    data = f.read()
            except OSError as e:
                self.errors.append("cannot read %s: %s" % (path, e.strerror or e))
                continue
            self._add(data.decode("utf-8-sig", "replace"))
        self._add(text)

    def _add(self, text: str) -> None:
        defs, skipped = parse_definitions(text)
        for d in defs:
            self.definitions[d.name] = d  # a later definition wins, as in LaTeX with \renewcommand
        self.skipped += skipped

    def __len__(self) -> int:
        return len(self.definitions)

    def needed(self, tex: str) -> List[Definition]:
        """The definitions `tex` uses, directly or through other definitions, in dependency order;
        not those that `tex` defines itself."""
        own = {d.name for d in parse_definitions(tex)[0]}
        order: List[Definition] = []
        seen: set = set()

        def visit(name):
            d = self.definitions.get(name)
            if d is None or name in seen or name in own:
                return
            seen.add(name)
            for used in sorted(d.uses):
                visit(used)
            order.append(d)

        for name in sorted(_control_words(tex)):
            visit(name)
        return order


_MACRO_CACHE: Dict[tuple, Macros] = {}


def load_macros(files: Sequence[str] = (), text: str = "") -> Optional[Macros]:
    """A Macros object for these files and text, re-read only when a file changes."""
    files = [f for f in files if f]
    if not files and not text.strip():
        return None
    stamps = []
    for f in files:
        try:
            st = os.stat(f)
            stamps.append((f, st.st_mtime_ns, st.st_size))
        except OSError:
            stamps.append((f, None, None))
    key = (tuple(stamps), text)
    if key not in _MACRO_CACHE:
        if len(_MACRO_CACHE) > 16:
            _MACRO_CACHE.clear()
        _MACRO_CACHE[key] = Macros(files, text)
    return _MACRO_CACHE[key]


# ---- the whole rewrite -------------------------------------------------------------------------------
_NOTES = {
    "colour": "colours were dropped (texmath has no colour)",
    "resized": "%s: written as the plain-size version",
    "layout": "%s: layout-only commands were left out",
}


def for_texmath(tex: str, macros: Optional[Macros] = None, target: str = "mathml") -> "Tuple[str, List[str]]":
    """`tex` rewritten for texmath, with the definitions it needs from `macros` prepended;
    plus notes on what was approximated.  `target`: the output format (bold letters are written
    as bold characters for MathML / OMML only, see :func:`_bold_upright`)."""
    hits: List[str] = []
    resized: List[str] = []
    layout: List[str] = []
    out = normalize_definitions(tex)
    out = _environments(out, hits)
    out = _font_switches(out)
    out = _rename(out, _RENAMES, hits)
    out = _rename(out, _SYMBOLS, [])
    if target in ("mathml", "omml"):
        out = _bold_upright(out)
    out = _rename(out, _RESIZED, resized)
    out = _textcolor(out, hits)
    out = _commands(out, _UNWRAP, "unwrap", layout)
    out = _commands(out, _DROP_WITH_ARG, "drop_arg", layout)
    out = _commands(out, _DROP, "drop", layout)
    notes = []
    if "\\textcolor" in hits or re.search(r"\\color" + _CS, out):
        notes.append(_NOTES["colour"])
    if resized:
        notes.append(_NOTES["resized"] % ", ".join(sorted(set(resized))))
    if layout:
        notes.append(_NOTES["layout"] % ", ".join(sorted(set(layout))))
    if macros:
        needed = macros.needed(out)
        if needed:
            # each on its own line: a definition ending in a comment-free body cannot swallow the next
            prelude = "\n".join(_for_texmath_body(d.source, target) for d in needed)
            out = prelude + "\n" + out
    return out, notes


def _for_texmath_body(source: str, target: str) -> str:
    """A definition with the same rewrites as the TeX (a macro body may use \\rm or \\notag too)."""
    out = _environments(source, [])
    out = _font_switches(out)
    out = _rename(out, _RENAMES, [])
    out = _rename(out, _SYMBOLS, [])
    if target in ("mathml", "omml"):
        out = _bold_upright(out)
    out = _rename(out, _RESIZED, [])
    out = _textcolor(out, [])
    out = _commands(out, _UNWRAP, "unwrap", [])
    out = _commands(out, _DROP_WITH_ARG, "drop_arg", [])
    return _commands(out, _DROP, "drop", [])
