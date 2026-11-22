"""LaTeX that texmath does not take, rewritten into LaTeX it does - for conversions *out of* LaTeX.

texmath (inside pandoc) implements amsmath and a few packages, but real papers also use
plain-TeX font switches (``{\\rm OOD}``, ``\\cal L``), commands from other packages
(``\\notag``, ``\\mathbbm``, ``\\smash``, ``dcases``...) and their own macros.  :func:`for_texmath`

1. expands the macros an equation uses - its own definitions and those from the user's macro
   files (:class:`Macros`) - with pandoc's LaTeX reader, which implements ``\\def`` (also with
   delimited parameters), ``\\let``, ``\\newcommand`` with defaults and ``\\DeclareMathOperator``
   like TeX does; ``\\DeclarePairedDelimiter`` is expanded here (pandoc does not know it);
2. rewrites what is left into constructs texmath parses.

Only the TeX handed to texmath is rewritten: LaTeX -> LaTeX output keeps the user's source.
Rewrites that lose something (a colour, a strut) are reported as notes.
"""
from __future__ import annotations

import os
import re
from dataclasses import dataclass
from typing import Dict, List, Optional, Sequence, Tuple

# a control word, a control symbol, a comment, a brace, &, $; \begin / \end with their argument
_TOKEN = re.compile(r"\\(?P<be>begin|end)\s*\{(?P<env>[^{}]*)\}|\\[a-zA-Z@]+|\\.|%[^\n]*|[{}&$]", re.S)
_CS = r"(?![a-zA-Z@])"  # end of a control word
_GAP = re.compile(r"(?:\s|%[^\n]*(?:\n|$))*")  # what TeX skips before an argument: spaces and comments

# commands whose argument is text: nothing is rewritten inside it (but in maths nested in it)
_TEXT_ARG = frozenset(
    "\\text \\mbox \\hbox \\makebox \\fbox \\framebox \\textrm \\textit \\textbf \\textsf \\texttt \\textnormal "
    "\\textup \\textsl \\textsc \\textmd \\emph \\label \\tag \\ref \\eqref \\operatorname \\DeclareMathOperator".split())
_VERBATIM_ARG = frozenset("\\label \\tag \\ref \\eqref \\operatorname \\DeclareMathOperator".split())

# plain-TeX font switches (scope: the rest of the group) -> the LaTeX command taking an argument
_SWITCHES = {"\\rm": "\\mathrm", "\\bf": "\\mathbf", "\\it": "\\mathit", "\\sl": "\\mathit", "\\sf": "\\mathsf",
             "\\tt": "\\mathtt", "\\cal": "\\mathcal"}
# where the scope of a switch ends at the current group level ($: the end of maths nested in text)
_SCOPE_ENDS = frozenset(("&", "\\\\", "\\cr", "\\right", "\\middle", "$"))

# exact or near-exact equivalents (renamed in place)
_RENAMES = {"\\notag": "\\nonumber", "\\mathbbm": "\\mathbb", "\\mathbbmss": "\\mathbb", "\\Bbb": "\\mathbb",
            "\\frak": "\\mathfrak", "\\textup": "\\textrm", "\\textsc": "\\textrm", "\\hbox": "\\mbox",
            "\\hphantom": "\\phantom"}
# symbols texmath 0.13 writes as the wrong character: \| as U+2225 PARALLEL TO (not U+2016, a norm),
# \setminus as a backslash, \varnothing as U+2300 DIAMETER SIGN
_SYMBOLS = {"\\|": "\\Vert", "\\setminus": "\\smallsetminus", "\\varnothing": "\\emptyset"}
# same maths, different size: renamed, with a note
_RESIZED = {"\\tbinom": "\\binom", "\\dbinom": "\\binom", "\\nicefrac": "\\tfrac"}
# commands that only adjust the layout: \cmd[opt]{x} -> {x}; value: how many [options] the command takes
_UNWRAP = {"\\smash": 1, "\\mathclap": 0, "\\mathllap": 0, "\\mathrlap": 0, "\\clap": 0, "\\llap": 0, "\\rlap": 0}
# struts: dropped with their argument (an empty group stays, so that \sin\vphantom{x}h is no \sinh)
_DROP_WITH_ARG = ("\\vphantom",)
_DROP = ("\\mathstrut", "\\strut")
# environments: name -> (texmath environment, opening extra, closing extra)
_ENVS = {"dcases": ("cases", "", ""), "rcases": ("array}{ll", "\\left.", "\\right\\}"),
         "drcases": ("array}{ll", "\\left.", "\\right\\}"),
         "matrix*": ("matrix", "", ""), "pmatrix*": ("pmatrix", "", ""), "bmatrix*": ("bmatrix", "", ""),
         "Bmatrix*": ("Bmatrix", "", ""), "vmatrix*": ("vmatrix", "", ""), "Vmatrix*": ("Vmatrix", "", ""),
         "smallmatrix*": ("smallmatrix", "", "")}
