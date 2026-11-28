"""The look of the windows: the Sun Valley ttk theme (the Windows 11 style, light and dark) on Windows
and Linux, the native Aqua look on macOS.

The theme's Tcl files and pictures are in ``sv_theme/`` (sv-ttk 2.6.1 by rdbende, MIT licence, see
``sv_theme/LICENSE``); no Python package is needed for it.  Light or dark follows the system
(Windows: *Choose your app mode*), or the ``theme`` entry of the settings / the environment variable
``MATHSHUTTLE_THEME`` (``light``, ``dark``, or ``native`` for Tk's own look).
"""
from __future__ import annotations

import ctypes
import os
import sys
import tkinter as tk
from tkinter import font as tkfont
from tkinter import ttk
from typing import Dict, Optional

_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "sv_theme")
CHOICES = ("system", "light", "dark", "native")

# colours of the classic Tk widgets (text panes, the preview strip, lists) to go with the ttk theme
PALETTES: Dict[str, Dict[str, str]] = {
    "light": {"bg": "#fafafa", "fg": "#1c1c1c", "field": "#ffffff", "border": "#d9d9d9", "focus": "#005fb8",
              "muted": "#5c5c5c", "error": "#b00020", "selbg": "#2f60d8", "selfg": "#ffffff",
              "preview": "#ffffff", "toast": "#202124"},
    "dark": {"bg": "#1c1c1c", "fg": "#fafafa", "field": "#2b2b2b", "border": "#3d3d3d", "focus": "#57c8ff",
             "muted": "#a8a8a8", "error": "#ff99a4", "selbg": "#2f60d8", "selfg": "#ffffff",
             # LaTeX draws black on white: the preview stays a sheet of paper
             "preview": "#ffffff", "toast": "#2b2b2b"},
    "native": {"bg": "", "fg": "", "field": "white", "border": "#c8c8c8", "focus": "#3b82f6",
               "muted": "#555555", "error": "#b00020", "selbg": "", "selfg": "",
               "preview": "white", "toast": "#202124"},
}


def system_prefers_dark() -> bool:
    """Windows: the *app mode* of Settings > Personalisation > Colours; elsewhere light."""
    if sys.platform != "win32":
        return False
    try:
        import winreg
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER,
                            r"Software\Microsoft\Windows\CurrentVersion\Themes\Personalize") as key:
            return winreg.QueryValueEx(key, "AppsUseLightTheme")[0] == 0
    except OSError:
        return False


def resolve(choice: Optional[str] = None) -> str:
    """"light", "dark" or "native" for a setting (None / "system": the environment variable, then the
    system's app mode).  macOS keeps its native look: Aqua follows the system's dark mode by itself."""
    env = os.environ.get("MATHSHUTTLE_THEME", "").strip().lower()
    if env in CHOICES[1:]:
        choice = env
    if choice == "native" or sys.platform == "darwin":
        return "native"
    if choice in ("light", "dark"):
        return choice
    return "dark" if system_prefers_dark() else "light"


def apply(root: tk.Tk, choice: Optional[str] = None) -> str:
    """Give `root` (and the windows it opens) the look; returns "light", "dark" or "native"."""
    mode = resolve(choice)
    if mode == "native":
        return mode
    try:
        if not getattr(root, "_sv_loaded", False):
            root.tk.call("source", os.path.join(_DIR, "sv.tcl"))
            root._sv_loaded = True  # type: ignore[attr-defined]
            _point_sizes(root)
        ttk.Style(root).theme_use("sun-valley-" + mode)
        # the theme sets the window colours on <<ThemeChanged>>, which Tk sends to a window that is
        # not shown yet only later, if at all: now, before the widgets are made
        root.tk.call("configure_colors")
    except tk.TclError:  # a Tk without PNG pictures, missing files: Tk's own look still works
        return "native"
    return mode


def _point_sizes(root: tk.Tk) -> None:
    """The theme gives its fonts in pixels, which are small on a 125 % or 150 % screen: points, which
    Tk scales with the screen, of the same size at 100 %."""
    for name, points in (("SunValleyCaptionFont", 9), ("SunValleyBodyFont", 10), ("SunValleyBodyStrongFont", 10),
                         ("SunValleyBodyLargeFont", 13), ("SunValleySubtitleFont", 15)):
        try:
            tkfont.nametofont(name, root=root).configure(size=points)
        except (tk.TclError, TypeError):
            try:
                root.tk.call("font", "configure", name, "-size", points)
            except tk.TclError:
                pass


def palette(mode: str) -> Dict[str, str]:
    return PALETTES.get(mode, PALETTES["native"])


def style_text(widget: tk.Text, mode: str) -> None:
    """A text pane in the colours of the theme, with a thin border that shows the focus."""
    p = palette(mode)
    options = {"relief": "flat", "borderwidth": 0, "highlightthickness": 1,
               "highlightbackground": p["border"], "highlightcolor": p["focus"]}
    if mode != "native":
        options.update(background=p["field"], foreground=p["fg"], insertbackground=p["fg"],
                       selectbackground=p["selbg"], selectforeground=p["selfg"],
                       inactiveselectbackground=p["selbg"])
    widget.configure(**options)


def dark_title_bar(window: tk.Misc, dark: bool) -> None:
    """Windows 10 (20H1) and 11: a dark title bar to go with a dark window."""
    if sys.platform != "win32":
        return
    try:
        window.update_idletasks()
        hwnd = ctypes.windll.user32.GetParent(window.winfo_id()) or window.winfo_id()
        value = ctypes.c_int(1 if dark else 0)
        # DWMWA_USE_IMMERSIVE_DARK_MODE: 20 (19 before Windows 10 20H1)
        for attribute in (20, 19):
            if ctypes.windll.dwmapi.DwmSetWindowAttribute(ctypes.c_void_p(hwnd), attribute, ctypes.byref(value),
                                                          ctypes.sizeof(value)) == 0:
                break
    except (AttributeError, OSError):
        pass
