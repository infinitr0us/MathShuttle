"""The LaTeX an equation was copied from, given back when it returns from Word / PowerPoint unedited.

Copy for Word/PowerPoint (the window, the hotkey, the document window, `mathshuttle copy`) remembers each
equation it copied: a fingerprint of what it shows, and the LaTeX as the user wrote it (macros, \\label,
spacing).  When Paste from Word/PowerPoint reads an equation with the same fingerprint, that LaTeX comes
back instead of the LaTeX rebuilt from Office's MathML (which has lost the macros and labels).

The fingerprint (:func:`fingerprint`) is what the equation shows: its characters in order, every script,
limit, fraction (a binomial apart), root, accent, matrix row and cell, and each letter's font.  What Word
itself changes when it pastes and copies an equation does not count (checked with Word 16.0.20527 on 99
equations): bold letters coming back bold italic, sans-serif coming back sans-serif italic, ℓ as 𝓁,
accents written with other characters (a combining hat as ^), brackets made a stretchy pair, spaces, how
characters are split into tokens (1.5 as 1 . 5) and which kind of token holds them.  Anything a user
changes does count: a symbol, an accent, a script, a matrix's shape, a letter made bold or upright.

Two copies that show the same but carry different \\label or \\tag are not told apart: such an equation
is then converted from Office's MathML as usual.

The memory is a small JSON file next to the settings (the newest 300 equations); set
MATHSHUTTLE_RECALL_PATH to use another file, or to an empty value to turn it off.
"""
from __future__ import annotations

import contextlib
import json
import os
import re
import sys
import tempfile
import threading
import time
import unicodedata
from typing import Iterator, List, Optional, Tuple

_LIMIT = 300
_LOCK = threading.Lock()
_VERSION = "2"


def store_path() -> Optional[str]:
    """Where the memory is kept (None: off)."""
    if "MATHSHUTTLE_RECALL_PATH" in os.environ:
        path = os.environ["MATHSHUTTLE_RECALL_PATH"]
        return os.path.abspath(path) if path else None
    base = (os.path.join(os.path.expanduser("~"), "Library", "Application Support") if sys.platform == "darwin"
            else os.environ.get("APPDATA", os.path.expanduser("~")))
    return os.path.join(base, "MathShuttle", "copied.json")


# ---- the fingerprint --------------------------------------------------------------------------------------
# a mathvariant -> font class: b(old) s(cript) f(raktur) d(ouble-struck) ss (sans) m(ono) u(pright) i(talic).
# Bold and bold italic are one class, as are the sans-serif ones: Word makes them bold italic, sans italic.
_VARIANTS = {"normal": "u", "italic": "i", "bold": "b", "bold-italic": "b", "double-struck": "d", "script": "s",
             "bold-script": "s", "fraktur": "f", "bold-fraktur": "f", "sans-serif": "ss", "bold-sans-serif": "ss",
             "sans-serif-italic": "ss", "sans-serif-bold-italic": "ss", "monospace": "m"}
_NAME_WORDS = (("DOUBLE-STRUCK", "d"), ("FRAKTUR", "f"), ("SCRIPT", "s"), ("SANS-SERIF", "ss"),
               ("MONOSPACE", "m"), ("BOLD", "b"), ("ITALIC", "i"))
_LETTERLIKE = {}
for _chars, _cls in (("ℂℍℕℙℚℝℤⅅⅆⅇⅈⅉ", "d"), ("ℊℋℐℒℛℬℰℱℳℯℴℓ", "s"), ("ℌℑℜℨℭ", "f")):
    for _ch in _chars:
        _LETTERLIKE[_ch] = (unicodedata.normalize("NFKC", _ch) if _ch != "ℓ" else "l", _cls)
# characters with nothing to see: spaces of every width, invisible operators, zero-width marks
_INVISIBLE = frozenset("\u00a0\u2000\u2001\u2002\u2003\u2004\u2005\u2006\u2007\u2008\u2009\u200a\u200b\u200c"
                       "\u200d\u205f\u2060\u2061\u2062\u2063\u2064\u3000\ufeff")