# mathtools' starred cases: the second column is text (with $...$ for maths)
_STARRED_CASES = {"cases*": ("cases", "", ""), "dcases*": ("cases", "", ""),
                  "rcases*": ("array}{ll", "\\left.", "\\right\\}"), "drcases*": ("array}{ll", "\\left.", "\\right\\}")}
STARRED_CASES = frozenset(_STARRED_CASES)


def _group_end(tex: str, start: int) -> int:
    """Index just after the brace group opening at `start` (``tex[start] == "{"``); -1 if it is not closed."""
    depth = 0
    for m in _TOKEN.finditer(tex, start):
        tok = m.group()
        if tok == "{":
            depth += 1
        elif tok == "}":
            depth -= 1
            if not depth:
                return m.end()
    return -1


def _bracket_end(tex: str, start: int) -> int:
    """Index just after the [option] opening at `start`; braces protect brackets (``[{[x]}]``). -1 if open."""
    depth = 0
    i = start + 1
    while i < len(tex):
        ch = tex[i]
        if ch == "\\":
            i += 2
            continue
        if ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
        elif ch == "]" and depth <= 0:
            return i + 1
        i += 1
    return -1


def _argument(tex: str, pos: int) -> Tuple[int, int]:
    """(start, end) of the TeX argument at `pos` (after spaces and comments): a brace group, a control
    sequence or one character.  (pos, pos) if there is none."""
    i = _GAP.match(tex, pos).end()
    if i >= len(tex) or tex[i] == "}":
        return pos, pos
    if tex[i] == "{":
        end = _group_end(tex, i)
        return i, end if end > 0 else len(tex)
    t = _TOKEN.match(tex, i)
    return i, t.end() if t and t.start() == i else i + 1


def _optional(tex: str, pos: int, count: int = 1) -> int:
    """End of at most `count` [options] at `pos` (after spaces and comments), or `pos`."""
    end = pos
    for _ in range(count):
        i = _GAP.match(tex, end).end()
        if tex[i:i + 1] != "[":
            break
        j = _bracket_end(tex, i)
        if j < 0:
            break
        end = j
    return end


def _unbrace(arg: str) -> str:
    """An argument without its one outer brace pair: ``{\\{}`` -> ``\\{``."""
    arg = arg.strip()
    return arg[1:-1] if arg.startswith("{") and _group_end(arg, 0) == len(arg) else arg


def _maths_in(text: str, start: int, end: int) -> List[Tuple[int, int]]:
    """$...$ and \\(...\\) inside the text tex[start:end] (maths nested in a \\text argument)."""
    spans = []
    for m in re.finditer(r"(?<!\\)\$(?:\\.|[^$\\])*\$|\\\((?:\\.|[^\\])*?\\\)", text[start:end]):
        spans.append((start + m.start(), start + m.end()))
    return spans


def _text_spans(tex: str) -> List[Tuple[int, int]]:
    """The text parts of the arguments of text commands (\\text{..}, \\label{..}...), where nothing is
    rewritten - without maths nested in them (``\\text{for $\\rm x$}``)."""
    spans = []
    for m in _TOKEN.finditer(tex):
        if m.group() in _TEXT_ARG:
            start, end = _argument(tex, _optional(tex, m.end(), 3))
            if end <= start:
                continue
            maths = [] if m.group() in _VERBATIM_ARG else _maths_in(tex, start, end)
            last = start
            for a, b in maths:
                spans.append((last, a))
                last = b
            spans.append((last, end))
    return spans


def _in(spans: List[Tuple[int, int]], pos: int) -> bool:
    return any(a <= pos < b for a, b in spans)


