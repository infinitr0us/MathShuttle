"""A system-wide hotkey, heard while the app runs, even minimised: Win32 RegisterHotKey, or on macOS
Carbon's RegisterEventHotKey (:mod:`._hotkeys_macos`), both via ctypes.

RegisterHotKey with no window posts WM_HOTKEY to the message queue of the thread that registered it,
so the Windows :class:`HotkeyListener` registers the key in a thread of its own, which runs a message
loop and puts each press into a queue that the Tk thread polls (Tk must only be used from its own
thread).  The macOS listener registers on Tk's thread and fills the same kind of queue.
"""
from __future__ import annotations

import ctypes
import ctypes.wintypes as wt
import queue
import re
import sys
import threading
from typing import Optional, Tuple

# Why there is no hotkey (None: there is one).  The module still imports elsewhere, for parse() and so
# that the app can say so; a listener there reports this reason instead of starting.
UNAVAILABLE: Optional[str] = None if sys.platform in ("win32", "darwin") else "Windows and macOS only"

if sys.platform == "win32":
    _user32 = ctypes.windll.user32
    _kernel32 = ctypes.windll.kernel32
    _user32.RegisterHotKey.argtypes = [wt.HWND, ctypes.c_int, wt.UINT, wt.UINT]
    _user32.RegisterHotKey.restype = wt.BOOL
    _user32.UnregisterHotKey.argtypes = [wt.HWND, ctypes.c_int]
    _user32.GetMessageW.argtypes = [ctypes.POINTER(wt.MSG), wt.HWND, wt.UINT, wt.UINT]
    _user32.GetMessageW.restype = wt.BOOL
    _user32.PostThreadMessageW.argtypes = [wt.DWORD, wt.UINT, wt.WPARAM, wt.LPARAM]
    _user32.PostThreadMessageW.restype = wt.BOOL
    _user32.PeekMessageW.argtypes = [ctypes.POINTER(wt.MSG), wt.HWND, wt.UINT, wt.UINT, wt.UINT]

WM_HOTKEY = 0x0312
WM_QUIT = 0x0012
MOD_ALT, MOD_CONTROL, MOD_SHIFT, MOD_WIN, MOD_NOREPEAT = 0x1, 0x2, 0x4, 0x8, 0x4000
_MODIFIERS = {"ctrl": MOD_CONTROL, "control": MOD_CONTROL, "alt": MOD_ALT, "shift": MOD_SHIFT, "win": MOD_WIN}
_KEYS = {**{"f%d" % i: 0x6F + i for i in range(1, 25)}, "space": 0x20, "insert": 0x2D, "home": 0x24,
         "end": 0x23, "pageup": 0x21, "pagedown": 0x22}

DEFAULT = "Ctrl+Alt+Shift+M"  # on the Mac: Control+Option+Shift+M

# macOS: Carbon's modifier bits and the virtual key codes (positions on an ANSI keyboard)
_MAC_MODIFIERS = {"ctrl": 0x1000, "control": 0x1000, "alt": 0x0800, "option": 0x0800, "opt": 0x0800,
                  "shift": 0x0200, "cmd": 0x0100, "command": 0x0100, "win": 0x0100}
_MAC_KEYS = {
    **dict(zip("asdfhgzxcv", range(0x00, 0x0A))), "b": 0x0B, "q": 0x0C, "w": 0x0D, "e": 0x0E, "r": 0x0F,
    "y": 0x10, "t": 0x11, "1": 0x12, "2": 0x13, "3": 0x14, "4": 0x15, "6": 0x16, "5": 0x17, "9": 0x19,
    "7": 0x1A, "8": 0x1C, "0": 0x1D, "o": 0x1F, "u": 0x20, "i": 0x22, "p": 0x23, "l": 0x25, "j": 0x26,
    "k": 0x28, "n": 0x2D, "m": 0x2E,
    "f1": 0x7A, "f2": 0x78, "f3": 0x63, "f4": 0x76, "f5": 0x60, "f6": 0x61, "f7": 0x62, "f8": 0x64,
    "f9": 0x65, "f10": 0x6D, "f11": 0x67, "f12": 0x6F, "f13": 0x69, "f14": 0x6B, "f15": 0x71, "f16": 0x6A,
    "f17": 0x40, "f18": 0x4F, "f19": 0x50, "f20": 0x5A,
    "space": 0x31, "insert": 0x72, "home": 0x73, "end": 0x77, "pageup": 0x74, "pagedown": 0x79}


def parse_macos(hotkey: str) -> Tuple[int, int]:
    """``"Ctrl+Alt+Shift+M"`` -> (Carbon modifiers, virtual key code) on the Mac: Ctrl is Control, Alt
    Option, Cmd (or Win) Command; ValueError if it is not one."""
    parts = [p.strip().lower() for p in hotkey.split("+") if p.strip()]
    if not parts:
        raise ValueError("empty hotkey")
    mods = 0
    for p in parts[:-1]:
        if p not in _MAC_MODIFIERS:
            raise ValueError("unknown modifier %r in %r" % (p, hotkey))
        mods |= _MAC_MODIFIERS[p]
    if parts[-1] not in _MAC_KEYS:
        raise ValueError("unknown key %r in %r" % (parts[-1], hotkey))
    if not mods & (0x1000 | 0x0800 | 0x0100):
        raise ValueError("%r needs Ctrl, Alt or Cmd (it would take the key from every program)" % hotkey)
    return mods, _MAC_KEYS[parts[-1]]