# an accent over or under a base, as Word or texmath write it -> one name
_ACCENTS = {}
for _chars, _name in (("\u0302^ˆ\u02c6", "hat"), ("\u0303~˜\u02dc\u223c", "tilde"),
                      ("\u0304\u0305¯‾-\u2212\u203e\u00af\u2015\u2014\u2500", "bar"),
                      ("_\u0332\u2017\u0331", "underbar"), ("\u20d7→\u2192", "vec"), ("\u0307.˙\u02d9", "dot"),
                      ("\u0308¨", "ddot"), ("\u20db", "dddot"), ("\u030c\u02c7ˇ", "check"), ("\u0306˘\u02d8", "breve"),
                      ("\u0301´\u00b4\u02ca", "acute"), ("\u0300`\u02cb", "grave"), ("\u030a˚\u02da", "ring")):
    for _ch in _chars:
        _ACCENTS[_ch] = _name
_FIXED = {"msub": 2, "msup": 2, "msubsup": 3, "munder": 2, "mover": 2, "munderover": 3, "mfrac": 2, "mroot": 2}
_ONE_ARGUMENT = ("msqrt", "menclose", "mphantom", "mtd")  # their content as one argument, however grouped
_OPENING, _CLOSING = frozenset("([{⟨⌊⌈|‖⟦⟪〈"), frozenset(")]}⟩⌋⌉|‖⟧⟫〉")
# characters Word writes for others it was given: one of each pair stands for both
_SAME = {"*": "∗", "-": "−", "'": "′"}


def _tag(el) -> str:
    return el.tag.rsplit("}", 1)[-1] if isinstance(el.tag, str) else ""


def _char(ch: str, tag: str, variant: Optional[str], word: bool) -> str:
    """One character as it shows: a letter with its font class, any other symbol as itself."""
    ch = _SAME.get(ch, ch)
    if ch in _LETTERLIKE:
        base, cls = _LETTERLIKE[ch]
        return base + cls
    name = unicodedata.name(ch, "")
    if name.startswith("MATHEMATICAL"):  # 𝐱 𝑥 𝔤 𝟐 𝟙 𝛻: the character says its font
        # its letter (𝜙 is ϕ, \phi, and 𝜑 is φ, \varphi: Word writes each in its italic form; NFKC would
        # make both φ)
        decomposition = unicodedata.decomposition(ch)
        base = chr(int(decomposition.split()[1], 16)) if decomposition.startswith("<font>") else \
            unicodedata.normalize("NFKC", ch)
        base = _SAME.get(base, base)
        cls = next((c for w, c in _NAME_WORDS if w in name), "u")
        # (a letter or digit keeps its font, 𝟙 is not 1; a symbol only bold: Word writes ∇ as 𝛻)
        return base + (cls if base.isalnum() or cls == "b" else "")
    if not ch.isalpha():
        return ch
    if tag == "mi":
        return ch + _VARIANTS.get((variant or ("normal" if word else "italic")).lower(), "u")
    return ch + _VARIANTS.get((variant or "normal").lower(), "u")  # mtext, mo, mn: upright unless said


def _accent(el) -> Optional[str]:
    """The accent's name if `el` (the second child of an mover / munder) is a single accent character."""
    if _tag(el) not in ("mo", "mi", "mtext"):
        return None
    text = "".join(ch for ch in (el.text or "") if not ch.isspace() and ch not in _INVISIBLE)
    return _ACCENTS.get(text) if len(text) == 1 else None


def _fence(el, chars) -> bool:
    return _tag(el) == "mo" and (el.text or "").strip() in chars and len((el.text or "").strip()) == 1


