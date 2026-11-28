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
_SCOPE_ENDS = frozenset(("&", "\\\\", "\\cr", "\\right", "\\middle", "$", "\\)"))

# exact or near-exact equivalents (renamed in place)
_RENAMES = {"\\notag": "\\nonumber", "\\mathbbm": "\\mathbb", "\\mathbbmss": "\\mathbb", "\\Bbb": "\\mathbb",
            "\\frak": "\\mathfrak", "\\textup": "\\textrm", "\\textsc": "\\textrm", "\\hbox": "\\mbox",
            "\\hphantom": "\\phantom"}
# symbols texmath 0.13 writes as the wrong character: \| as U+2225 PARALLEL TO (not U+2016, a norm),
# \setminus and \backslash swapped (a backslash and U+2216), \varnothing as U+2300 DIAMETER SIGN.
# One pass: \backslash -> \setminus is not renamed again.
_SYMBOLS = {"\\|": "\\Vert", "\\setminus": "\\smallsetminus", "\\backslash": "\\setminus",
            "\\varnothing": "\\emptyset"}
# texmath reads these Unicode operators correctly but emits different glyphs for their commands.
_OFFICE_SYMBOLS = {
    "\\leqslant": "⩽", "\\geqslant": "⩾", "\\amalg": "⨿",
    "\\longleftarrow": "⟵", "\\longrightarrow": "⟶", "\\longleftrightarrow": "⟷",
    "\\Longleftarrow": "⟸", "\\Longrightarrow": "⟹", "\\Longleftrightarrow": "⟺",
    "\\longmapsto": "⟼", "\\iff": "⟺",
    # Unsized norm bars also need the right character, with their original opening / closing class.
    "\\lVert": "\\mathopen{\\Vert}", "\\rVert": "\\mathclose{\\Vert}",
}
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


_SIZED_NORM = re.compile(r"(\\(?:left|right|middle|[bB]igg?[lrm]?)" + _CS + _GAP.pattern + r")\\[lr]Vert" + _CS)


def _sized_norms(tex: str) -> str:
    """texmath 0.13 writes a sized \\lVert / \\rVert (\\left\\lVert, \\middle\\lVert, \\bigr\\rVert...) as
    U+2225 PARALLEL TO, not the norm bar U+2016 that it writes for \\Vert there.
    With \\bigl and the like, texmath then writes the bars as plain symbols rather than a delimiter pair,
    as it already did for \\bigl\\| (the size is fixed there anyway)."""
    pos = 0
    while True:
        m = _find(tex, _SIZED_NORM, pos)
        if not m:
            return tex
        new = m.group(1) + "\\Vert"
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
    """A text column cell of starred cases (``if $x>0$``) as maths: ``\\text{if }{x>0}``.  Only the $ of
    the cell itself switch to maths (not those inside ``\\textbf{if $x$}``); comments are dropped."""
    cell = re.sub(r"(?<!\\)%[^\n]*\n?", "", cell)
    stripped = cell.strip()
    m = re.match(r"\\(?:text[a-z]*|mbox|emph)\s*\{", stripped)
    if m and _group_end(stripped, m.end() - 1) == len(stripped):
        return stripped  # one text command already: texmath reads it with the maths inside
    cuts, depth = [], 0
    for t in _TOKEN.finditer(cell):
        tok = t.group()
        if tok == "{":
            depth += 1
        elif tok == "}":
            depth -= 1
        elif tok == "$" and not depth:
            cuts.append(t.start())
    if len(cuts) % 2:
        cuts.pop()  # an unmatched $ stays text
    out, last = [], 0
    for i in range(0, len(cuts), 2):
        if cell[last:cuts[i]]:
            out.append("\\text{%s}" % cell[last:cuts[i]])
        out.append("{%s}" % cell[cuts[i] + 1:cuts[i + 1]])  # a group: $\alpha$ $b$ is no \alphab
        last = cuts[i + 1] + 1
    if cell[last:].strip() or not out:
        out.append("\\text{%s}" % (cell[last:] if out else cell.strip()))
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
    r"|[gex]?def|let|DeclarePairedDelimiterX|DeclarePairedDelimiter"
    r"|(?:New|Renew|Provide|Declare)DocumentCommand)%s" % _CS)


@dataclass
class Definition:
    name: str                               # \foo
    source: str                             # the definition as written; pandoc expands it
    uses: frozenset                         # control words in it
    paired: Optional[Tuple[str, str]] = None  # \DeclarePairedDelimiter: the left and right delimiter
    span: Tuple[int, int] = (0, 0)          # where it is in the text it was read from
    paired_x: Optional[Tuple[int, str]] = None  # \DeclarePairedDelimiterX: its arguments and body
    loop: bool = False                      # made by a \foreach alphabet loop (pandoc cannot run it)


def _control_words(tex: str) -> set:
    return set(re.findall(r"\\[a-zA-Z@]+", tex))


def _in_comment(text: str, pos: int) -> bool:
    line = text[text.rfind("\n", 0, pos) + 1:pos]
    return bool(re.search(r"(?<!\\)(?:\\\\)*%", line))


