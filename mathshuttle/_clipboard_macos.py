"""macOS pasteboard (NSPasteboard) through the Objective-C runtime with ctypes; used through :mod:`.clipboard`.

No PyObjC: the runtime's C functions (``objc_getClass``, ``sel_registerName``, ``objc_msgSend``) are
called directly.  ``objc_msgSend`` has no signature of its own; every call must go through a function
pointer of the method's exact argument and result types.  On arm64 this is not optional: a variadic
call passes its arguments on the stack, where the method does not look for them.  So each signature
used here gets its own prototype (which also keeps threads from racing on a shared ``argtypes``).

Every public function runs in an autorelease pool of its own, so objects the frameworks hand back
autoreleased are freed even when called from a thread without a run loop (the hotkey's worker).

macOS may ask the user before an app reads pasteboard data that another app wrote without a paste
command (paste privacy).  Listing the types and reading the change count do not read the data itself;
reading an equation that Word copied (:func:`read_equation_from_office`) does.
"""
from __future__ import annotations

import ctypes
import re
import threading
from typing import Dict, List, Optional, Tuple, Union

# shared by the platform backends: the exceptions the app catches, the decoding of registered formats
from .clipboard import _XML_DECL, ClipboardChanged, ClipboardError, _decode_text

# The pasteboard types an equation travels under, in the order they are read.  Found with Word and
# PowerPoint 16.115 on macOS 27.2 (tools/mac_office_probe.py, 2026-11-26):
# * Word copies an equation as MathML (UTF-8, no XML declaration) under both of these, next to RTF,
#   HTML (with the OMML), PDF and OLE types.  For a selection with text, or a math paragraph of several
#   aligned lines, it writes no MathML (the types may be listed with no data): read_office_html().
# * Word pastes an equation from either type; PowerPoint only from public.mathml.  With MathML and no
#   plain text, PowerPoint puts the equation into a new text box of its own instead of the one with the
#   cursor.  With the MathML as plain text as well (as on Windows), both paste in place; Word and
#   PowerPoint also turn plain-text MathML, or $...$ LaTeX, into an equation by themselves.
# Reading also takes any other type whose name contains "mathml" (any case); writing puts the MathML
# under these and as plain text.
MATHML_TYPES = (
    "public.mathml",
    "public.mathml.presentation",
)
TEXT_TYPE = "public.utf8-plain-text"  # NSPasteboardTypeString
HTML_TYPE = "public.html"

_objc = ctypes.CDLL("/usr/lib/libobjc.A.dylib")
ctypes.CDLL("/System/Library/Frameworks/AppKit.framework/AppKit")  # defines NSPasteboard (Tk loads it too)

_objc.objc_getClass.argtypes = [ctypes.c_char_p]
_objc.objc_getClass.restype = ctypes.c_void_p
_objc.sel_registerName.argtypes = [ctypes.c_char_p]
_objc.sel_registerName.restype = ctypes.c_void_p
_objc.objc_autoreleasePoolPush.argtypes = []
_objc.objc_autoreleasePoolPush.restype = ctypes.c_void_p
_objc.objc_autoreleasePoolPop.argtypes = [ctypes.c_void_p]
_objc.objc_autoreleasePoolPop.restype = None

_id = ctypes.c_void_p
_NSInteger = ctypes.c_ssize_t   # long on 64-bit macOS
_NSUInteger = ctypes.c_size_t   # unsigned long
_BOOL = ctypes.c_bool           # bool on arm64, signed char (0 / 1) on x86_64: one byte either way
_MSG_SEND = ctypes.cast(_objc.objc_msgSend, ctypes.c_void_p).value


def _prototype(restype, *argtypes):
    """objc_msgSend as a function (receiver, selector, *argtypes) -> restype."""
    return ctypes.CFUNCTYPE(restype, _id, ctypes.c_void_p, *argtypes)(_MSG_SEND)


