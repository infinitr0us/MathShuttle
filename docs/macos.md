# MathShuttle on macOS

The same code runs on macOS: the window, every conversion, the command line, **Open file…**, the LaTeX
preview and the tests (checked on macOS 27.2 with Python 3.12 / Tk 9.0 and Python 3.9 / Tk 8.6, pandoc
3.14 from Homebrew, MacTeX 2026). Install pandoc with `brew install pandoc`, then either

* `MathShuttle.app`: the standalone app (no Python needed), from the release zip or built with
  `./build_app.sh` (see [Building the app](development.md#building-the-app-macos)); drag it into
  Applications. It is not signed by a
  registered developer: the first time you open a downloaded copy, macOS refuses; open it once with
  System Settings > Privacy & Security > *Open Anyway* (or remove the download mark with
  `xattr -dr com.apple.quarantine MathShuttle.app`). A copy built on your Mac opens directly.
* or `python3 -m mathshuttle` (any Python ≥ 3.9 with tkinter; uv's 3.11 and later work in a `.venv` as
  they are, its 3.9 there needs `TCL_LIBRARY` set to its `lib/tcl8.6`).

An app started from the
Finder does not get the shell's `PATH`, so pandoc is also looked for in `/opt/homebrew/bin` and
`/usr/local/bin`, and `latex` / `dvipng` in `/Library/TeX/texbin`. The shortcuts take Command as well as
Control (Cmd+Shift+C, Cmd+Shift+V, Cmd+Return); the settings are kept in
`~/Library/Application Support/MathShuttle/settings.json`.

**Word / PowerPoint for Mac** (16.115, checked with `tools/mac_office_probe.py`): the clipboard code
uses the general pasteboard (NSPasteboard, through the Objective-C runtime with ctypes). Word copies an
equation as MathML under `public.mathml` and `public.mathml.presentation`, PowerPoint under
`public.mathml`; Word pastes from either, PowerPoint only from `public.mathml`. *Copy for
Word/PowerPoint* writes both, and the MathML as plain text too: without the plain text, PowerPoint
puts the equation into a new text box of its own instead of the one you clicked into. For a selection
of text with equations in it, and for lines aligned at `=`, Mac Word copies no MathML; *Paste from
Word/PowerPoint* then reads the equations from the HTML it copies along. macOS may ask once before the
app reads what another app copied (paste privacy).

**The hotkey** on the Mac is Control+Option+Shift+M (see
[the hotkey](word-and-powerpoint.md#the-hotkey-without-the-window)), registered with
Carbon's `RegisterEventHotKey`, which needs no Accessibility permission; Cmd+Shift+C / Cmd+Shift+V
work while the MathShuttle window is in front.
