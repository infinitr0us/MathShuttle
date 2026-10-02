# Equation Tool

A local, offline replacement for the [texmath web demo](https://johnmacfarlane.net/texmath.html):
convert equations between **LaTeX, MathML and OMML** (Word/PowerPoint "Office Math"), plus
eqn and Typst output — and exchange equations with Word / PowerPoint directly through the
clipboard as native, editable equations.

The conversion engine *is* texmath: it runs inside [pandoc](https://pandoc.org) (same author),
which is the only runtime dependency besides Python's standard library.

```
From: LaTeX | MathML | OMML          To: LaTeX | MathML | OMML | eqn | Typst
```

## Quick start

* `dist\EquationTool\EquationTool.exe` — the standalone app (no Python needed).
  Build it with `.\build_exe.ps1` (see below); add `-Shortcut` for a Start-menu entry.
* `EquationTool.pyw` — double-click to run from source with any Python ≥ 3.9 (tkinter only).
* `python -m equation_tool` — same GUI; with arguments it becomes a CLI (see below).

Requirement: **pandoc** (`winget install JohnMacFarlane.Pandoc`). The app looks for
`pandoc.exe` next to itself, on `PATH`, then in `%LOCALAPPDATA%\Pandoc`; set
`EQUATION_TOOL_PANDOC` to point at a specific copy.

## Word / PowerPoint workflow

**LaTeX → Office.** Type or paste LaTeX, click **Copy for Word/PowerPoint** (Ctrl+Shift+C),
then click inside a Word document / a PowerPoint text box and press **Ctrl+V**: Office inserts
a native equation (the clipboard carries the equation in the `MathML` clipboard format, which
Word, PowerPoint and OneNote recognise). The equation is fully editable in Office afterwards.
Aligned blocks (`align`, `aligned`, `split`, `alignat`, `eqnarray`) arrive as an Office
*equation array* with the `=` signs lined up. Office turns a plain MathML table into a matrix
with centred columns, so the tool marks these tables the way Word itself exports an equation
array (`columnspacing="0em"`). `cases` and matrices stay matrices, and so does an `array` unless its
columns read like an alignment (`\begin{array}{rl} x &= 1`, or `{rcl}` with relations in the middle).

**Office → LaTeX.** Select an equation in Word or PowerPoint, press Ctrl+C, then click
**Paste from Word/PowerPoint** (Ctrl+Shift+V): the tool reads the MathML that Office put on the
clipboard and converts it (to LaTeX by default; any other target works too). Equation arrays
that Office exports as a bare `<mtable>` are recognised and come back as `aligned`, not `matrix`
(continuation lines such as `&+ c` included).

MathML input may also come from elsewhere: prefixed elements (`<mml:math>`, as written by
Word's XSLT, MathType or JATS journal XML), named entities (`&InvisibleTimes;`, from
Mathematica / Maple), several documents each with its own XML declaration.

Tip: LaTeX **→** OMML text output is still there for people who work with the XML directly
(`document.xml`, python-docx, …); for pasting into Office the clipboard route above is the one
to use — raw OMML cannot be pasted.

## LaTeX input: modes and delimiters

Everything pandoc's LaTeX reader understands is accepted, in any mix:

| input | detected mode |
|---|---|
| bare `\frac{a}{b}` (no `$`, `\(`, `\[` or maths environment, except inside `\text`, `\mbox` and similar: `\mbox{for $n$}` is fine) | **Mode** box (Auto = display) |
| `$…$`, `\(…\)`, `\begin{math}` | inline |
| `$$…$$`, `\[…\]`, `\begin{displaymath}` | display |
| `equation`, `equation*` | display |
| `align`, `align*`, `alignat`, `flalign`, `eqnarray`, `gather`, `multline` (+`*`) | display |

`\label`, `\tag`, `\nonumber`, `%` comments and `\newcommand` definitions placed before the
equation are handled; text with several equations converts each of them (output blocks are
separated by a blank line; *Copy for Word/PowerPoint* takes the first).

**Mode** = Auto keeps the detected mode; Display / Inline override it (affects `display="block"`
in MathML, `<m:oMathPara>` vs `<m:oMath>` in OMML, `$ x $` vs `$x$` in Typst, `.EQ` vs inline eqn).

## LaTeX output: the wrapper box

| wrapper | result |
|---|---|
| Auto | keeps the input's environment; otherwise `\[ … \]` (display) or `$…$` (inline) |
| Bare | math-mode content only |
| `$ … $`, `\( … \)` | inline |
| `$$ … $$`, `\[ … \]` | display |
| `equation`, `equation*` | numbered / unnumbered equation |
| `align`, `align*`, `gather`, `gather*`, `multline`, `multline*` | amsmath environments |

Environment content is converted as needed: an `align` body becomes `\begin{aligned}` when a
single-equation wrapper is chosen (and `gather` → `gathered`, `multline` → `gathered`), and an
`aligned`/`split` block inside an `equation` is unwrapped when `align` is chosen.
`eqnarray`'s `a &=& b` rows become `a &= b` in `align`/`aligned`, a `split` becomes `aligned`
in `$…$` / `\(…\)`, and a note appears when `\tag` / `\label` commands end up where
LaTeX rejects them (per-line ones inside `aligned`, any `\tag` in `$…$`, …).
LaTeX → LaTeX never re-renders the equation; only the delimiters change.

## Command line

```
python -m equation_tool convert -f latex -t omml [--mode display|inline] [--wrapper align] [FILE]
python -m equation_tool copy  [-f latex] [FILE]          # → clipboard, ready to paste into Office
python -m equation_tool paste [-t latex] [--wrapper W]   # equation copied in Office → stdout
python -m equation_tool version                          # pandoc + embedded texmath version
```
`FILE` defaults to stdin. Input and output are UTF-8 (also through pipes and redirection); an input
file may also be UTF-16 with a BOM (what `>` writes in Windows PowerShell 5.1).
Errors (e.g. texmath parse errors, with the caret diagram) go to stderr with exit code 1.

## Keeping texmath up to date

pandoc is statically linked against texmath, so the texmath version you run is the one the
pandoc release was built with - it cannot be updated separately. When a texmath release
carries a fix you care about, upgrade pandoc once a pandoc release built against it exists
(`winget upgrade JohnMacFarlane.Pandoc`); the two are maintained together and pandoc releases
normally follow within days or weeks.

The right-hand pane of the status bar, the Help window (`?` / F1) and
`python -m equation_tool version` show the pandoc and embedded texmath versions; the latter
two also print which `pandoc.exe` is in use. Check that path before upgrading: a copy bundled
with `build_exe.ps1 -BundlePandoc` or selected through `EQUATION_TOOL_PANDOC` takes precedence
over the installed pandoc and is not touched by `winget upgrade` - rebuild with `-BundlePandoc`
(or replace that copy) and restart the app. A Scoop shim on `PATH` is followed to the real
executable when reading the texmath version and when bundling. The current directory is never
searched for `pandoc.exe`, and an `EQUATION_TOOL_PANDOC` that names no file is an error.

## Building the exe

```powershell
.\build_exe.ps1                     # creates .venv, installs PyInstaller, builds dist\EquationTool\
.\build_exe.ps1 -Shortcut           # + Start-menu shortcut   (-DesktopShortcut for the desktop)
.\build_exe.ps1 -BundlePandoc       # + copies pandoc.exe into dist\EquationTool\ (self-contained)
```

## Tests

```
python -m pytest tests            # unit + pandoc round trips + headless GUI (needs pandoc, a desktop)
python tests\office_smoke.py      # optional: real Word + PowerPoint via COM (needs pywin32, Office)
```
The Office smoke test pastes into hidden Word/PowerPoint instances, copies the equation back,
converts it to LaTeX and closes everything without saving. Both tests overwrite the clipboard.

## Layout

```
equation_tool/
  pandoc_engine.py   find/run pandoc; read latex|mathml|omml → Equation(tex, display); write mathml|omml|eqn|typst
  latex_wrap.py      delimiter / environment detection and re-wrapping for LaTeX output
  convert.py         convert(text, src, dst, mode, wrapper) used by GUI and CLI
  clipboard.py       Win32 clipboard via ctypes: "MathML" format in/out
  app.py             tkinter GUI
  __main__.py        GUI / CLI entry
```

## Known texmath behaviour (same as the website)

* Office → LaTeX output is texmath's rendering style: `\sum\limits_{i}`, `\widehat{x}`,
  `\parallel` for `\|`, `\rightarrow` for `\to`, `\operatorname{f}` → `{f}`, extra braces.
* In the OMML *text* output, `gather`, `multline`, `eqnarray`, `flalign` become an OMML matrix
  (`<m:m>`); only `align`-style arrays become an equation array (`<m:eqArr>`). (The clipboard
  route to Office also makes `eqnarray` an equation array, see above.)
* Unsupported macros stop the conversion with texmath's parse error shown in the output pane.
