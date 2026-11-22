"""A system-wide hotkey (Win32 RegisterHotKey via ctypes), heard while the app runs, even minimised.

RegisterHotKey with no window posts WM_HOTKEY to the message queue of the thread that registered it,
so :class:`HotkeyListener` registers the key in a thread of its own, which runs a message loop and
puts each press into a queue that the Tk thread polls (Tk must only be used from its own thread).
"""
from __future__ import annotations

import ctypes
import ctypes.wintypes as wt
import queue
import re
import threading
from typing import Optional, Tuple

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

DEFAULT = "Ctrl+Alt+Shift+M"


def parse(hotkey: str) -> Tuple[int, int]:
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
        self.mods, self.vk = parse(hotkey)
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