def _parse_definition(text: str, m) -> Optional[Definition]:
    cmd = m.group("cmd")
    pos = m.end()
    gap = _GAP.pattern  # spaces and comments, as TeX skips them
    if cmd == "let":
        lm = re.compile(gap + r"(\\[a-zA-Z@]+)" + gap + r"=?\s*(\\[a-zA-Z@]+|\\.|[^\s\\])").match(text, pos)
        if not lm:
            return None
        return Definition(lm.group(1), text[m.start():lm.end()], frozenset({lm.group(2)}), span=(m.start(), lm.end()))
    nm = re.compile(gap + r"(?:\{" + gap + r"(\\[a-zA-Z@]+)" + gap + r"\}|(\\[a-zA-Z@]+))").match(text, pos)
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
    elif cmd == "DeclarePairedDelimiterX":  # \DeclarePairedDelimiterX{\set}[1]{\{}{\}}{#1}
        count = 0
        cm = re.compile(_GAP.pattern + r"\[\s*(\d)\s*\]").match(text, pos)
        if cm:
            count, pos = int(cm.group(1)), cm.end()
        l0, l1 = _argument(text, pos)
        r0, r1 = _argument(text, l1)
        b0 = _GAP.match(text, r1).end()
        if text[b0:b0 + 1] != "{" or _group_end(text, b0) < 0 or r1 <= r0:
            return None
        end = _group_end(text, b0)
        left, right, body = _unbrace(text[l0:l1]), _unbrace(text[r0:r1]), text[b0 + 1:end - 1]
        return Definition(name, text[m.start():end], frozenset(_control_words(left + right + body) - {name}),
                          (left, right), (m.start(), end), paired_x=(count, body))
    elif cmd.endswith("DocumentCommand"):  # xparse: \NewDocumentCommand{\foo}{O{1} m m}{body}
        # pandoc's reader implements xparse (o, O{}, \IfNoValueTF, \RenewDocumentCommand): kept as written
        s0 = _GAP.match(text, pos).end()
        if text[s0:s0 + 1] != "{" or _group_end(text, s0) < 0:
            return None
        s1 = _group_end(text, s0)
        b0 = _GAP.match(text, s1).end()
        if text[b0:b0 + 1] != "{" or _group_end(text, b0) < 0:
            return None
        end = _group_end(text, b0)
        body = text[b0 + 1:end - 1]
        return Definition(name, text[m.start():end], frozenset(_control_words(body) - {name}), span=(m.start(), end))
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
    uses = frozenset(_control_words(source[len(m.group()):]) - {name})
    if cmd.startswith("DeclareMathOperator"):
        # pandoc expands \DeclareMathOperator* to \mathop{\mathrm{..}}, which loses the limits of the
        # starred form; texmath knows \operatorname(*) itself
        source = "\\newcommand{%s}{\\operatorname%s{%s}}" % (name, "*" if cmd.endswith("*") else "",
                                                             text[b0 + 1:end - 1])
    return Definition(name, source, uses, span=(m.start(), end))


_LOOP = re.compile(r"\\foreach\s*(\\[a-zA-Z]+)\s*in\s*\{([^{}]*)\}|\\@for\s*(\\[a-zA-Z]+)\s*:=\s*([^{}\\]*?)\s*\\do")
_LOOP_DEF = r"\\csname\s*([A-Za-z]*)\s*%s\s*\\endcsname"


def _letters(items: str) -> List[str]:
    """A pgffor / \\@for list of letters: "A,B,...,Z,a,b,...,z" -> A..Z a..z."""
    parts = [p.strip() for p in items.split(",") if p.strip()]
    out: List[str] = []
    for i, p in enumerate(parts):
        if p == "..." and out and i + 1 < len(parts) and len(parts[i + 1]) == 1 and parts[i + 1].isalpha() \
                and out[-1].isupper() == parts[i + 1].isupper():
            out += [chr(c) for c in range(ord(out[-1]) + 1, ord(parts[i + 1]))]
        elif len(p) == 1 and p.isalpha():
            out.append(p)
        else:
            return []  # not a list of letters
    return out


def _letter_loops(text: str) -> "Tuple[List[Definition], List[Tuple[int, int]]]":
    """The alphabet idiom: ``\\foreach \\x in {A,...,Z}{\\expandafter\\xdef\\csname cal\\x\\endcsname
    {\\noexpand\\mathcal{\\x}}}`` (also with \\@for and \\newcommand) defines \\calA ... \\calZ; pandoc
    cannot run the loop.  Returns the definitions and the spans of the loops."""
    defs: List[Definition] = []
    spans: List[Tuple[int, int]] = []
    for m in _LOOP.finditer(text):
        if _in_comment(text, m.start()):
            continue
        var, items = (m.group(1), m.group(2)) if m.group(1) else (m.group(3), m.group(4))
        b0 = _GAP.match(text, m.end()).end()
        if text[b0:b0 + 1] != "{" or _group_end(text, b0) < 0:
            continue
        end = _group_end(text, b0)
        letters = _letters(items)
        body = text[b0 + 1:end - 1]
        found = False
        for d in re.finditer(_LOOP_DEF % re.escape(var) + r"(?![a-zA-Z@])", body):
            g0 = _GAP.match(body, d.end()).end()
            if body[g0:g0 + 1] != "{" or _group_end(body, g0) < 0:
                continue
            template = body[g0 + 1:_group_end(body, g0) - 1].replace("\\noexpand", "")
            for letter in letters:
                value = re.sub(re.escape(var) + r"(?![a-zA-Z@])", lambda _m: letter, template)
                name = "\\" + d.group(1) + letter
                defs.append(Definition(name, "\\newcommand{%s}{%s}" % (name, value),
                                       frozenset(_control_words(value) - {name}), span=(m.start(), end), loop=True))
                found = True
        if found:
            spans.append((m.start(), end))
    return defs, spans


def parse_definitions(text: str, document: bool = False) -> "Tuple[List[Definition], List[str]]":
    """The macro definitions in LaTeX source (a preamble, a macro file, an equation), and the names
    of those that cannot be used (malformed, never closed).  `document`: a whole document or a style
    file, whose internal definitions (names or bodies with @) are left out."""
    defs, loops = _letter_loops(text) if "\\foreach" in text or "\\@for" in text else ([], [])
    skipped: List[str] = []
    pos = 0
    while True:
        m = _DEF_START.search(text, pos)
        if not m:
            if document:
                defs = [d for d in defs if "@" not in d.name and "@" not in d.source]
            defs.sort(key=lambda d: d.span[0])  # the loops' definitions where the loops are (stable)
            return defs, skipped
        if _in_comment(text, m.start()) or _in(loops, m.start()):
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


_USEPACKAGE = re.compile(r"\\(?:usepackage|RequirePackage)\s*(?:\[[^\]]*\])?\s*\{([^{}]*)\}")


def packages_in(text: str) -> set:
    """The packages that LaTeX source loads (\\usepackage{a,b})."""
    names: set = set()
    for m in _USEPACKAGE.finditer(text):
        if not _in_comment(text, m.start()):
            names.update(p.strip() for p in m.group(1).split(",") if p.strip())
    return names


_VERBATIM = re.compile(r"\\begin\s*\{(verbatim\*?|Verbatim\*?|lstlisting|minted|comment|BVerbatim|LVerbatim)\}"
                       r"[\s\S]*?\\end\s*\{\1\}|\\(?:verb|lstinline)\*?([^\sa-zA-Z{])[^\n]*?\2")