def _fenced(base) -> Optional[list]:
    """The children of `base` if it is a bracketed group, ( … ) or ‖ … ‖ (Word's form of a script on one)."""
    while _tag(base) == "mrow":
        kids = [k for k in base if isinstance(k.tag, str)]
        if len(kids) != 1:
            break
        base = kids[0]
    if _tag(base) != "mrow":
        return None
    kids = [k for k in base if isinstance(k.tag, str)]
    if len(kids) >= 2 and _fence(kids[0], _OPENING) and _fence(kids[-1], _CLOSING):
        return kids
    return None


def fingerprint(mathml: str) -> Optional[str]:
    """What a single <math> shows, as a string to compare (None if it is not one readable equation)."""
    from .pandoc_engine import parse_mathml
    try:
        maths = parse_mathml(mathml)
    except Exception:  # noqa: BLE001 - not MathML we can read: nothing to recall
        return None
    if len(maths) != 1:
        return None
    out: List[str] = [_VERSION, "|"]

    def walk(el, variant: Optional[str]) -> None:
        tag = _tag(el)
        variant = el.get("mathvariant") or variant
        if tag in ("annotation", "annotation-xml", "mspace", "maligngroup", "malignmark", "mprescripts", "none"):
            out.append({"mprescripts": "<pre>", "none": "<none>"}.get(tag, ""))
            return
        kids = [k for k in el if isinstance(k.tag, str)]
        if tag == "semantics":
            if kids:
                walk(kids[0], variant)
            return
        if tag in ("mi", "mo", "mn", "mtext", "ms"):
            text = "".join(ch for ch in (el.text or "") if not ch.isspace() and ch not in _INVISIBLE)
            word = len([ch for ch in text if ch.isalpha()]) > 1
            out.extend(_char(ch, tag, variant, word) for ch in text)
            return
        if tag == "mfenced":
            seps = [ch for ch in el.get("separators", ",") if not ch.isspace()]
            out.append(el.get("open", "("))
            for k, child in enumerate(kids):
                if k and seps:
                    out.append(seps[min(k - 1, len(seps) - 1)])
                walk(child, variant)
            out.append(el.get("close", ")"))
            return
        if tag in ("mover", "munder") and len(kids) == 2 and _accent(kids[1]):
            out.append("<%s:%s{" % (tag, _accent(kids[1])))
            walk(kids[0], variant)
            out.append("}>")
            return
        if tag in ("msub", "msup", "msubsup") and kids:
            fenced = _fenced(kids[0])
            if fenced:  # a script on a bracketed group, which texmath puts on the closing bracket alone
                for child in fenced[:-1]:
                    walk(child, variant)
                kids = [fenced[-1]] + kids[1:]
        if tag in _FIXED or tag in _ONE_ARGUMENT or tag in ("mmultiscripts", "mtable", "mtr", "mlabeledtr"):
            mark = tag
            if tag == "mfrac" and (el.get("linethickness") or "").strip() in ("0", "0pt", "0em", "0px", "0ex"):
                mark = "binom"  # no fraction bar
            if tag == "menclose":
                mark += ":" + " ".join(sorted((el.get("notation") or "longdiv").split()))
            out.append("<%s" % mark)
            for child in ([el] if tag in _ONE_ARGUMENT else kids):
                out.append("{")
                if child is el:
                    for k in kids:
                        walk(k, variant)
                else:
                    walk(child, variant)
                out.append("}")
            out.append(">")
            return
        for child in kids:  # mrow, mstyle, mpadded, math...: their content in order
            walk(child, variant)

    walk(maths[0], None)
    return "".join(out)


# ---- the memory ---------------------------------------------------------------------------------------------
_MARKS = re.compile(r"\\(?:label|tag\*?)\s*\{[^{}]*\}")


def _marks(tex: str) -> List[str]:
    """The \\label s and \\tag s of an equation's LaTeX (what two same-looking equations differ in)."""
    return [" ".join(m.split()) for m in _MARKS.findall(tex)]


