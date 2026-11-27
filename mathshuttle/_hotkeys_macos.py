"""The system-wide hotkey on macOS: Carbon's RegisterEventHotKey through ctypes (no PyObjC, and no
Accessibility permission: a registered hot key is not a keyboard monitor).

Registration happens on Tk's main thread.  macOS delivers each press as a Carbon event to the
application's event target while the app's event loop runs (Tk's), and the handler puts it into the
queue that the app polls, as the Windows listener does (see :mod:`.hotkeys`).
"""
from __future__ import annotations

import ctypes
import itertools
import queue
from typing import Optional

_carbon = ctypes.CDLL("/System/Library/Frameworks/Carbon.framework/Carbon")


def _fourcc(code: str) -> int:
    return int.from_bytes(code.encode("ascii"), "big")


class _EventTypeSpec(ctypes.Structure):
    _fields_ = [("eventClass", ctypes.c_uint32), ("eventKind", ctypes.c_uint32)]


class _EventHotKeyID(ctypes.Structure):
    _fields_ = [("signature", ctypes.c_uint32), ("id", ctypes.c_uint32)]


_HANDLER = ctypes.CFUNCTYPE(ctypes.c_int32, ctypes.c_void_p, ctypes.c_void_p, ctypes.c_void_p)
_p = ctypes.c_void_p
for name, restype, argtypes in (
        ("GetApplicationEventTarget", _p, []),
        ("InstallEventHandler", ctypes.c_int32, [_p, _HANDLER, ctypes.c_ulong, ctypes.POINTER(_EventTypeSpec), _p,
                                                 ctypes.POINTER(_p)]),
        ("RemoveEventHandler", ctypes.c_int32, [_p]),
        ("RegisterEventHotKey", ctypes.c_int32, [ctypes.c_uint32, ctypes.c_uint32, _EventHotKeyID, _p, ctypes.c_uint32,
                                                 ctypes.POINTER(_p)]),
        ("UnregisterEventHotKey", ctypes.c_int32, [_p]),
        ("GetEventParameter", ctypes.c_int32, [_p, ctypes.c_uint32, ctypes.c_uint32, _p, ctypes.c_ulong, _p, _p]),
        ("SetEventParameter", ctypes.c_int32, [_p, ctypes.c_uint32, ctypes.c_uint32, ctypes.c_ulong, _p]),
        ("CreateEvent", ctypes.c_int32, [_p, ctypes.c_uint32, ctypes.c_uint32, ctypes.c_double, ctypes.c_uint32,
                                         ctypes.POINTER(_p)]),
        ("SendEventToEventTarget", ctypes.c_int32, [_p, _p]),
        ("GetMainEventQueue", _p, []),
        ("PostEventToQueue", ctypes.c_int32, [_p, _p, ctypes.c_int16]),
        ("ReleaseEvent", None, [_p])):
    fn = getattr(_carbon, name)
    fn.restype, fn.argtypes = restype, argtypes

kEventClassKeyboard = _fourcc("keyb")
kEventHotKeyPressed = 5
kEventParamDirectObject = _fourcc("----")
typeEventHotKeyID = _fourcc("hkid")
eventNotHandledErr = -9874
eventHotKeyExistsErr = -9878
_SIGNATURE = _fourcc("MSht")
_IDS = itertools.count(1)


class MacHotkeyListener:
    """Listens for one hotkey; each press is put into `presses`.  Same interface as the Windows
    listener: start_and_wait() returns None once registered, else why not; stop(); simulate_press()."""

    def __init__(self, hotkey: str, mods: int, keycode: int) -> None:
        self.hotkey = hotkey
        self.mods, self.keycode = mods, keycode
        self.presses: "queue.Queue[str]" = queue.Queue()
        self.error: Optional[str] = None
        self._id = next(_IDS)
        self._hotkey_ref = _p()
        self._handler_ref = _p()
        self._callback = _HANDLER(self._on_event)  # kept alive: Carbon holds only the pointer

    def _on_event(self, call_ref, event, user_data) -> int:
        hkid = _EventHotKeyID()
        status = _carbon.GetEventParameter(event, kEventParamDirectObject, typeEventHotKeyID, None,
                                           ctypes.sizeof(hkid), None, ctypes.byref(hkid))
        if status == 0 and hkid.signature == _SIGNATURE and hkid.id == self._id:
            self.presses.put(self.hotkey)
            return 0
        return eventNotHandledErr  # another listener's key

    def start_and_wait(self, timeout: float = 2.0) -> Optional[str]:
        target = _carbon.GetApplicationEventTarget()
        spec = _EventTypeSpec(kEventClassKeyboard, kEventHotKeyPressed)
        status = _carbon.InstallEventHandler(target, self._callback, 1, ctypes.byref(spec), None,
                                             ctypes.byref(self._handler_ref))
        if status:
            self.error = "could not listen for the hotkey (Carbon error %d)" % status
            return self.error
        status = _carbon.RegisterEventHotKey(self.keycode, self.mods, _EventHotKeyID(_SIGNATURE, self._id), target, 0,
                                             ctypes.byref(self._hotkey_ref))
        if status:
            self.stop()
            self.error = ("%s is already used by another program" % self.hotkey if status == eventHotKeyExistsErr
                          else "could not register %s (Carbon error %d)" % (self.hotkey, status))
        return self.error

    def is_alive(self) -> bool:
        return bool(self._hotkey_ref.value)

    def stop(self) -> None:
        if self._hotkey_ref.value:
            _carbon.UnregisterEventHotKey(self._hotkey_ref)
            self._hotkey_ref = _p()
        if self._handler_ref.value:
            _carbon.RemoveEventHandler(self._handler_ref)
            self._handler_ref = _p()

    def _event(self) -> _p:
        event = _p()
        if _carbon.CreateEvent(None, kEventClassKeyboard, kEventHotKeyPressed, 0.0, 0, ctypes.byref(event)):
            raise OSError("CreateEvent failed")
        hkid = _EventHotKeyID(_SIGNATURE, self._id)
        _carbon.SetEventParameter(event, kEventParamDirectObject, typeEventHotKeyID, ctypes.sizeof(hkid),
                                  ctypes.byref(hkid))
        return event

    def simulate_press(self) -> None:
        """Deliver a hot key event of this listener to the application target, as a press would (for
        tests: no keystroke is sent anywhere, the event stays in this process)."""
        event = self._event()
        try:
            _carbon.SendEventToEventTarget(event, _carbon.GetApplicationEventTarget())
        finally:
            _carbon.ReleaseEvent(event)

    def post_press(self) -> None:
        """Post a hot key event of this listener to the main event queue, from which the app's event loop
        (Tk's) dispatches it as it does a real press (for tests; stays in this process)."""
        event = self._event()
        try:
            _carbon.PostEventToQueue(_carbon.GetMainEventQueue(), event, 1)  # kEventPriorityStandard
        finally:
            _carbon.ReleaseEvent(event)