def without_verbatim(text: str) -> str:
    """LaTeX source with its verbatim parts blanked out (a LaTeX example in a tutorial is no
    definition), keeping every other character where it was (offsets into it hold for `text`)."""
    return _VERBATIM.sub(lambda m: re.sub(r"[^\n]", " ", m.group()), text)


_HOOKS = re.compile(r"\\(?:AtBeginDocument" + _CS + r"|AddToHook\s*\{\s*begindocument(?:/before|/end)?\s*\}"
                    r"(?:\s*\[[^\]]*\])?)")
# a hook whose definitions depend on something (\@ifpackageloaded{physics}{..}{}): not taken
_CONDITIONAL = re.compile(r"\\(?:@?if[a-zA-Z@]*|If[A-Za-z]*TF?|IfPackageLoaded[A-Za-z]*)(?![a-zA-Z@])")


def _hook_spans(text: str) -> List[Tuple[int, int]]:
    """The arguments of \\AtBeginDocument{..} and \\AddToHook{begindocument}[label]{..} without a condition
    in them: definitions there hold for the whole body, but pandoc's reader does not run the hook."""
    spans = []
    for m in _HOOKS.finditer(text):
        if _in_comment(text, m.start()):
            continue
        a0, a1 = _argument(text, m.end())
        if a1 > a0 and text[a0] == "{" and not _CONDITIONAL.search(text, a0, a1):
            spans.append((a0, a1))
    return spans


def pandoc_leftovers(text: str) -> str:
    """The definitions in LaTeX source that pandoc's reader does not apply (\\DeclarePairedDelimiter(X),
    the \\foreach alphabet loop, those in \\AtBeginDocument), with what they use and the source's
    \\usepackage lines: what a document or a pasted preamble adds to the macros of its equations,
    which pandoc already expanded otherwise.  Only those: re-applying all definitions would also
    apply one that holds in a group elsewhere."""
    text = without_verbatim(text)
    if not any(k in text for k in ("\\DeclarePairedDelimiter", "\\foreach", "\\@for", "usepackage", "RequirePackage",
                                   "\\AtBeginDocument", "\\AddToHook")):
        return ""
    defs, _ = parse_definitions(text, document=True)
    hooks = _hook_spans(text)
    wanted = [d for d in defs if d.paired or d.loop or _in(hooks, d.span[0])]
    # with the definitions those use (\DeclarePairedDelimiter{\pr}{\myL}{\myR} and \myL's \newcommand)
    names = {n for d in wanted for n in d.uses}
    while True:
        more = {n for d in defs if d.name in names for n in d.uses} - names
        if not more:
            break
        names |= more
    keep = [d.source for d in defs if d.paired or d.loop or d.name in names or _in(hooks, d.span[0])]
    keep += ["\\usepackage{%s}" % p for p in sorted(packages_in(text))]
    return "\n".join(keep)


class Macros:
    """Macro definitions from the user's files and text, applied to every LaTeX input.  A document's own
    definitions (its preamble, files it \\input s: :func:`documents.read_text_document`) come as
    `document`, after the user's files and text, so that they win; `labels` then maps its \\label s to
    the numbers LaTeX gave them (from the .aux file), for \\eqref."""

    def __init__(self, files: Sequence[str] = (), text: str = "", document: str = "",
                 labels: Optional[Dict[str, str]] = None):
        self.files = list(files)
        self.text = text
        self.document = document
        self.definitions: Dict[str, Definition] = {}
        self.order: List[Definition] = []  # every definition, in file order (\let takes a snapshot)
        self.skipped: List[str] = []
        self.errors: List[str] = []
        self.packages: set = set()
        self.labels: Dict[str, str] = dict(labels or {})
        for path in self.files:
            try:
                with open(path, "rb") as f:
                    data = f.read()
            except OSError as e:
                self.errors.append("cannot read %s: %s" % (path, e.strerror or e))
                continue
            self._add(data.decode("utf-8-sig", "replace"))
        self._add(text)
        if document:
            self._add(document, document=True)

    def _add(self, text: str, document: bool = False) -> None:
        defs, skipped = parse_definitions(text, document)
        for d in defs:
            self.definitions[d.name] = d  # a later definition wins, as in LaTeX with \renewcommand
            self.order.append(d)
        if not document:  # (a document's own skipped definitions are pandoc's business)
            self.skipped += skipped
        self.packages |= packages_in(text)

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


def load_macros(files: Sequence[str] = (), text: str = "", document: str = "",
                labels: Optional[Dict[str, str]] = None) -> Optional[Macros]:
    """A Macros object for these files and text (None for neither), re-read only when a file changes;
    `document` and `labels`: a document's own definitions and label numbers (see :class:`Macros`)."""
    files = [f for f in files if f]
    if not files and not text.strip() and not document.strip() and not labels:
        return None
    key = (file_stamps(files), text, document, tuple(sorted((labels or {}).items())))
    if key not in _MACRO_CACHE:
        if len(_MACRO_CACHE) > 16:
            _MACRO_CACHE.clear()
        _MACRO_CACHE[key] = Macros(files, text, document, labels)
    return _MACRO_CACHE[key]


def with_document(macros: Optional[Macros], document: str, labels: Optional[Dict[str, str]] = None) -> Optional[Macros]:
    """The user's macros plus a document's own definitions and label numbers (which win)."""
    if not document.strip() and not labels:
        return macros
    return load_macros(macros.files if macros is not None else (), macros.text if macros is not None else "",
                       document, labels)


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
        left, right, x = paired[m.group(1)]
        i = _GAP.match(tex, m.end()).end()
        size = ""
        if tex[i:i + 1] == "*":
            opening, closing, middle, i = "\\left", "\\right", "\\middle", i + 1
        else:
            j = _optional(tex, i, 1)
            if j > i:
                size = tex[i:j].strip()[1:-1].strip()
                i = j
            opening, closing = (size + "l", size + "r") if size else ("", "")
            middle = size
        count, body = x if x else (1, "#1")
        args = []
        for _ in range(count):
            a0, a1 = _argument(tex, i)
            if a1 <= a0:
                from .pandoc_engine import ConversionError
                raise ConversionError("%s needs an argument (it was declared with \\DeclarePairedDelimiter)"
                                      % m.group(1))
            args.append(_unbrace(tex[a0:a1]))
            i = a1
        inner = re.sub(r"#(\d)", lambda d: "{%s}" % args[int(d.group(1)) - 1] if 0 < int(d.group(1)) <= count
                       else d.group(), body)
        # \delimsize (in an X body: \delimsize\vert) takes the size of the pair, or \middle for *
        inner = re.sub(r"\\delimsize" + _CS + r"\s*", lambda _d: middle + " " if middle else "", inner)
        # one group: \abs{x}^2 puts the whole |x| under the exponent; the argument keeps its own scope
        new = "{%s%s {%s}%s%s}" % (opening, left, inner if x else "%s" % args[0], closing, right)
        tex = tex[:m.start()] + new + tex[i:]
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


