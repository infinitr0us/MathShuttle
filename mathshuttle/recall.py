"""The LaTeX an equation was copied from, given back when it returns from Word / PowerPoint unedited.

Copy for Word/PowerPoint (the window, the hotkey, `mathshuttle copy`) remembers each equation it copied:
a fingerprint of what it shows, and the LaTeX as the user wrote it (macros, \\label, spacing).  When
Paste from Word/PowerPoint reads an equation with the same fingerprint, that LaTeX comes back instead of
the LaTeX rebuilt from Office's MathML (which has lost the macros and labels).

The fingerprint is what the equation shows (:func:`convert._symbols`: symbols in order, scripts,
fractions and roots with their bounds, no spacing, fonts folded) plus each letter's font class, so that
what Word itself changes does not count (a bold letter coming back bold italic, parentheses becoming a
stretchy pair, spaces) and what a user changes does (a symbol, a script, a letter made bold or upright).
The memory is a small JSON file next to the settings (the newest 300 equations); set
MATHSHUTTLE_RECALL_PATH to use another file, or to an empty value to turn it off.
"""
from __future__ import annotations

import json
import os
import re
import sys
import threading
import time
import unicodedata
from typing import List, Optional, Tuple

_LIMIT = 300
_LOCK = threading.Lock()


def store_path() -> Optional[str]:
    """Where the memory is kept (None: off)."""
    if "MATHSHUTTLE_RECALL_PATH" in os.environ:
        return os.environ["MATHSHUTTLE_RECALL_PATH"] or None
    base = (os.path.join(os.path.expanduser("~"), "Library", "Application Support") if sys.platform == "darwin"
            else os.environ.get("APPDATA", os.path.expanduser("~")))
    return os.path.join(base, "MathShuttle", "copied.json")


# a mathvariant, or a word of a mathematical alphanumeric's name -> font class
_VARIANTS = {"normal": "u", "italic": "i", "bold": "b", "bold-italic": "b", "double-struck": "d", "script": "s",
             "bold-script": "s", "fraktur": "f", "bold-fraktur": "f", "sans-serif": "ss", "bold-sans-serif": "ss",
             "sans-serif-italic": "ss", "sans-serif-bold-italic": "ss", "monospace": "m"}
_NAME_WORDS = (("DOUBLE-STRUCK", "d"), ("FRAKTUR", "f"), ("SCRIPT", "s"), ("SANS-SERIF", "ss"),
               ("MONOSPACE", "m"), ("BOLD", "b"), ("ITALIC", "i"))


def _font(ch: str, variant: Optional[str]) -> str:
    """A letter's font class: b(old) s(cript) f(raktur) d(ouble-struck) ss (sans) m(ono), u(pright) or
    i(talic).  Bold and bold italic are one class: Word makes every bold letter bold italic."""
    name = unicodedata.name(ch, "")
    if name.startswith("MATHEMATICAL"):  # the character says it (𝐱, 𝑥, ℝ...)
        return next((cls for word, cls in _NAME_WORDS if word in name), "u")
    if ch in "ℂℍℕℙℚℝℤ":
        return "d"
    if ch in "ℊℋℐℒℛℬℰℱℳℯℴ":
        return "s"
    if ch in "ℌℑℜℨℭ":
        return "f"
    return _VARIANTS.get((variant or "italic").lower(), "u")


def fingerprint(mathml: str) -> Optional[str]:
    """What a single <math> shows, as a string to compare (None if it is not one readable equation)."""
    from .convert import _symbols
    from .pandoc_engine import parse_mathml
    try:
        maths = parse_mathml(mathml)
    except Exception:  # noqa: BLE001 - not MathML we can read: nothing to recall
        return None
    if len(maths) != 1:
        return None
    math = maths[0]
    fonts: List[str] = []
    for el in math.iter():
        tag = el.tag.rsplit("}", 1)[-1] if isinstance(el.tag, str) else ""
        if tag != "mi" or not el.text:
            continue
        text = el.text.strip()
        variant = el.get("mathvariant")
        if len(text) > 1 and variant is None:
            variant = "normal"  # a multi-letter <mi> is upright (a name such as sin, softmax)
        fonts += [_font(ch, variant) for ch in text if ch.isalpha()]
    shown = unicodedata.normalize("NFKC", _symbols(math))
    shown = re.sub(r"\s+", "", shown)
    return shown + "|" + "".join(fonts)


def _load(path: str) -> list:
    try:
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, list) else []
    except (OSError, ValueError):
        return []


def remember(mathml: str, tex: str, display: bool) -> None:
    """Note that `mathml` was copied for Office from the LaTeX `tex` (an equation's body as written)."""
    path = store_path()
    key = fingerprint(mathml) if path else None
    if not key:
        return
    with _LOCK:
        entries = [e for e in _load(path) if isinstance(e, dict) and e.get("fingerprint") != key]
        entries.append({"fingerprint": key, "tex": tex, "display": bool(display), "time": int(time.time())})
        entries = entries[-_LIMIT:]
        try:
            os.makedirs(os.path.dirname(path), exist_ok=True)
            part = path + ".part"
            with open(part, "w", encoding="utf-8") as f:
                json.dump(entries, f, ensure_ascii=False)
            os.replace(part, path)
        except OSError:
            pass  # a memory that cannot be written is no reason to fail a copy


def recall(mathml: str) -> Optional[Tuple[str, bool]]:
    """(LaTeX, display) the equation `mathml` was copied from, if it comes back unedited; else None."""
    path = store_path()
    key = fingerprint(mathml) if path else None
    if not key:
        return None
    with _LOCK:
        for entry in reversed(_load(path)):
            if isinstance(entry, dict) and entry.get("fingerprint") == key and isinstance(entry.get("tex"), str):
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
