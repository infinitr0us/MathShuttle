"""Win32 clipboard access via ctypes (no third-party dependencies); used through :mod:`.clipboard`.

Word / PowerPoint / OneNote exchange equations through the registered clipboard
format ``"MathML"`` (and ``"MathML Presentation"``): data placed there is pasted as
a native, editable equation, and copying an equation from Office puts its MathML
there.
"""
from __future__ import annotations

import ctypes
import ctypes.wintypes as wt
import re
import time
from typing import Dict, List, Optional, Tuple

# shared by the platform backends: the exceptions the app catches, the decoding of registered formats
from .clipboard import _XML_DECL, ClipboardChanged, ClipboardError, _decode_text

_user32 = ctypes.windll.user32
_kernel32 = ctypes.windll.kernel32

CF_UNICODETEXT = 13
_GMEM_MOVEABLE = 0x0002

# --- prototypes (explicit types matter on 64-bit) ------------------------------
_user32.OpenClipboard.argtypes = [wt.HWND]
_user32.OpenClipboard.restype = wt.BOOL
_user32.CloseClipboard.restype = wt.BOOL
_user32.EmptyClipboard.restype = wt.BOOL
_user32.GetClipboardData.argtypes = [wt.UINT]
_user32.GetClipboardData.restype = wt.HANDLE
_user32.SetClipboardData.argtypes = [wt.UINT, wt.HANDLE]
_user32.SetClipboardData.restype = wt.HANDLE
_user32.RegisterClipboardFormatW.argtypes = [wt.LPCWSTR]
_user32.RegisterClipboardFormatW.restype = wt.UINT
_user32.EnumClipboardFormats.argtypes = [wt.UINT]
_user32.EnumClipboardFormats.restype = wt.UINT
_user32.GetClipboardFormatNameW.argtypes = [wt.UINT, wt.LPWSTR, ctypes.c_int]
_user32.GetClipboardFormatNameW.restype = ctypes.c_int
_user32.IsClipboardFormatAvailable.argtypes = [wt.UINT]
_user32.IsClipboardFormatAvailable.restype = wt.BOOL
_kernel32.GlobalAlloc.argtypes = [wt.UINT, ctypes.c_size_t]
_kernel32.GlobalAlloc.restype = wt.HGLOBAL
_kernel32.GlobalLock.argtypes = [wt.HGLOBAL]
_kernel32.GlobalLock.restype = wt.LPVOID
_kernel32.GlobalUnlock.argtypes = [wt.HGLOBAL]
_kernel32.GlobalUnlock.restype = wt.BOOL
_kernel32.GlobalSize.argtypes = [wt.HGLOBAL]
_kernel32.GlobalSize.restype = ctypes.c_size_t
_kernel32.GlobalFree.argtypes = [wt.HGLOBAL]
_kernel32.GlobalFree.restype = wt.HGLOBAL
_user32.GetClipboardSequenceNumber.restype = wt.DWORD


def sequence_number() -> int:
    """Changes whenever the clipboard's contents change (GetClipboardSequenceNumber)."""
    return _user32.GetClipboardSequenceNumber()


def register_format(name: str) -> int:
    fmt = _user32.RegisterClipboardFormatW(name)
    if not fmt:
        raise ClipboardError(f"RegisterClipboardFormat({name!r}) failed")
    return fmt


CF_MATHML = register_format("MathML")
CF_MATHML_PRESENTATION = register_format("MathML Presentation")


class _Open:
    """Context manager that opens the clipboard, retrying while another app holds it."""

    def __enter__(self):
        for attempt in range(20):
            if _user32.OpenClipboard(None):
                return self
            time.sleep(0.05)
        raise ClipboardError("Could not open the clipboard (another application is holding it)")

    def __exit__(self, *exc):
        _user32.CloseClipboard()
        return False