_SIMPLE_DEF = re.compile(r"\s*\\(?:(?:re)?newcommand\*?|providecommand\*?|g?def)\s*\{?\s*(\\[a-zA-Z]+)\s*\}?"
                         r"\s*\{([\s\S]*)\}\s*")
# a body that needs TeX itself (a mode test, a look ahead, expansion control): pandoc expands those
_NOT_LITERAL = re.compile(r"\\(?:if[a-zA-Z]*|else|fi|xspace|expandafter|noexpand|csname|protect|futurelet|"
                          r"let|relax|ensuremath|@ifnextchar|ifx)(?![a-zA-Z@])")


_TAKES_ARGUMENT = frozenset(("\\" + n) for n in """
frac dfrac tfrac cfrac binom dbinom tbinom sqrt mathbb mathbf mathcal mathrm mathit mathsf mathtt mathfrak mathscr
mathnormal boldsymbol bm pmb operatorname text textrm textbf textit textsf texttt textup textnormal mbox emph hat
widehat tilde widetilde bar overline underline vec dot ddot dddot check breve acute grave mathring overbrace
underbrace overset underset stackrel xrightarrow xleftarrow left right middle big Big bigg Bigg bigl bigr Bigl
Bigr biggl biggr Biggl Biggr bigm Bigm not color textcolor boxed cancel bcancel xcancel phantom hphantom vphantom
smash mathop mathrel mathbin mathord mathopen mathclose mathpunct mathinner hspace mspace label tag ref eqref
overleftarrow overrightarrow underleftarrow underrightarrow sideset substack
""".split())


_NO_ARGUMENT: List[frozenset] = []


def _no_argument() -> frozenset:
    """Commands that take no argument (symbols, operators, Greek letters): diagnose's list without those
    that take one."""
    if not _NO_ARGUMENT:
        from .diagnose import _KNOWN
        _NO_ARGUMENT.append(frozenset(_KNOWN) - _TAKES_ARGUMENT)
    return _NO_ARGUMENT[0]


def _simple_expansion(tex: str, definitions: List[Definition]) -> Optional[str]:
    """`tex` with its macros expanded here, when every definition it needs is a macro without arguments
    (``\\newcommand{\\calR}{\\mathcal{R}}``: the \\foreach alphabet, \\R for \\mathbb{R}): as TeX replaces
    them, without a pandoc run per equation.  None when one is anything else (pandoc expands those)."""
    bodies: Dict[str, str] = {}
    for d in definitions:
        m = _SIMPLE_DEF.fullmatch(d.source)
        if not m or m.group(1) != d.name or "#" in m.group(2) or "@" in d.source or _NOT_LITERAL.search(m.group(2)):
            return None
        if d.source.lstrip().startswith("\\providecommand") and d.name in bodies:
            continue  # \providecommand defines only what is not defined yet
        bodies[d.name] = m.group(2)
    if not bodies:
        return tex
    for _ in range(20):
        out, last = [], 0
        for t in _TOKEN.finditer(tex):  # (whole control words; a comment is one token)
            if t.group() not in bodies:
                continue
            body = bodies[t.group()]
            before = tex[last:t.start()]
            # right after a command that takes an argument (\mathbb\RR, \frac\mass{V}, \left\bar) TeX hands
            # the whole macro over, which plain text in its place would not do: pandoc decides.  After one
            # that takes none (\in \calR, \sum\subk, \cdot\vx) the text goes in where the macro was.
            prev = re.search(r"(\\[a-zA-Z@]+)\s*$", before or (out[-1] if out else ""))
            if prev and (prev.group(1) in _TAKES_ARGUMENT or prev.group(1) not in _no_argument()):
                return None
            end = t.end()
            if t.group()[1:].isalpha():  # TeX skips the spaces after a control word (\text{\prefix Hz})
                end = re.compile(r"[ \t]*\n?[ \t]*").match(tex, end).end()
            if re.search(r"\\[a-zA-Z@]+$", body) and re.match(r"[a-zA-Z@]", tex[end:end + 1]):
                body += " "  # \bb R -> \mathbb R, not \mathbbR
            out += [before, body]
            last = end
        if not out:
            return tex
        tex = "".join(out) + tex[last:]
    return None  # a macro that keeps expanding into itself: pandoc decides


def _depth(tex: str, pos: int) -> int:
    """How many brace groups are open at `pos`."""
    depth = 0
    for m in _TOKEN.finditer(tex, 0, pos):
        depth += {"{": 1, "}": -1}.get(m.group(), 0)
    return depth


def _without(text: str, spans: List[Tuple[int, int]]) -> str:
    out, last = [], 0
    for a, b in sorted(spans):
        out.append(text[last:a])
        last = b
    return "".join(out) + text[last:]


# ---- standard LaTeX that texmath 0.13 rejects ------------------------------------------------------------
# amsmath / LaTeX commands written with what texmath has (renamed in place, no note)
_STANDARD = {"\\varlimsup": "\\overline{\\lim}", "\\varliminf": "\\underline{\\lim}",
             "\\varinjlim": "\\underset{\\rightarrow}{\\lim}", "\\varprojlim": "\\underset{\\leftarrow}{\\lim}",
             "\\injlim": "\\operatorname*{inj\\,lim}", "\\projlim": "\\operatorname*{proj\\,lim}",
             "\\idotsint": "\\int\\cdots\\int", "\\operatornamewithlimits": "\\operatorname*",
             "\\bigm": "\\big", "\\Bigm": "\\Big", "\\biggm": "\\bigg", "\\Biggm": "\\Bigg",
             "\\cfrac": "\\dfrac"}
# no-ops in maths: font sizes, page-break hints, \qedhere
_SIZES = ("\\tiny", "\\scriptsize", "\\footnotesize", "\\small", "\\normalsize", "\\large", "\\Large", "\\LARGE",
          "\\huge", "\\Huge")