_send = _prototype(_id)                                     # -(id)m
_send_int = _prototype(_NSInteger)                          # -(NSInteger)m
_send_uint = _prototype(_NSUInteger)                        # -(NSUInteger)m
_send_ptr = _prototype(ctypes.c_void_p)                     # -(const void *)m
_send_id = _prototype(_id, _id)                             # -(id)m:(id)a
_send_index = _prototype(_id, _NSUInteger)                  # -(id)m:(NSUInteger)a
_send_encode = _prototype(_id, _NSUInteger, _BOOL)          # -(id)m:(NSUInteger)a b:(BOOL)b
_send_bytes = _prototype(_id, ctypes.c_void_p, _NSUInteger)                # -(id)m:(const void *)a b:(NSUInteger)b
_send_bytes_enc = _prototype(_id, ctypes.c_void_p, _NSUInteger, _NSUInteger)  # ... c:(NSUInteger)c
_send_set = _prototype(_BOOL, _id, _id)                     # -(BOOL)m:(id)a b:(id)b

_NSUTF8StringEncoding = 4


def _class(name: str) -> int:
    cls = _objc.objc_getClass(name.encode("ascii"))
    if not cls:
        raise ClipboardError("The Objective-C class %s is not available" % name)
    return cls


_SEL = {name: _objc.sel_registerName(name.encode("ascii")) for name in (
    "generalPasteboard", "changeCount", "clearContents", "types", "dataForType:", "stringForType:",
    "setData:forType:", "setString:forType:", "count", "objectAtIndex:", "alloc", "initWithBytes:length:encoding:",
    "autorelease", "dataWithBytes:length:", "dataUsingEncoding:allowLossyConversion:", "length", "bytes")}
_NSPasteboard, _NSString, _NSData = _class("NSPasteboard"), _class("NSString"), _class("NSData")

# Serialises this process's own pasteboard use (the Tk thread and the hotkey's worker), so that the
# expect_sequence check and the write that follows it are not split by another write of ours.  Other
# apps are not held off: macOS has no clipboard lock like OpenClipboard; the gap is a few calls long.
_lock = threading.Lock()


class _Pool:
    """An autorelease pool for the duration of a `with` block."""

    def __enter__(self):
        self._pool = _objc.objc_autoreleasePoolPush()
        return self

    def __exit__(self, *exc):
        _objc.objc_autoreleasePoolPop(self._pool)
        return False


# --- conversions between Python and Foundation objects (call inside a pool) --------------------
def _nsstring(text: str) -> int:
    # a lone surrogate (possible in a Python str, not in UTF-8) becomes "?" rather than failing the copy
    data = text.encode("utf-8", "replace")
    obj = _send_bytes_enc(_send(_NSString, _SEL["alloc"]), _SEL["initWithBytes:length:encoding:"],
                          data, len(data), _NSUTF8StringEncoding)
    if not obj:
        raise ClipboardError("Could not make an NSString")
    return _send(obj, _SEL["autorelease"])


def _nsdata(data: bytes) -> int:
    obj = _send_bytes(_NSData, _SEL["dataWithBytes:length:"], data, len(data))
    if not obj:
        raise ClipboardError("Could not make an NSData")
    return obj


def _bytes(nsdata: int) -> bytes:
    n = _send_uint(nsdata, _SEL["length"])
    return ctypes.string_at(_send_ptr(nsdata, _SEL["bytes"]), n) if n else b""


def _str(nsstring: int) -> str:
    # dataUsingEncoding: rather than UTF8String: the text may contain NUL characters
    data = _send_encode(nsstring, _SEL["dataUsingEncoding:allowLossyConversion:"], _NSUTF8StringEncoding, True)
    return _bytes(data).decode("utf-8", "replace") if data else ""


def _pasteboard() -> int:
    pb = _send(_NSPasteboard, _SEL["generalPasteboard"])
    if not pb:
        raise ClipboardError("Could not open the pasteboard")
    return pb


