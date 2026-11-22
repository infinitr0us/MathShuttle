"""The clipboard via ctypes (no third-party dependencies): text, and the equations Office copies and pastes.

Word / PowerPoint / OneNote exchange equations as MathML: on Windows through the registered clipboard
format ``"MathML"`` (and ``"MathML Presentation"``), see :mod:`._clipboard_win32`; on macOS through the
general pasteboard (NSPasteboard), see :mod:`._clipboard_macos`, whose MathML types are still to be
confirmed against Mac Word / PowerPoint.

This module is what the app and the CLI use.  The platform backend is loaded on first use, not on
import: importing the GUI never fails because of the clipboard, and a backend that cannot be loaded
is a ClipboardError where the clipboard is used, which the callers report.
"""
from __future__ import annotations

import sys
from typing import Dict, List, Optional, Tuple


class ClipboardError(RuntimeError):
    pass


class ClipboardChanged(ClipboardError):
    """Something else was copied after the clipboard was read: it is not overwritten."""


# --- shared by the backends ---------------------------------------------------------
_XML_DECL = '<?xml version="1.0" encoding="UTF-8"?>\n'


def _decode_text(raw: Optional[bytes]) -> Optional[str]:
    """Text in a registered format: UTF-8 (what Office writes) or, from some other programs,
    UTF-16-LE - recognised by its BOM or by the NUL after the first (ASCII) character."""
    if raw is None:
        return None
    if raw[:2] == b"\xff\xfe" or (raw[1:2] == b"\0" and raw[:1] not in (b"", b"\0")):
        text = raw[:len(raw) & ~1].decode("utf-16-le", "replace")
        return text.lstrip("\ufeff").split("\0", 1)[0]
    return raw.split(b"\0", 1)[0].decode("utf-8-sig", "replace")


_backend_module = None


def _backend():
    global _backend_module
    if _backend_module is None:
        try:
            if sys.platform == "win32":
                from . import _clipboard_win32 as module
            elif sys.platform == "darwin":
                from . import _clipboard_macos as module
            else:
                raise ClipboardError("No clipboard support on %s (Windows and macOS only)" % sys.platform)
        except (OSError, AttributeError) as e:  # a system library that cannot be loaded or lacks a function
            raise ClipboardError("Cannot use the clipboard: %s" % e)
        _backend_module = module
    return _backend_module


# --- the API (formats: Win32 format ids on Windows, pasteboard type names on macOS) ----------
def sequence_number() -> int:
    """Changes whenever the clipboard's contents change (Windows: GetClipboardSequenceNumber,
    macOS: the pasteboard's changeCount)."""
    return _backend().sequence_number()


def set_data(entries: Dict, expect_sequence: Optional[int] = None) -> None:
    """Replace the clipboard contents with the given {format: raw bytes} entries, all at once.  With
    `expect_sequence` (a :func:`sequence_number` taken when the clipboard was read), raise
    ClipboardChanged instead if it changed since."""
    _backend().set_data(entries, expect_sequence)


def get_data(fmt) -> Optional[bytes]:
    """Raw bytes stored under `fmt`, or None if that format is not on the clipboard."""
    return _backend().get_data(fmt)


def available_formats() -> List[Tuple[object, str]]:
    """(format, name) of every format currently on the clipboard; `format` is what :func:`get_data` takes."""
    return _backend().available_formats()


def get_text() -> Optional[str]:
    """The clipboard's plain text, or None if it holds none."""
    return _backend().get_text()


def set_text(text: str, expect_sequence: Optional[int] = None) -> None:
    """Replace the clipboard contents with plain text (`expect_sequence`: as for :func:`set_data`)."""
    _backend().set_text(text, expect_sequence)


def copy_mathml_for_office(mathml: str, plain_text: Optional[str] = None, expect_sequence: Optional[int] = None) -> None:
    """Put MathML on the clipboard so that pasting in Word / PowerPoint inserts a native equation;
    `plain_text` (default: the MathML itself, which Word on Windows pastes from) is what a plain-text
    paste receives."""
    _backend().copy_mathml_for_office(mathml, plain_text, expect_sequence)


def read_equation_from_office() -> Optional[str]:
    """MathML of an equation copied from Word / PowerPoint / OneNote, or None if there is none."""
    return _backend().read_equation_from_office()
