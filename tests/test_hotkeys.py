"""The system-wide hotkey (hotkeys.py).  No keystroke is sent: presses are simulated inside this process
(Windows: WM_HOTKEY posted to the listener's thread; macOS: a Carbon hot key event)."""
import os
import sys
import time

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from mathshuttle import hotkeys  # noqa: E402

# an unusual combination, so that the test neither collides with nor takes a key the user relies on
KEY = "Ctrl+Alt+Shift+F24" if sys.platform == "win32" else "Ctrl+Alt+Shift+F20"  # the Mac has no F21-F24


@pytest.mark.parametrize("text, expected", [
    ("Ctrl+Alt+Shift+M", (hotkeys.MOD_CONTROL | hotkeys.MOD_ALT | hotkeys.MOD_SHIFT, ord("M"))),
    ("ctrl + alt + f9", (hotkeys.MOD_CONTROL | hotkeys.MOD_ALT, 0x78)),
    ("Win+Shift+1", (hotkeys.MOD_WIN | hotkeys.MOD_SHIFT, ord("1"))),
])
def test_parse_windows(text, expected):
    assert hotkeys.parse_windows(text) == expected


@pytest.mark.parametrize("text, expected", [
    ("Ctrl+Alt+Shift+M", (0x1000 | 0x0800 | 0x0200, 0x2E)),   # controlKey | optionKey | shiftKey, kVK_ANSI_M
    ("cmd + option + f9", (0x0100 | 0x0800, 0x65)),
    ("Command+Shift+1", (0x0100 | 0x0200, 0x12)),
])
def test_parse_macos(text, expected):
    assert hotkeys.parse_macos(text) == expected


@pytest.mark.parametrize("text", ["Shift+M", "M", "", "Ctrl+Hyper+M", "Ctrl+Alt+Enterprise"])
def test_parse_rejects(text):
    for parse in (hotkeys.parse_windows, hotkeys.parse_macos):
        with pytest.raises(ValueError):
            parse(text)


def test_display():
    expected = "Control+Option+Shift+M" if sys.platform == "darwin" else "Ctrl+Alt+Shift+M"
    assert hotkeys.display("Ctrl+Alt+Shift+M") == expected


@pytest.mark.skipif(sys.platform in ("win32", "darwin"), reason="where there is no hotkey")
def test_listener_reports_why_there_is_no_hotkey():
    listener = hotkeys.HotkeyListener("Ctrl+Alt+Shift+M")
    assert hotkeys.UNAVAILABLE and listener.start_and_wait() == hotkeys.UNAVAILABLE
    assert not listener.is_alive()  # no thread was started
    listener.stop()  # and stopping it is harmless


@pytest.mark.skipif(sys.platform != "darwin", reason="Carbon: macOS")
def test_mac_listener_registers_hears_and_releases():
    import tkinter as tk
    root = tk.Tk()  # Tk's event loop is what delivers the presses in the app
    root.withdraw()
    listener = hotkeys.HotkeyListener(KEY)
    try:
        assert listener.start_and_wait() is None and listener.is_alive()
        other = hotkeys.HotkeyListener(KEY)
        assert "already in use" in (other.start_and_wait() or "")  # the same combination twice
        listener.simulate_press()  # straight to the application's event target
        assert listener.presses.get(timeout=2) == KEY
        listener.post_press()  # into the main event queue: Tk's event loop must dispatch it
        end = time.time() + 3
        while listener.presses.empty() and time.time() < end:
            root.update()
            time.sleep(0.02)
        assert listener.presses.get_nowait() == KEY
        other_key = hotkeys.HotkeyListener("Ctrl+Alt+Shift+F19")
        assert other_key.start_and_wait() is None
        other_key.simulate_press()  # another listener's press is not ours
        assert listener.presses.empty() and other_key.presses.get(timeout=2)
        other_key.stop()
    finally:
        listener.stop()
        root.destroy()
    assert not listener.is_alive()
    again = hotkeys.HotkeyListener(KEY)
    assert again.start_and_wait() is None  # released by stop()
    again.stop()


@pytest.mark.skipif(sys.platform != "win32", reason="RegisterHotKey: Windows")
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