def _find(tex: str, pattern: "re.Pattern", start: int = 0):
    """The first match of `pattern` at a token boundary, outside comments and text."""
    spans = _text_spans(tex)
    for m in _TOKEN.finditer(tex, start):
        if m.group().startswith("%") or _in(spans, m.start()):
            continue
        hit = pattern.match(tex, m.start())
        if hit:
            return hit
    return None


def _scope_end(tex: str, pos: int) -> int:
    """Where the scope of a font switch ending at `pos` ends: the close of the current group,
    the next & or \\\\ of the current level, \\right, \\end{...}, the end of nested maths, or the end."""
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
    """Inside text (``\\text{for {\\rm all}}``) a switch is a text font change that texmath does not
    parse: dropped (Office shows that text upright anyway)."""
    spans = _text_spans(tex)
    if not spans:
        return tex
    out, last = [], 0
    for m in _SWITCH_RE.finditer(tex):
        if _in(spans, m.start()):
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
        # a comment in the scope would swallow the closing brace: TeX ignores it anyway
        body = re.sub(r"(?<!\\)%[^\n]*", "", scope).strip()
        if body.startswith("{") and _group_end(body, 0) == len(body):
            body = body[1:-1]  # \rm{OOD}: the group is the whole scope
        new = "%s{%s}" % (_SWITCHES[m.group(1)], body) + scope[len(scope.rstrip()):] if body else ""
        tex = tex[:m.start()] + new + tex[end:]
        pos = m.start() + (len(_SWITCHES[m.group(1)]) + 1 if new else 0)  # nested switches inside the new group


def _bold_upright(tex: str) -> str:
    """``\\mathbf{x}`` -> 𝐱: texmath 0.13 writes \\mathbf on a Latin letter as bold *italic* MathML /
    OMML, while LaTeX sets it upright; given the bold character itself it writes ``<mi>𝐱</mi>``,
    which is also what Word exports for a bold upright letter.  Only for an argument of letters, digits
    and operators (``\\mathbf{x+y}``: as in LaTeX, the operators stay as they are)."""
    pattern = re.compile(r"\\mathbf" + _CS)
    pos = 0
    while True:
        m = _find(tex, pattern, pos)
        if not m:
            return tex
        start, end = _argument(tex, m.end())
        inner = _unbrace(tex[start:end])
        if not re.fullmatch(r"[A-Za-z0-9 +\-=<>,.;:!?'()\[\]/|*]*", inner) or not re.search(r"[A-Za-z0-9]", inner):
            pos = m.end()
            continue
        bold = "".join(_BOLD.get(ch, ch) for ch in inner)
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
        if new[-1:].isalpha() and re.match(r"[a-zA-Z@#]", tex[m.end():m.end() + 1]):
            new += " "  # \|g -> \Vert g, \|#1 -> \Vert #1: not the control words \Vertg, \Vert#1...
        tex = tex[:m.start()] + new + tex[m.end():]
        pos = m.start() + len(new)


def _commands(tex: str, names, how: str, hits: List[str]) -> str:
    """`how`: "unwrap" (\\cmd[opt]{x} -> {x}; `names` maps each to its number of options), "drop_arg"
    (\\cmd{x} -> {}), "drop" (\\cmd -> {}).  The empty group keeps the atoms around apart."""
    pattern = re.compile(r"(%s)%s" % ("|".join(re.escape(k) for k in names), _CS))
    pos = 0
    while True:
        m = _find(tex, pattern, pos)
        if not m:
            return tex
        hits.append(m.group(1))
        if how == "drop":
            new, end = "{}", m.end()
        else:
            options = names[m.group(1)] if isinstance(names, dict) else 0
            start, end = _argument(tex, _optional(tex, m.end(), options))
            new = "{}" if how == "drop_arg" else "{%s}" % _unbrace(tex[start:end])
        tex = tex[:m.start()] + new + tex[end:]
        pos = m.start() + (2 if how != "unwrap" else 0)


def _textcolor(tex: str, hits: List[str]) -> str:
    """\\textcolor[model]{c}{x} -> {\\color{c}x} (texmath reads \\color and drops the colour)."""
    pattern = re.compile(r"\\textcolor" + _CS)
    while True:
        m = _find(tex, pattern)
        if not m:
            return tex
        hits.append("\\textcolor")
        c0, c1 = _argument(tex, _optional(tex, m.end(), 1))
        a0, a1 = _argument(tex, c1)
        tex = tex[:m.start()] + "{\\color{%s} %s}" % (_unbrace(tex[c0:c1]), tex[a0:a1]) + tex[a1:]