def set_data(entries: Dict[int, bytes], expect_sequence: Optional[int] = None) -> None:
    """Replace the clipboard contents with the given {format_id: raw bytes} entries.  With
    `expect_sequence` (a :func:`sequence_number` taken when the clipboard was read), raise
    ClipboardChanged instead if it changed since - checked while the clipboard is held open."""
    with _Open():
        if expect_sequence is not None and sequence_number() != expect_sequence:
            raise ClipboardChanged("The clipboard changed meanwhile (something else was copied): left as it is")
        if not _user32.EmptyClipboard():
            raise ClipboardError("EmptyClipboard failed")
        for fmt, data in entries.items():
            handle = _kernel32.GlobalAlloc(_GMEM_MOVEABLE, len(data))
            if not handle:
                raise ClipboardError("GlobalAlloc failed")
            ptr = _kernel32.GlobalLock(handle)
            if not ptr:
                _kernel32.GlobalFree(handle)
                raise ClipboardError("GlobalLock failed")
            ctypes.memmove(ptr, data, len(data))
            _kernel32.GlobalUnlock(handle)
            if not _user32.SetClipboardData(fmt, handle):
                _kernel32.GlobalFree(handle)
                raise ClipboardError(f"SetClipboardData(format {fmt}) failed")
            # ownership of `handle` passed to the system


def get_data(fmt: int) -> Optional[bytes]:
    """Raw bytes stored under `fmt`, or None if that format is not on the clipboard."""
    with _Open():
        handle = _user32.GetClipboardData(fmt)
        if not handle:
            return None
        ptr = _kernel32.GlobalLock(handle)
        if not ptr:
            raise ClipboardError("GlobalLock failed")
        try:
            size = _kernel32.GlobalSize(handle)
            return ctypes.string_at(ptr, size)
        finally:
            _kernel32.GlobalUnlock(handle)


def available_formats() -> List[Tuple[int, str]]:
    """(id, name) of every format currently on the clipboard."""
    out: List[Tuple[int, str]] = []
    buf = ctypes.create_unicode_buffer(256)
    with _Open():
        fmt = 0
        while True:
            fmt = _user32.EnumClipboardFormats(fmt)
            if not fmt:
                break
            n = _user32.GetClipboardFormatNameW(fmt, buf, 256)
            out.append((fmt, buf.value if n else _STANDARD_NAMES.get(fmt, f"#{fmt}")))
    return out


_STANDARD_NAMES = {1: "CF_TEXT", 2: "CF_BITMAP", 7: "CF_OEMTEXT", 8: "CF_DIB", 13: "CF_UNICODETEXT",
                   16: "CF_LOCALE", 17: "CF_DIBV5"}


# --- text helpers ---------------------------------------------------------------
def get_text() -> Optional[str]:
    raw = get_data(CF_UNICODETEXT)
    if raw is None:
        return None
    text = raw.decode("utf-16-le", "replace")
    return text.split("\0", 1)[0]


def set_text(text: str, expect_sequence: Optional[int] = None) -> None:
    set_data({CF_UNICODETEXT: text.encode("utf-16-le") + b"\0\0"}, expect_sequence)


# --- Office equation exchange -----------------------------------------------------
def copy_mathml_for_office(mathml: str, plain_text: Optional[str] = None, expect_sequence: Optional[int] = None) -> None:
    """Put MathML on the clipboard so that Ctrl+V in Word/PowerPoint inserts a native equation.

    `plain_text` (default: the MathML itself) is what a plain-text paste receives.  Keep the default
    for Office: Word (16.0.20527, checked 2026-11-22) pastes from the plain-text format, not from the
    registered "MathML" ones - with ``x`` there it inserts the text x and no equation, and with LaTeX
    there it runs its own LaTeX import (Microsoft 365 >= 2606) instead of using this MathML.
    """
    body = mathml.strip()
    if not body.startswith("<?xml"):
        body = _XML_DECL + body
    payload = body.encode("utf-8") + b"\0"
    text = plain_text if plain_text is not None else mathml
    set_data({
        CF_MATHML: payload,
        CF_MATHML_PRESENTATION: payload,
        CF_UNICODETEXT: text.encode("utf-16-le") + b"\0\0",
    }, expect_sequence)


def read_equation_from_office() -> Optional[str]:
    """MathML of an equation copied from Word/PowerPoint/OneNote, or None if there is none.

    Office puts the equation on the clipboard under the "MathML" and
    "MathML Presentation" formats (UTF-8 XML).
    """
    for fmt in (CF_MATHML, CF_MATHML_PRESENTATION):
        mathml = _decode_text(get_data(fmt))
        if mathml and re.search(r"<(\w+:)?math\b", mathml):  # other programs may write <mml:math>
            return mathml
    return None