_NO_OPS = _SIZES + ("\\qedhere", "\\nobreak", "\\allowdisplaybreaks", "\\hfill", "\\hfil", "\\hss")
_COUNTERS = {"\\addtocounter": 2, "\\setcounter": 2, "\\stepcounter": 1, "\\refstepcounter": 1,
             "\\@namedef": 2}  # (what a document's redefined \label leaves: \global\@namedef{..}{..})
_GROUPING = {"\\begingroup": "{", "\\endgroup": "}", "\\bgroup": "{", "\\egroup": "}"}
_RULES = re.compile(r"\\(?:toprule|midrule|bottomrule)(?:\s*\[[^\]]*\])?|\\cmidrule\s*(?:\([^)]*\))?\s*\{[^{}]*\}")
# text commands: texmath reads none of them inside another (\text{a \textbf{b}})
_TEXT_STYLES = ("\\text", "\\textrm", "\\textit", "\\textbf", "\\textsf", "\\texttt", "\\textup", "\\textsl",
                "\\textmd", "\\emph", "\\mbox")
# lengths texmath takes in \hspace: pt, em, cm, in; the rest in those
_LENGTHS = {"mm": (0.1, "cm"), "bp": (1.00375, "pt"), "pc": (12.0, "pt"), "dd": (1.07, "pt"), "cc": (12.84, "pt"),
            "sp": (1 / 65536, "pt"), "ex": (0.43, "em"), "mu": (1 / 18, "em"), "pt": (1, "pt"), "em": (1, "em"),
            "cm": (1, "cm"), "in": (1, "in")}
_DIMEN = r"(-?\s*(?:\d+\.?\d*|\.\d+))\s*(mm|cm|in|pt|bp|pc|dd|cc|sp|em|ex|mu)"
_GLUE = r"(?:\s*plus\s*-?[\d.]+\s*(?:fil+|[a-z]{2}))?(?:\s*minus\s*-?[\d.]+\s*(?:fil+|[a-z]{2}))?"
_SPACING = re.compile(r"\\hspace\*?\s*\{\s*%s\s*\}|\\(?:kern|hskip)\s*%s%s|\\m(?:kern|skip)\s*%s%s"
                      % (_DIMEN, _DIMEN, _GLUE, _DIMEN, _GLUE))
_FILL = re.compile(r"\\hspace\*?\s*\{\s*\\(?:h?fill|stretch\s*\{[^{}]*\})\s*\}")
_REFS = re.compile(r"\\(eqref|ref|cref|Cref|autoref|pageref|nameref|cite|citep|citet|Citep|Citet)\*?" + _CS)
_ACCENT_FONT = re.compile(
    r"(\\(?:hat|tilde|widehat|widetilde|bar|overline|underline|vec|dot|ddot|check|breve|acute|grave|mathring|"
    r"overrightarrow|overleftarrow))\s*(\\(?:mathcal|mathbf|mathbb|mathrm|mathit|mathsf|mathtt|mathfrak|mathscr|"
    r"boldsymbol|bm))" + _CS)
_ENV_POSITION = re.compile(r"(\\begin\s*\{(?:aligned|alignedat|gathered|array|split|subarray)\})\s*\[\s*[tbc]\s*\]")


# keys of a labels mapping (Macros.labels, a document's .aux numbers) that say how the document prints
# references and citations: "cleveref" / "hyperref", "authoryear" / "numeric" (see documents)
REF_STYLE, CITE_STYLE = "\x00ref-style", "\x00cite-style"
# cleveref's default names (singular, plural; capitalised for \Cref), hyperref's \autoref names
_CREF_NAMES = {"equation": ("eq.", "eqs."), "figure": ("fig.", "figs."), "subfigure": ("fig.", "figs."),
               "table": ("table", "tables"), "subtable": ("table", "tables"), "section": ("section", "sections"),
               "subsection": ("section", "sections"), "subsubsection": ("section", "sections"),
               "chapter": ("chapter", "chapters"), "appendix": ("appendix", "appendices"),
               "theorem": ("theorem", "theorems"), "lemma": ("lemma", "lemmas"),
               "corollary": ("corollary", "corollaries"), "proposition": ("proposition", "propositions"),
               "definition": ("definition", "definitions"), "remark": ("remark", "remarks"),
               "example": ("example", "examples"), "assumption": ("assumption", "assumptions"),
               "algorithm": ("algorithm", "algorithms"), "line": ("line", "lines"), "footnote": ("footnote", "footnotes"),
               "enumi": ("item", "items"), "item": ("item", "items"), "page": ("page", "pages")}
_AUTOREF_NAMES = {"equation": "Equation", "figure": "Figure", "table": "Table", "part": "Part", "appendix": "Appendix",
                  "chapter": "chapter", "section": "section", "subsection": "subsection",
                  "subsubsection": "subsubsection", "paragraph": "paragraph", "theorem": "Theorem",
                  "footnote": "footnote", "Item": "item", "item": "item", "page": "page"}


def _listed(items: List[str]) -> str:
    return items[0] if len(items) == 1 else ", ".join(items[:-1]) + " and " + items[-1]


def reference_text(command: str, keys: List[str], labels: Dict[str, str]) -> Optional[str]:
    """What \\eqref / \\ref / \\cref / \\Cref / \\autoref (pandoc: ref+label, ref+Label) of `keys` print,
    from the .aux numbers in `labels`; None when one of them has no number."""
    numbers = [labels.get(k) for k in keys]
    if not keys or any(n is None for n in numbers):
        return None
    if command == "eqref":
        return "(%s)" % ", ".join(numbers)
    if command not in ("cref", "Cref", "autoref", "ref+label", "ref+Label"):
        return ", ".join(numbers)
    types = [labels.get("type:" + k) for k in keys]
    style = ("hyperref" if command == "autoref" else "cleveref" if command in ("cref", "Cref")
             else labels.get(REF_STYLE, "hyperref"))
    kind = types[0] if len(set(types)) == 1 else None
    if style == "cleveref":  # one name for each run of one type: "fig. 1 and eqs. (2) and (3)"
        groups: List[Tuple[Optional[str], List[str]]] = []
        for n, t in zip(numbers, types):
            shown = "(%s)" % n if t == "equation" else n
            if groups and groups[-1][0] == t:
                groups[-1][1].append(shown)
            else:
                groups.append((t, [shown]))
        parts = []
        for t, shown in groups:
            names = _CREF_NAMES.get(t or "")
            name = (names[0] if len(shown) == 1 else names[1]) if names else ""
            if name and command in ("Cref", "ref+Label"):
                name = name[:1].upper() + name[1:]
            parts.append(("%s %s" % (name, _listed(shown))) if name else _listed(shown))
        return _listed(parts)
    name = _AUTOREF_NAMES.get(kind or "")
    return "%s %s" % (name, _listed(numbers)) if name else _listed(numbers)