def display(hotkey: str) -> str:
    """The hotkey as the user's system names its keys: Control+Option+Shift+M on the Mac."""
    if sys.platform != "darwin":
        return hotkey
    names = {"ctrl": "Control", "control": "Control", "alt": "Option", "option": "Option", "opt": "Option",
             "shift": "Shift", "cmd": "Command", "command": "Command", "win": "Command"}
    parts = [p.strip() for p in hotkey.split("+") if p.strip()]
    return "+".join([names.get(p.lower(), p) for p in parts[:-1]] + parts[-1:])


def parse(hotkey: str) -> Tuple[int, int]:
    """The hotkey for this system: :func:`parse_macos` on the Mac, else :func:`parse_windows`."""
    return parse_macos(hotkey) if sys.platform == "darwin" else parse_windows(hotkey)


def parse_windows(hotkey: str) -> Tuple[int, int]:
    """``"Ctrl+Alt+Shift+M"`` -> (modifiers, virtual-key code); ValueError if it is not one."""
    parts = [p.strip().lower() for p in hotkey.split("+") if p.strip()]
    if not parts:
        raise ValueError("empty hotkey")
    mods = 0
    for p in parts[:-1]:
        if p not in _MODIFIERS:
            raise ValueError("unknown modifier %r in %r" % (p, hotkey))
        mods |= _MODIFIERS[p]
    key = parts[-1]
    if re.fullmatch(r"[a-z0-9]", key):
        vk = ord(key.upper())
    elif key in _KEYS:
        vk = _KEYS[key]
    else:
        raise ValueError("unknown key %r in %r" % (key, hotkey))
    if not mods & (MOD_CONTROL | MOD_ALT | MOD_WIN):
        raise ValueError("%r needs Ctrl, Alt or Win (it would take the key from every program)" % hotkey)
    return mods, vk


class HotkeyListener(threading.Thread):
    """Listens for one hotkey; each press is put into `presses`.  `error` says why it could not be
    registered (another program holds the combination), after `started` is set."""

    def __init__(self, hotkey: str) -> None:
        super().__init__(daemon=True, name="hotkey")
        self.hotkey = hotkey
        self.mods, self.vk = parse_windows(hotkey)
        self.presses: "queue.Queue[str]" = queue.Queue()
        self.started = threading.Event()
        self.cancelled = threading.Event()  # given up on (start_and_wait timed out): must not keep the key
        self._lock = threading.Lock()
        self.error: Optional[str] = None
        self.thread_id = 0

    def run(self) -> None:
        self.thread_id = _kernel32.GetCurrentThreadId()
        msg = wt.MSG()
        _user32.PeekMessageW(ctypes.byref(msg), None, 0, 0, 0)  # create the message queue first
        if not _user32.RegisterHotKey(None, 1, self.mods | MOD_NOREPEAT, self.vk):
            self.error = "%s is already used by another program" % self.hotkey
            self.started.set()
            return
        with self._lock:  # against start_and_wait giving up at this very moment
            if self.cancelled.is_set():  # registered after the caller stopped waiting: release the key
                _user32.UnregisterHotKey(None, 1)
                return
            self.started.set()
        try:
            while _user32.GetMessageW(ctypes.byref(msg), None, 0, 0) > 0:
                if msg.message == WM_HOTKEY:
                    self.presses.put(self.hotkey)
        finally:
            _user32.UnregisterHotKey(None, 1)

    def start_and_wait(self, timeout: float = 2.0) -> Optional[str]:
        """Start; None once the hotkey is registered, else the reason it is not."""
        if UNAVAILABLE is not None:
            return UNAVAILABLE  # no thread: there is nothing to register the key with
        self.start()
        if not self.started.wait(timeout):
            with self._lock:
                self.cancelled.set()
                registered = self.started.is_set() and self.error is None  # just now, after all
            if registered:
                self.stop()
            return "the hotkey thread did not start"
        return self.error

    def stop(self) -> None:
        if self.thread_id and self.is_alive():
            _user32.PostThreadMessageW(self.thread_id, WM_QUIT, 0, 0)
            self.join(1.0)

    def simulate_press(self) -> None:
        """Post WM_HOTKEY as a press would (for tests: no keystroke is sent anywhere)."""
        _user32.PostThreadMessageW(self.thread_id, WM_HOTKEY, 1, 0)


if sys.platform == "darwin":
    from . import _hotkeys_macos

    class HotkeyListener(_hotkeys_macos.MacHotkeyListener):  # type: ignore[no-redef]  # noqa: F811
        """The macOS listener (Carbon), with the Windows listener's interface."""

        def __init__(self, hotkey: str) -> None:
            super().__init__(hotkey, *parse_macos(hotkey))