# --- the API (see clipboard.py) ---------------------------------------------------------------
def sequence_number() -> int:
    """The general pasteboard's changeCount: it changes whenever anything is copied."""
    with _Pool():
        return _send_int(_pasteboard(), _SEL["changeCount"])


def set_data(entries: Dict[str, Union[bytes, str]], expect_sequence: Optional[int] = None) -> None:
    """Replace the pasteboard contents with {type: data} (bytes as they are, str as a string), in the
    given order (the first is the richest representation, by the pasteboard's convention)."""
    with _lock, _Pool():
        pb = _pasteboard()
        if expect_sequence is not None and _send_int(pb, _SEL["changeCount"]) != expect_sequence:
            raise ClipboardChanged("The clipboard changed meanwhile (something else was copied): left as it is")
        _send_int(pb, _SEL["clearContents"])
        for fmt, value in entries.items():
            if isinstance(value, str):
                ok = _send_set(pb, _SEL["setString:forType:"], _nsstring(value), _nsstring(fmt))
            else:
                ok = _send_set(pb, _SEL["setData:forType:"], _nsdata(bytes(value)), _nsstring(fmt))
            if not ok:
                raise ClipboardError("Could not put %s on the clipboard" % fmt)


def get_data(fmt: str) -> Optional[bytes]:
    with _lock, _Pool():
        data = _send_id(_pasteboard(), _SEL["dataForType:"], _nsstring(fmt))
        return _bytes(data) if data else None


def available_formats() -> List[Tuple[str, str]]:
    """(type, type) for each type on the pasteboard: the key :func:`get_data` takes, and its name."""
    with _lock, _Pool():
        types = _send(_pasteboard(), _SEL["types"])
        n = _send_uint(types, _SEL["count"]) if types else 0
        names = [_str(_send_index(types, _SEL["objectAtIndex:"], i)) for i in range(n)]
    return [(name, name) for name in names]


def get_text() -> Optional[str]:
    with _lock, _Pool():
        text = _send_id(_pasteboard(), _SEL["stringForType:"], _nsstring(TEXT_TYPE))
        return _str(text) if text else None


def set_text(text: str, expect_sequence: Optional[int] = None) -> None:
    set_data({TEXT_TYPE: text}, expect_sequence)


# --- Office equation exchange -----------------------------------------------------------------
def _mathml_payload(mathml: str) -> bytes:
    """What goes under each MathML type: UTF-8 with an XML declaration, as Office writes on Windows,
    without the NUL that ends a Windows clipboard string (pasteboard data carries its length)."""
    body = mathml.strip()
    if not body.startswith("<?xml"):
        body = _XML_DECL + body
    return body.encode("utf-8")


def copy_mathml_for_office(mathml: str, plain_text: Optional[str] = None, expect_sequence: Optional[int] = None) -> None:
    """Put MathML under every type of MATHML_TYPES and, as on Windows (where Word pastes from the plain
    text), as the plain text too unless `plain_text` says otherwise."""
    payload = _mathml_payload(mathml)
    entries: Dict[str, Union[bytes, str]] = {t: payload for t in MATHML_TYPES}
    entries[TEXT_TYPE] = plain_text if plain_text is not None else mathml
    set_data(entries, expect_sequence)


def _mathml_types_on(types: List[str]) -> List[str]:
    """The types to look for an equation under: MATHML_TYPES first, then any other "mathml" type."""
    return [t for t in MATHML_TYPES if t in types] + \
           [t for t in types if "mathml" in t.lower() and t not in MATHML_TYPES]


def read_equation_from_office() -> Optional[str]:
    """MathML of an equation on the pasteboard (under one of the MathML types), or None."""
    for fmt in _mathml_types_on([name for _, name in available_formats()]):
        mathml = _decode_text(get_data(fmt))
        if mathml and re.search(r"<(\w+:)?math\b", mathml):  # other programs may write <mml:math>
            return mathml
    return None


def read_office_html() -> Optional[str]:
    """The HTML on the pasteboard (Word writes its equations into it as OMML), or None."""
    return _decode_text(get_data(HTML_TYPE))