def citation_text(command: str, cites: List[Tuple[str, str, str]], labels: Dict[str, str]) -> Tuple[str, bool]:
    """What \\cite, \\citep, \\citet, \\citeauthor ... (`command`, without the backslash) of `cites`
    ((key, prefix, suffix)) print, from the .aux (numbers; natbib's authors and years for an author-year
    document); and whether every key was found there."""
    command = command.rstrip("*").lower()
    found = all(("cite:" + k) in labels or ("citeauthor:" + k) in labels for k, _, _ in cites)
    year = {k: labels.get("citeyear:" + k) for k, _, _ in cites}
    author = {k: labels.get("citeauthor:" + k) for k, _, _ in cites}
    if command == "citeauthor":
        return "; ".join(author[k] or k for k, _, _ in cites), found
    if command in ("citeyear", "citeyearpar"):
        years = "; ".join(year[k] or k for k, _, _ in cites)
        return ("(%s)" % years if command == "citeyearpar" else years), found
    prefix, suffix = cites[0][1], cites[-1][2]
    if labels.get(CITE_STYLE) == "authoryear" and all(author[k] and year[k] for k, _, _ in cites):
        if command in ("citep", "citealp"):
            body = "; ".join("%s, %s" % (author[k], year[k]) for k, _, _ in cites)
            body = (prefix + " " if prefix else "") + body + (", " + suffix if suffix else "")
            return ("(%s)" % body if command == "citep" else body), found
        if command == "citealt":
            return "; ".join("%s %s" % (author[k], year[k]) for k, _, _ in cites), found
        # \citet, and \cite, which natbib prints as \citet in an author-year document
        parts = ["%s (%s)" % (author[k], year[k]) for k, _, _ in cites]
        if suffix:
            parts[-1] = parts[-1][:-1] + ", " + suffix + ")"
        return "; ".join(parts), found
    if command in ("citet", "citealt") and all(author[k] for k, _, _ in cites):  # numeric \citet: Smith [7]
        parts = ["%s [%s]" % (author[k], labels.get("cite:" + k, k)) for k, _, _ in cites]
        if suffix:
            parts[-1] = parts[-1][:-1] + ", " + suffix + "]"
        return ", ".join(parts), found
    body = ", ".join(labels.get("cite:" + k, k) for k, _, _ in cites)
    body = (prefix + " " if prefix else "") + body + (", " + suffix if suffix else "")
    return "[%s]" % body, found


def _refs(tex: str, labels: Dict[str, str]) -> str:
    """\\eqref{eq:x} -> (2.1) with the number from the document's .aux file (or the label itself),
    \\ref -> 2.1, \\cite{key} -> [key]: as text in maths, as letters inside text."""
    pos = 0
    while True:
        m = _REFS.search(tex, pos)
        if not m:
            return tex
        if _in_comment(tex, m.start()):
            pos = m.end()
            continue
        i = _optional(tex, m.end(), 2)
        a0, a1 = _argument(tex, i)
        if a1 <= a0 or tex[a0] != "{":
            pos = m.end()
            continue
        keys = [k.strip() for k in _unbrace(tex[a0:a1]).split(",") if k.strip()]
        if m.group(1).lower().startswith("cite"):
            shown = citation_text(m.group(1), [(k, "", "") for k in keys], labels)[0]
        elif m.group(1) in ("pageref", "nameref"):  # (the .aux holds a label's page and title too)
            field = {"pageref": "page:", "nameref": "title:"}[m.group(1)]
            shown = ", ".join(labels.get(field + k, k) for k in keys)
        else:
            shown = reference_text(m.group(1), keys, labels)
            if shown is None:  # no number: the label itself, as LaTeX's ?? would not say which
                shown = ", ".join(labels.get(k, k) for k in keys)
                shown = "(%s)" % shown if m.group(1) == "eqref" else shown
        in_text = _in(_text_spans(tex), m.start())
        new = shown if in_text else "\\text{%s}" % shown.replace("_", "\\_")
        tex = tex[:m.start()] + new + tex[a1:]
        pos = m.start() + len(new)


def _intertext(tex: str, layout: List[str]) -> str:
    """\\intertext{and so} in an align: a row of its own with the text (texmath has no \\intertext)."""
    pattern = re.compile(r"\\(?:short)?intertext" + _CS)
    while True:
        m = _find(tex, pattern)
        if not m:
            return tex
        a0, a1 = _argument(tex, m.end())
        layout.append("intertext")
        tex = tex[:m.start()] + "\\text{%s}\\\\" % _unbrace(tex[a0:a1]) + tex[a1:]


def _nested_text(tex: str) -> str:
    """\\text{a \\textbf{b} c} -> \\text{a }\\textbf{b}\\text{ c}; size switches inside text dropped."""
    names = "|".join(re.escape(n) for n in _TEXT_STYLES)
    outer = re.compile(r"(%s)%s" % (names, _CS))
    inner = re.compile(r"(%s|%s)%s" % (names, "|".join(re.escape(n) for n in _SIZES), _CS))
    pos = 0
    while True:
        m = outer.search(tex, pos)
        if not m:
            return tex
        a0, a1 = _argument(tex, m.end())
        if a1 <= a0 or tex[a0] != "{" or _in_comment(tex, m.start()):
            pos = m.end()
            continue
        body = tex[a0 + 1:a1 - 1]
        pieces, last, depth, changed, maths = [], 0, 0, False, False
        for t in _TOKEN.finditer(body):
            tok = t.group()
            if tok == "{":
                depth += 1
            elif tok == "}":
                depth -= 1
            elif tok in ("$", "\\(", "\\)") and not depth:
                maths = tok == "\\(" or (tok == "$" and not maths)  # maths in the text: not split there
            elif not depth and not maths and inner.fullmatch(tok):
                changed = True
                if tok in _SIZES:  # dropped (with the space after it)
                    pieces.append("%s{%s}" % (m.group(1), body[last:t.start()]))
                    last = t.end() + (1 if body[t.end():t.end() + 1] == " " else 0)
                    continue
                b0, b1 = _argument(body, t.end())
                pieces.append("%s{%s}" % (m.group(1), body[last:t.start()]))
                pieces.append("%s{%s}" % (tok, _unbrace(body[b0:b1])))
                last = b1
        if not changed:
            pos = m.end()
            continue
        pieces.append("%s{%s}" % (m.group(1), body[last:]))
        pieces = [p for p in pieces if not p.endswith("{}")]
        # one group, as the \text was one argument (x_\text{a \textbf{b}}: all of it in the subscript)
        new = pieces[0] if len(pieces) == 1 else "{%s}" % "".join(pieces)
        tex = tex[:m.start()] + new + tex[a1:]
        pos = m.start()  # the pieces may hold more nesting


