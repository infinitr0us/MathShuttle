"""The system-wide hotkey (hotkeys.py).  No keystroke is sent: presses are simulated with WM_HOTKEY."""
import os
import sys
import time

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from mathshuttle import hotkeys  # noqa: E402

# an unusual combination, so that the test neither collides with nor takes a key the user relies on
KEY = "Ctrl+Alt+Shift+F24"


@pytest.mark.parametrize("text, expected", [
    ("Ctrl+Alt+Shift+M", (hotkeys.MOD_CONTROL | hotkeys.MOD_ALT | hotkeys.MOD_SHIFT, ord("M"))),
    ("ctrl + alt + f9", (hotkeys.MOD_CONTROL | hotkeys.MOD_ALT, 0x78)),
    ("Win+Shift+1", (hotkeys.MOD_WIN | hotkeys.MOD_SHIFT, ord("1"))),
])
def test_parse(text, expected):
    assert hotkeys.parse(text) == expected


@pytest.mark.parametrize("text", ["Shift+M", "M", "", "Ctrl+Hyper+M", "Ctrl+Alt+Enterprise"])
def test_parse_rejects(text):
    with pytest.raises(ValueError):
        hotkeys.parse(text)


@pytest.mark.skipif(sys.platform == "win32", reason="where there is no hotkey")
def test_listener_reports_why_there_is_no_hotkey():
    listener = hotkeys.HotkeyListener("Ctrl+Alt+Shift+M")
    assert hotkeys.UNAVAILABLE and listener.start_and_wait() == hotkeys.UNAVAILABLE
    assert not listener.is_alive()  # no thread was started
    listener.stop()  # and stopping it is harmless
    if sys.platform == "darwin":
        assert hotkeys.UNAVAILABLE == "not on macOS yet"


@pytest.mark.skipif(sys.platform != "win32", reason="RegisterHotKey: the hotkey is Windows only for now")
def test_listener_registers_hears_and_releases():
    listener = hotkeys.HotkeyListener(KEY)
    assert listener.start_and_wait() is None
    try:
        other = hotkeys.HotkeyListener(KEY)
        assert "already used" in other.start_and_wait()  # as when another program holds it
        listener.simulate_press()
        assert listener.presses.get(timeout=2) == KEY
    finally:
        listener.stop()
    assert not listener.is_alive()
    again = hotkeys.HotkeyListener(KEY)
    assert again.start_and_wait() is None  # released by stop()
    again.stop()
    time.sleep(0.05)