def _env_end(tex: str, pos: int, env: str) -> Tuple[int, int]:
    depth = 1
    for m in _TOKEN.finditer(tex, pos):
        if m.group("be") and m.group("env") == env:
            depth += 1 if m.group("be") == "begin" else -1
            if not depth:
                return m.start(), m.end()
    return -1, -1


def _split_top(body: str, separators: Tuple[str, ...]) -> List[str]:
    """`body` split at its own (not nested) separators: & / \\\\."""
    parts, start, depth = [], 0, 0
    for m in _TOKEN.finditer(body):
        tok = m.group()
        if tok == "{" or m.group("be") == "begin":
            depth += 1
        elif tok == "}" or m.group("be") == "end":
            depth -= 1
        elif not depth and tok in separators:
            parts.append(body[start:m.start()])
            start = m.end()
    parts.append(body[start:])
    return parts


def _text_cell(cell: str) -> str:
    """A text column cell of starred cases (``if $x>0$``) as maths: ``\\text{if }x>0``."""
    parts = re.split(r"(?<!\\)\$", cell)
    out = []
    for i, part in enumerate(parts):
        if i % 2:
            out.append(part)
        elif part.strip():
            out.append("\\text{%s}" % part.strip("\n"))
    return "".join(out)


def _environments(tex: str, hits: List[str]) -> str:
    # starred cases: rewrite their text column first
    for env, (name, before, after) in _STARRED_CASES.items():
        while True:
            m = re.search(r"\\begin\s*\{%s\}" % re.escape(env), tex)
            if not m:
                break
            end, end2 = _env_end(tex, m.end(), env)
            if end < 0:
                break
            hits.append(env)
            rows = []
            for row in _split_top(tex[m.end():end], ("\\\\", "\\cr")):
                cells = _split_top(row, ("&",))
                if len(cells) > 1:
                    cells[1] = _text_cell(cells[1])
                rows.append("&".join(cells))
            tex = (tex[:m.start()] + before + "\\begin{%s}" % name + "\\\\".join(rows) +
                   "\\end{%s}" % name.split("}")[0] + after + tex[end2:])

    def begin(m):
        env = m.group(1)
        hits.append(env)
        name, before, _ = _ENVS[env]
        return before + "\\begin{%s}" % name

    def end(m):
        name, _, after = _ENVS[m.group(1)]
        return "\\end{%s}" % name.split("}")[0] + after

    names = "|".join(re.escape(e) for e in sorted(_ENVS, key=len, reverse=True))
    # matrix* takes an optional column alignment [r], which texmath would read as maths; nothing else does
    tex = re.sub(r"\\begin\s*\{([a-zA-Z]*matrix\*)\}\s*\[[^\[\]{}]*\]", lambda m: "\\begin{%s}" % m.group(1), tex)
    tex = re.sub(r"\\begin\s*\{(%s)\}" % names, begin, tex)
    return re.sub(r"\\end\s*\{(%s)\}" % names, end, tex)


# ---- definitions ----------------------------------------------------------------------------------
_DEF_START = re.compile(
    r"\\(?P<cmd>(?:re)?newcommand\*?|providecommand\*?|DeclareRobustCommand\*?|DeclareMathOperator\*?"
    r"|[gex]?def|let|DeclarePairedDelimiter)%s" % _CS)


@dataclass
class Definition:
    name: str                               # \foo
    source: str                             # the definition as written; pandoc expands it
    uses: frozenset                         # control words in it
    paired: Optional[Tuple[str, str]] = None  # \DeclarePairedDelimiter: the left and right delimiter
    span: Tuple[int, int] = (0, 0)          # where it is in the text it was read from


def _control_words(tex: str) -> set:
    return set(re.findall(r"\\[a-zA-Z@]+", tex))


def _in_comment(text: str, pos: int) -> bool:
    line = text[text.rfind("\n", 0, pos) + 1:pos]
    return bool(re.search(r"(?<!\\)(?:\\\\)*%", line))