def _spacing(tex: str) -> str:
    """\\hspace{10mm}, \\kern3pt, \\hskip 1em plus 1fil, \\mkern3mu: in units texmath reads (it takes pt,
    em, cm and in, and \\mspace); \\hspace{\\fill} and its kind are left out."""
    tex = _at_tokens(tex, _FILL, lambda _m: "{}")

    def length(value: str, unit: str) -> str:
        factor, base = _LENGTHS[unit]
        number = float(re.sub(r"\s+", "", value)) * factor  # (a line break between - and the number)
        return "%s%s" % (("%.3f" % number).rstrip("0").rstrip(".") or "0", base)

    def sub(m):
        if m.group(1):  # \hspace
            return "\\hspace{%s}" % length(m.group(1), m.group(2))
        if m.group(3):  # \kern / \hskip
            return "\\hspace{%s}" % length(m.group(3), m.group(4))
        if m.group(6) == "mu" and re.fullmatch(r"\d+", re.sub(r"\s+", "", m.group(5))):
            return "\\mspace{%smu}" % re.sub(r"\s+", "", m.group(5))  # \mkern 3mu (texmath: whole mu only)
        return "\\hspace{%s}" % length(m.group(5), m.group(6))
    return _at_tokens(tex, _SPACING, sub)


def _drop_arguments(tex: str, names: Dict[str, int]) -> str:
    """\\addtocounter{equation}{1} and its kind: left out with their arguments."""
    pattern = re.compile(r"(%s)%s" % ("|".join(re.escape(n) for n in names), _CS))
    while True:
        m = _find(tex, pattern)
        if not m:
            return tex
        end = m.end()
        for _ in range(names[m.group(1)]):
            end = _argument(tex, end)[1]
        tex = tex[:m.start()] + "{}" + tex[end:]


def _sideset(tex: str) -> str:
    """\\sideset{_a^b}{_c^d}\\sum -> {}_a^b\\sum_c^d (the scripts beside the operator); limits after it
    (\\sideset{}{'}\\sum_{i=1}^n) stay under and over the operator, the side scripts beside them."""
    pattern = re.compile(r"\\sideset" + _CS)
    while True:
        m = _find(tex, pattern)
        if not m:
            return tex
        l0, l1 = _argument(tex, m.end())
        r0, r1 = _argument(tex, l1)
        o0, o1 = _argument(tex, r1)
        left, right = _unbrace(tex[l0:l1]), _unbrace(tex[r0:r1])
        operator, end, limits = tex[o0:o1], o1, {}
        while True:  # _{..} and ^{..} right after the operator: its limits
            s = re.compile(_GAP.pattern + r"(\\limits" + _CS + r")?" + _GAP.pattern + r"([_^])").match(tex, end)
            if not s or s.group(2) in limits:
                break
            a0, a1 = _argument(tex, s.end())
            if a1 <= a0:
                break
            limits[s.group(2)] = _unbrace(tex[a0:a1])
            end = a1
        if limits:
            if "^" in limits:
                operator = "\\overset{%s}{%s}" % (limits["^"], operator)
            if "_" in limits:
                operator = "\\underset{%s}{%s}" % (limits["_"], operator)
        new = "{}%s{%s}%s" % (left, operator, "{}" + right if right and limits else right)
        tex = tex[:m.start()] + new + tex[end:]


def _accent_fonts(tex: str) -> str:
    """\\widetilde\\mathcal K -> \\widetilde{\\mathcal{K}}: LaTeX takes it, texmath wants the group."""
    pos = 0
    while True:
        m = _find(tex, _ACCENT_FONT, pos)
        if not m:
            return tex
        a0, a1 = _argument(tex, m.end())
        new = "%s{%s{%s}}" % (m.group(1), m.group(2), _unbrace(tex[a0:a1]))
        tex = tex[:m.start()] + new + tex[a1:]
        pos = m.start() + len(m.group(1))


def merge_notes(notes: List[str]) -> List[str]:
    """`notes` of several equations with the built-in-pack notes merged into one (the commands of each
    package listed once), where the first of them was."""
    prefix = _NOTES["packs"].split("%s")[0]
    by_package: Dict[str, List[str]] = {}
    out: List[str] = []
    for note in notes:
        if not note.startswith(prefix):
            if note not in out:
                out.append(note)
            continue
        if not by_package:
            out.append(prefix)  # where the merged note goes
        for part in note[len(prefix):].split("; "):
            m = re.fullmatch(r"(.*) \((\S+)\)", part)
            if m:
                names = by_package.setdefault(m.group(2), [])
                names += [n for n in m.group(1).split(", ") if n not in names]
    if by_package:
        merged = _NOTES["packs"] % "; ".join("%s (%s)" % (", ".join(names), package)
                                             for package, names in by_package.items())
        out[out.index(prefix)] = merged
    return out


def _at_tokens(tex: str, pattern: "re.Pattern", repl) -> str:
    """pattern.sub, but only where a token starts and outside comments: in ``a \\\\global`` the word
    after a row break \\\\ is no \\global."""
    out, last = [], 0
    for t in _TOKEN.finditer(tex):
        if t.start() < last or t.group().startswith("%"):
            continue
        m = pattern.match(tex, t.start())
        if m:
            out += [tex[last:m.start()], repl(m) if callable(repl) else m.expand(repl)]
            last = m.end()
    return "".join(out) + tex[last:]


