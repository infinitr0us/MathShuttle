# Building, testing and how it works

## Keeping texmath up to date

pandoc is statically linked against texmath: the texmath version you run is the one the
pandoc release was built with, and it cannot be updated separately. When a texmath release
carries a fix you care about, upgrade pandoc once a pandoc release built against it exists
(`winget upgrade JohnMacFarlane.Pandoc`); the two are maintained together and pandoc releases
normally follow within days or weeks.

The right-hand pane of the status bar, the Help window (`?` / F1) and
`python -m mathshuttle version` show the pandoc and embedded texmath versions; the latter
two also print which `pandoc.exe` is in use. Check that path before upgrading: a copy bundled
with `build_exe.ps1 -BundlePandoc` or selected through `MATHSHUTTLE_PANDOC` takes precedence
over the installed pandoc and is not touched by `winget upgrade`: rebuild with `-BundlePandoc`
(or replace that copy) and restart the app. A Scoop shim on `PATH` is followed to the real
executable when reading the texmath version and when bundling. The current directory is never
searched for `pandoc.exe`, and an `MATHSHUTTLE_PANDOC` that names no file is an error.

## Building the exe

```powershell
.\build_exe.ps1                     # creates .venv, installs PyInstaller, builds dist\MathShuttle\
.\build_exe.ps1 -Shortcut           # + Start-menu shortcut   (-DesktopShortcut for the desktop)
.\build_exe.ps1 -BundlePandoc       # + copies pandoc.exe into dist\MathShuttle\ (self-contained, see Licence)
```

## Building the app (macOS)

```sh
./build_app.sh                          # creates .venv-build, installs PyInstaller, builds dist/MathShuttle.app
./build_app.sh --zip                    # + the release zip (tools/make_release.py macos)
PYTHON=/path/to/python3 ./build_app.sh  # build with that Python (it needs tkinter with Tk 8.6 or later)
```

The app is signed ad hoc (no developer certificate), as every Apple silicon program must be.
For a release, `.venv\Scripts\python tools\make_release.py windows` (after `build_exe.ps1`) and
`.venv-build/bin/python tools/make_release.py macos` (or `./build_app.sh --zip`) zip the app with
`LICENSE.txt`, `README.md` and a `THIRD-PARTY-NOTICES.txt`
made from the licences of the Python, Tcl/Tk and PyInstaller the build used (`tools/licenses` holds the
Tcl and Tk terms for Pythons that ship none, such as uv's).

## Tests

```
python -m pytest tests                  # unit + pandoc round trips + headless GUI (needs pandoc, a desktop)
python tests\office_smoke.py            # optional: real Word + PowerPoint via COM (needs pywin32, Office)
python tools\capture_word_fixtures.py   # optional: re-capture tests\fixtures\word from your Word
python tools/mac_office_probe.py        # macOS, interactive: what Mac Word / PowerPoint copy and paste
```
The Mac probe asks you to copy equations in Word and PowerPoint and saves every pasteboard type it
finds, then puts candidates on the pasteboard one at a time for you to paste and rate; the results go
to `probe-out/` (not tracked).
The Office smoke test pastes into hidden Word/PowerPoint instances, copies the equation back,
converts it to LaTeX and closes everything without saving. These overwrite the clipboard, and so do
the clipboard tests in pytest (deselect them with `-k "not copy_for_office and not clipboard"`).
On macOS the Windows-only tests (the Win32 hotkey) are skipped, and the pasteboard tests
(`tests/test_clipboard_macos.py`) read back only what they wrote themselves.
`tests\fixtures\word` holds what Word 16.0.20527 really writes (MathML and OMML) for typical
equations typed into it; the tidy tests run on those. `tests/fixtures/mac_word` holds what Mac Word
and PowerPoint 16.115 copy (MathML, and the HTML fragment with the OMML).

## What Word does with the clipboard (16.0.20527)

Word pastes from the plain-text clipboard format, not from the registered `MathML` formats that
Microsoft documents as taking priority: with the text `x` next to the MathML it inserts the text x,
and with LaTeX there it runs its own LaTeX import. So *Copy for Word/PowerPoint* puts the MathML
in the plain-text format too. Equation arrays need `columnspacing="0em"` (how Word exports them),
otherwise a table pastes as a centred matrix. Every `<mspace>` becomes one ordinary space, while
Unicode spaces are kept; a comma or semicolon between brackets becomes the separator of a bracket
group (nested groups come out wrong) unless it says `separator="false"`. What Word copies back for an
equation it was given, without any edit (`tests/fixtures/recall`): bold letters bold italic,
sans-serif letters sans-serif italic, `ℓ` as `𝓁`, `ϕ` as `φ`, `*` as `∗`, a combining hat as `^`, an
overline as `-`, brackets as a stretchy pair with a script on a closing bracket moved to the whole
group, `1.5` as three tokens, `\qquad` as two em spaces.

## Layout

```
mathshuttle/
  pandoc_engine.py   find/run pandoc; read latex|mathml|omml → Equation(tex, display); write mathml|omml|eqn|typst
  latex_wrap.py      delimiter / environment detection and re-wrapping for LaTeX output
  compat.py          LaTeX texmath does not take → LaTeX it does; macro files
  packs.py           built-in packs: physics, braket, slashed, tensor, siunitx literal units, AASTeX
  diagnose.py        texmath's parse errors in plain words (unknown command, unclosed brace...)
  tidy.py            texmath's LaTeX from MathML / OMML → the LaTeX an author writes
  recall.py          the LaTeX an equation was copied from, when it comes back from Office unedited
  convert.py         convert(text, src, dst, mode, wrapper, macros, tidy) used by GUI and CLI
  documents.py       the equations of .docx / .pptx / .tex / .md files, with where they are; a .docx of them
  wordexport.py      a passage or a whole .tex / .md document as a Word file (numbers, references, citations)
  hotkeys.py         the system-wide hotkey (Windows: RegisterHotKey in a thread of its own)
  _hotkeys_macos.py  the hotkey on macOS (Carbon RegisterEventHotKey via ctypes)
  preview.py         the LaTeX preview (latex + dvipng)
  clipboard.py       the clipboard the app uses; loads the platform's backend on first use
  office_html.py     the OMML of the equations in the HTML that Word copies (when it copies no MathML)
  _clipboard_win32.py  Win32 clipboard via ctypes: "MathML" format in/out, "HTML Format" in
  _clipboard_macos.py  macOS pasteboard via the Objective-C runtime (ctypes): public.mathml in/out, public.html in
  app.py             tkinter GUI
  __main__.py        GUI / CLI entry
```