def _parse_definition(text: str, m) -> Optional[Definition]:
    cmd = m.group("cmd")
    pos = m.end()
    if cmd == "let":
        lm = re.compile(r"\s*(\\[a-zA-Z@]+)\s*=?\s*(\\[a-zA-Z@]+|\\.|[^\s\\])").match(text, pos)
        if not lm:
            return None
        return Definition(lm.group(1), text[m.start():lm.end()], frozenset({lm.group(2)}), span=(m.start(), lm.end()))
    nm = re.compile(r"\s*(?:\{\s*(\\[a-zA-Z@]+)\s*\}|(\\[a-zA-Z@]+))").match(text, pos)
    if not nm:
        return None
    name = nm.group(1) or nm.group(2)
    pos = nm.end()
    if cmd.endswith("def"):  # \def\foo#1#2{..}, \def\foo #1.{..}: parameter text up to the body
        brace = text.find("{", pos)
        if brace < 0 or "}" in text[pos:brace]:
            return None
        end = _group_end(text, brace)
    elif cmd == "DeclarePairedDelimiter":
        l0, l1 = _argument(text, pos)
        r0, r1 = _argument(text, l1)
        if r1 <= r0 or text[r0:r1] == "" or (text[r0] == "{" and _group_end(text, r0) < 0):
            return None
        left, right = _unbrace(text[l0:l1]), _unbrace(text[r0:r1])
        return Definition(name, text[m.start():r1], frozenset(_control_words(left + right)), (left, right),
                          (m.start(), r1))
    else:  # \newcommand{\foo}[n][default]{body}, \DeclareMathOperator*{\foo}{text}
        if not cmd.startswith("DeclareMathOperator"):
            pos = _optional(text, pos, 2)
        b0 = _GAP.match(text, pos).end()
        if text[b0:b0 + 1] != "{":
            return None
        end = _group_end(text, b0)
    if end < 0:  # the body is never closed
        return None
    source = text[m.start():end]
    return Definition(name, source, frozenset(_control_words(source[len(m.group()):]) - {name}), span=(m.start(), end))


def parse_definitions(text: str) -> "Tuple[List[Definition], List[str]]":
    """The macro definitions in LaTeX source (a preamble, a macro file, an equation), and the names
    of those that cannot be used (malformed, never closed)."""
    defs: List[Definition] = []
    skipped: List[str] = []
    pos = 0
    while True:
        m = _DEF_START.search(text, pos)
        if not m:
            return defs, skipped
        if _in_comment(text, m.start()):
            pos = m.end()
            continue
        d = _parse_definition(text, m)
        if d is None:
            nm = re.compile(r"\s*\{?\s*(\\[a-zA-Z@]+)").match(text, m.end())
            skipped.append(nm.group(1) if nm else "\\" + m.group("cmd"))
            pos = m.end()
            continue
        defs.append(d)
        pos = d.span[1]


class Macros:
    """Macro definitions from the user's files and text, applied to every LaTeX input."""

    def __init__(self, files: Sequence[str] = (), text: str = ""):
        self.files = list(files)
        self.text = text
        self.definitions: Dict[str, Definition] = {}
        self.order: List[Definition] = []  # every definition, in file order (\let takes a snapshot)
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
            self.order.append(d)
        self.skipped += skipped

    def __len__(self) -> int:
        return len(self.definitions)

    def needed(self, tex: str) -> List[Definition]:
        """The definitions `tex` uses, directly or through other definitions, in file order (so that
        \\let and redefinitions behave as in the file); not those that `tex` defines itself."""
        own = {d.name for d in parse_definitions(tex)[0]}
        names: set = set()
        todo = [n for n in _control_words(tex) if n not in own]
        while todo:
            name = todo.pop()
            if name in names or name not in self.definitions:
                continue
            names.add(name)
            for d in self.order:
                if d.name == name:
                    todo.extend(d.uses)
        return [d for d in self.order if d.name in names]


_MACRO_CACHE: Dict[tuple, Macros] = {}


def file_stamps(files: Sequence[str]) -> tuple:
    stamps = []
    for f in files:
        try:
            st = os.stat(f)
            stamps.append((f, st.st_mtime_ns, st.st_size))
        except OSError:
            stamps.append((f, None, None))
    return tuple(stamps)


def load_macros(files: Sequence[str] = (), text: str = "") -> Optional[Macros]:
    """A Macros object for these files and text (None for neither), re-read only when a file changes."""
    files = [f for f in files if f]
    if not files and not text.strip():
        return None
    key = (file_stamps(files), text)
    if key not in _MACRO_CACHE:
        if len(_MACRO_CACHE) > 16:
            _MACRO_CACHE.clear()
        _MACRO_CACHE[key] = Macros(files, text)
    return _MACRO_CACHE[key]