def _standard(tex: str, layout: List[str]) -> str:
    """Standard LaTeX that texmath 0.13 rejects, written with what it has."""
    tex = _at_tokens(tex, _ENV_POSITION, r"\1")  # \begin{aligned}[t]: texmath would show the [t]
    tex = _at_tokens(tex, re.compile(r"\\cfrac\s*\[\s*[lcr]\s*\]"), lambda _m: "\\cfrac")
    tex = _at_tokens(tex, re.compile(r"\\textnormal" + _CS), lambda _m: "\\textrm")  # also inside text
    tex = _rename(tex, _STANDARD, [])
    tex = _intertext(tex, layout)
    tex = _nested_text(tex)
    tex = _commands(tex, {"\\mathnormal": 0}, "unwrap", [])
    tex = _at_tokens(tex, re.compile(r"\\displaybreak(?:\s*\[\s*\d\s*\])?"), lambda _m: "{}")
    tex = _at_tokens(tex, _RULES, lambda _m: "\\hline")  # booktabs rules in an array
    tex = _rename(tex, _GROUPING, [])
    tex = _at_tokens(tex, re.compile(r"\\global" + _CS + r"\s*"), lambda _m: "")
    tex = _commands(tex, _NO_OPS, "drop", [])
    tex = _drop_arguments(tex, _COUNTERS)
    tex = _spacing(tex)
    tex = _sideset(tex)
    return _accent_fonts(tex)


# ---- the whole rewrite -------------------------------------------------------------------------------
_NOTES = {
    "colour": "colours were dropped (texmath has no colour)",
    "resized": "%s: written as the plain-size version",
    "layout": "%s: layout-only commands were left out",
    "unexpanded": "the macros could not be expanded together with this equation; texmath was given the "
                  "definitions instead",
    "packs": "written as their packages define them (built in): %s",
    "intertext": "\\intertext: written as a row of text in the equation",
}


def for_texmath(tex: str, macros: Optional[Macros] = None, target: str = "mathml") -> "Tuple[str, List[str]]":
    """`tex` rewritten for texmath, its macros expanded; plus notes on what was approximated.
    `target`: the output format (bold letters are written as bold characters for MathML / OMML only,
    see :func:`_bold_upright`)."""
    notes: List[str] = []
    own, _ = parse_definitions(tex)
    for d in own:
        if _depth(tex, d.span[0]):
            from .pandoc_engine import ConversionError
            raise ConversionError("%s is defined inside a group: a definition that only holds there cannot be "
                                  "expanded here - define it at the start of the equation or in Macros..." % d.name)
    used = macros.needed(tex) if macros else []
    definitions = used + own
    # a name defined twice (two macro files, a file and the equation) has its last definition: a
    # \newcommand{\abs} after a \DeclarePairedDelimiter{\abs} (or before it) is not both
    last = {d.name: d for d in definitions}
    paired = {name: d.paired + (d.paired_x,) for name, d in last.items() if d.paired}
    # \let\myabs\abs of a paired delimiter: the same pair
    for d in definitions:
        aliased = next(iter(d.uses), None) if d.source.lstrip().startswith("\\let") else None
        if last[d.name] is d and aliased in paired:
            paired[d.name] = paired[aliased]
    # paired delimiters first, in the equation and in the definitions that use them: then pandoc expands
    # the macros their delimiters may be made of (\DeclarePairedDelimiter{\abs}{\ld}{\rangle})
    plain: List[str] = []
    seen: set = set()
    for d in definitions:
        if d.paired or last[d.name].paired or d.name in paired:
            continue
        source = d.source
        if d.name in seen:  # pandoc keeps a name's first \newcommand: a later one must win, as \renewcommand
            source = re.sub(r"^(\s*)\\(?:newcommand|DeclareRobustCommand)(?![a-zA-Z@])", r"\1\\renewcommand", source)
        seen.add(d.name)
        try:
            plain.append(_paired(source, paired))
        except Exception:  # noqa: BLE001 - \newcommand{\foo}{\abs}: the pair is used where \foo is
            plain.append(source)
    out = _paired(_without(tex, [d.span for d in own]) if own else tex, paired)
    simple = _simple_expansion(out, [d for d in definitions if not d.paired and d.name not in paired])
    if simple is not None:  # 0-argument \newcommand only (the \foreach alphabet): no pandoc run per equation
        out, plain = _paired(simple, paired).strip(), []
    if plain:
        expanded = _expand(out, plain)
        if expanded is None:
            # a style file's machinery (\let to internals, \expandafter, \csname...) can stop pandoc:
            # once more with the plain definitions alone
            simple = [s for s in plain if not re.search(r"\\(?:let|expandafter|csname|if[a-z]*|@)" + _CS + "|@", s)]
            expanded = _expand(out, simple) if simple and len(simple) < len(plain) else None
        if expanded is None:
            notes.append(_NOTES["unexpanded"])
            out = "\n".join(plain) + "\n" + out
        else:
            out = _paired(expanded, paired)

    hits: List[str] = []
    resized: List[str] = []
    layout: List[str] = []
    # (a Macros with no definitions is falsy: it may still hold packages and labels)
    out = _refs(out, macros.labels if macros is not None else {})
    from . import packs
    defined = set(macros.definitions if macros is not None else ()) | {d.name for d in own}
    packages = (macros.packages if macros is not None else set()) | packages_in(tex)
    pack_hits: List[str] = []
    out = packs.apply(out, defined, packages, pack_hits)
    rows: List[str] = []
    out = _standard(out, rows)
    if rows:
        notes.append(_NOTES["intertext"])
    out = _environments(out, hits)
    out = _font_switches(out)
    out = _rename(out, _RENAMES, hits)
    out = _rename(out, _SYMBOLS, [])
    out = _sized_norms(out)
    if target in ("mathml", "omml"):
        out = _rename(out, _OFFICE_SYMBOLS, [])
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
    if pack_hits:
        by_package: Dict[str, List[str]] = {}
        for hit in pack_hits:
            package, name = hit.split(": ", 1)
            if name not in by_package.setdefault(package, []):
                by_package[package].append(name)
        notes.append(_NOTES["packs"] % "; ".join("%s (%s)" % (", ".join(names), package)
                                                for package, names in by_package.items()))
    return out, notes