def _load(path: str) -> list:
    """The entries; [] for a missing or unreadable file (OSError other than a missing file is raised)."""
    try:
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
    except FileNotFoundError:
        return []
    except ValueError:
        return []
    return [e for e in data if isinstance(e, dict)] if isinstance(data, list) else []


@contextlib.contextmanager
def _locked(path: str) -> Iterator[bool]:
    """Holds `path`.lock (made exclusively) while another process could write the memory too: yields
    whether it got the lock within about 2 s (a lock left by a crashed process is taken after 10 s)."""
    lock = path + ".lock"
    got = False
    for _ in range(100):
        try:
            os.close(os.open(lock, os.O_CREAT | os.O_EXCL | os.O_WRONLY))
            got = True
            break
        except FileExistsError:
            try:
                if time.time() - os.path.getmtime(lock) > 10:
                    os.remove(lock)
                    continue
            except OSError:
                pass
            time.sleep(0.02)
        except OSError:
            break
    try:
        yield got
    finally:
        if got:
            try:
                os.remove(lock)
            except OSError:
                pass


def _save(path: str, entries: list) -> None:
    folder = os.path.dirname(path)
    handle, part = tempfile.mkstemp(dir=folder, prefix=".copied.", suffix=".part")
    try:
        with os.fdopen(handle, "w", encoding="utf-8") as f:
            json.dump(entries, f, ensure_ascii=False)
        for attempt in range(5):
            try:
                os.replace(part, path)
                return
            except PermissionError:  # a reader has it open for a moment (Windows)
                if attempt == 4:
                    raise
                time.sleep(0.05)
    finally:
        try:
            os.remove(part)
        except OSError:
            pass


def remember(mathml: str, tex: str, display: bool) -> None:
    """Note that `mathml` was copied for Office from the LaTeX `tex` (an equation's body as written)."""
    path = store_path()
    key = fingerprint(mathml) if path else None
    if not key:
        return
    with _LOCK:
        try:
            os.makedirs(os.path.dirname(path), exist_ok=True)
            with _locked(path) as got:
                if not got:
                    return  # another process is writing: this copy is not remembered, nothing is lost
                entries = _load(path)
                old = next((e for e in entries if e.get("fingerprint") == key), None)
                entries = [e for e in entries if e.get("fingerprint") != key]
                if old is not None and (old.get("ambiguous") or _marks(old.get("tex") or "") != _marks(tex)):
                    # the same equation with another \label or \tag: which one comes back is not known
                    entries.append({"fingerprint": key, "ambiguous": True, "time": int(time.time())})
                else:
                    entries.append({"fingerprint": key, "tex": tex, "display": bool(display), "time": int(time.time())})
                _save(path, entries[-_LIMIT:])
        except OSError:
            pass  # a memory that cannot be read or written is no reason to fail a copy


def recall(mathml: str) -> Optional[Tuple[str, bool]]:
    """(LaTeX, display) the equation `mathml` was copied from, if it comes back unedited; else None."""
    path = store_path()
    key = fingerprint(mathml) if path else None
    if not key:
        return None
    with _LOCK:
        try:
            entries = _load(path)
        except OSError:
            return None
    for entry in reversed(entries):
        if entry.get("fingerprint") == key:
            if entry.get("ambiguous") or not isinstance(entry.get("tex"), str):
                return None
            return entry["tex"], bool(entry.get("display", True))
    return None


NOTE = ("The equation came back from Office unchanged: this is the LaTeX it was copied from (your macros, "
        "labels and spacing).")


def latex_for(mathml: str, wrapper: str = "auto", mode: str = "auto") -> Optional[str]:
    """The LaTeX `mathml` was copied from, in `wrapper` (`mode`: auto keeps its own display or inline), or
    None when it was not copied from here or has been edited."""
    found = recall(mathml)
    if found is None:
        return None
    from . import latex_wrap
    tex, display = found
    display = display if mode == "auto" else mode == "display"
    return latex_wrap.wrap(tex, wrapper, display)