# ---- expansion ----------------------------------------------------------------------------------------
def _paired(tex: str, paired: Dict[str, Tuple[str, str]]) -> str:
    """Uses of \\DeclarePairedDelimiter macros: ``\\abs{x}`` -> ``\\lvert x\\rvert``, ``\\abs*{x}`` ->
    ``\\left\\lvert x\\right\\rvert``, ``\\abs[\\big]{x}`` -> ``\\bigl\\lvert x\\bigr\\rvert``."""
    if not paired:
        return tex
    pattern = re.compile(r"(%s)%s" % ("|".join(re.escape(k) for k in paired), _CS))
    pos = 0
    while True:
        m = _find(tex, pattern, pos)
        if not m:
            return tex
        left, right = paired[m.group(1)]
        i = _GAP.match(tex, m.end()).end()
        size = ""
        if tex[i:i + 1] == "*":
            opening, closing, i = "\\left", "\\right", i + 1
        else:
            j = _optional(tex, i, 1)
            if j > i:
                size = tex[i:j].strip()[1:-1].strip()
                i = j
            opening, closing = (size + "l", size + "r") if size else ("", "")
        a0, a1 = _argument(tex, i)
        new = "%s%s %s%s%s " % (opening, left, _unbrace(tex[a0:a1]), closing, right)
        tex = tex[:m.start()] + new + tex[a1:]
        pos = m.start()  # nested uses inside the argument


_PANDOC_MATH_ENVS = frozenset(
    "equation equation* displaymath math align align* alignat alignat* flalign flalign* eqnarray eqnarray* "
    "gather gather* multline multline*".split())


def _expand(body: str, sources: List[str]) -> Optional[str]:
    """`body` with the macros of `sources` (definitions as written) expanded by pandoc's LaTeX reader;
    None if pandoc does not give back exactly one equation."""
    from .pandoc_engine import ConversionError, _read_json
    env = re.fullmatch(r"\s*\\begin\s*\{([a-zA-Z]+\*?)\}[\s\S]*\\end\s*\{\1\}\s*", body)
    wrapped = body if env and env.group(1) in _PANDOC_MATH_ENVS else "\\[" + body + "\n\\]"
    try:
        eqs = _read_json("latex", ("\n".join(sources) + "\n" + wrapped).encode("utf-8"))
    except ConversionError:
        return None
    return eqs[0].tex if len(eqs) == 1 else None


def _without(text: str, spans: List[Tuple[int, int]]) -> str:
    out, last = [], 0
    for a, b in sorted(spans):
        out.append(text[last:a])
        last = b
    return "".join(out) + text[last:]


# ---- the whole rewrite -------------------------------------------------------------------------------
_NOTES = {
    "colour": "colours were dropped (texmath has no colour)",
    "resized": "%s: written as the plain-size version",
    "layout": "%s: layout-only commands were left out",
    "unexpanded": "the macros could not be expanded together with this equation; texmath was given the "
                  "definitions instead",
}


def for_texmath(tex: str, macros: Optional[Macros] = None, target: str = "mathml") -> "Tuple[str, List[str]]":
    """`tex` rewritten for texmath, its macros expanded; plus notes on what was approximated.
    `target`: the output format (bold letters are written as bold characters for MathML / OMML only,
    see :func:`_bold_upright`)."""
    notes: List[str] = []
    own, _ = parse_definitions(tex)
    used = macros.needed(tex) if macros else []
    definitions = used + own
    paired = {d.name: d.paired for d in definitions if d.paired}
    plain = [d.source for d in definitions if not d.paired]
    out = _without(tex, [d.span for d in own]) if own else tex
    if plain:
        expanded = _expand(out, [d.source for d in used if not d.paired] + [d.source for d in own if not d.paired])
        if expanded is None:
            notes.append(_NOTES["unexpanded"])
            out = "\n".join(plain) + "\n" + out
        else:
            out = expanded
    out = _paired(out, paired)

    hits: List[str] = []
    resized: List[str] = []
    layout: List[str] = []
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
    if "\\textcolor" in hits or re.search(r"\\color" + _CS, out):
        notes.append(_NOTES["colour"])
    if resized:
        notes.append(_NOTES["resized"] % ", ".join(sorted(set(resized))))
    if layout:
        notes.append(_NOTES["layout"] % ", ".join(sorted(set(layout))))
    return out, notes
