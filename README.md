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

**Office → LaTeX.** Select an equation in Word or PowerPoint, press Ctrl+C, then click
**Paste from Word/PowerPoint** (Ctrl+Shift+V): the tool reads the MathML that Office put on the
clipboard and converts it (to LaTeX by default; any other target works too). Equation arrays
that Office exports as a bare `<mtable>` are recognised and come back as `aligned`, not `matrix`.

Tip: LaTeX **→** OMML text output is still there for people who work with the XML directly
(`document.xml`, python-docx, …); for pasting into Office the clipboard route above is the one
to use — raw OMML cannot be pasted.

## LaTeX input: modes and delimiters

Everything pandoc's LaTeX reader understands is accepted, in any mix:

| input | detected mode |
|---|---|
| bare `\frac{a}{b}` | **Mode** box (Auto = display) |
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
LaTeX → LaTeX never re-renders the equation; only the delimiters change.

## Command line

```
python -m equation_tool convert -f latex -t omml [--mode display|inline] [--wrapper align] [FILE]
python -m equation_tool copy  [-f latex] [FILE]          # → clipboard, ready to paste into Office
python -m equation_tool paste [-t latex] [--wrapper W]   # equation copied in Office → stdout
```
`FILE` defaults to stdin. Errors (e.g. texmath parse errors, with the caret diagram) go to stderr
with exit code 1.

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
* `gather`, `multline`, `eqnarray`, `flalign` become an OMML matrix (`<m:m>`); only `align`-style
  arrays become an equation array (`<m:eqArr>`).
* Unsupported macros stop the conversion with texmath's parse error shown in the output pane.
