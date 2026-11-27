# MathShuttle

Move equations between LaTeX and Word / PowerPoint, both ways, offline. Copy LaTeX (with your
paper's macros) and paste it into Office as a native, editable equation; copy an equation in
Office and get LaTeX you can put in a paper. MathShuttle also converts between **LaTeX, MathML
and OMML** (Office Math), with eqn and Typst output, as a local stand-in for the
[texmath web demo](https://johnmacfarlane.net/texmath.html). Windows and macOS (see *macOS* below),
Python standard library.

The conversion engine *is* texmath: it runs inside [pandoc](https://pandoc.org) (same author),
which is the only runtime dependency besides Python's standard library.

```
From: LaTeX | MathML | OMML          To: LaTeX | MathML | OMML | eqn | Typst
```

### Next to Office's own LaTeX support

Microsoft 365 (Version 2606 and later) converts LaTeX pasted with `$…$` / `\[…\]` delimiters
into an equation itself. What this tool adds:

* **Your macros.** Word's paste rejects `\newcommand` and `\DeclareMathOperator`; the tool reads
  your papers' macro files (`math_commands.tex`, a preamble) once and expands them in every
  equation (see *Macros* below).
* **LaTeX from Office that you can put in a paper** (`\mathrm{OOD}`, `\operatorname{softmax}`,
  `\boldsymbol{\theta}`, `\binom`, `cases`, `a &= b`), without opening Word's equation options.
* MathML, OMML, Typst and eqn output; a command line; Office versions without LaTeX paste
  (Office 2021 / 2024 LTSC, older Mac Office).

On a 784-equation PhD thesis (Word 16.0.20527): Word's own paste turned 781 into equations;
the tool converts all 784, and 764 of them come back from Word as the same symbols and fonts.

## Quick start

1. Install **pandoc**, the conversion engine: `winget install JohnMacFarlane.Pandoc` (Windows) or
   `brew install pandoc` (macOS).
2. Download the app from the [Releases page](https://github.com/infinitr0us/MathShuttle/releases):
   `MathShuttle-<version>-windows-x64.zip` (unzip it anywhere, run `MathShuttle\MathShuttle.exe`) or
   `MathShuttle-<version>-macos-arm64.zip` (drag `MathShuttle.app` into Applications; see *macOS*
   below for the first start). No Python needed.
3. Type or paste LaTeX, click **Copy for Word/PowerPoint**, and press Ctrl+V (Cmd+V) in Word or
   PowerPoint: the equation arrives as a native, editable equation. For the other way, copy an
   equation in Word and click **Paste from Word/PowerPoint**.

Other ways to run it:

* `pip install git+https://github.com/infinitr0us/MathShuttle` (or `pipx install ...`): the commands
  `mathshuttle` (the window without arguments, the CLI with them) and `mathshuttle-gui` (the window
  without a console window on Windows). No other Python packages are needed.
* From a clone: `MathShuttle.pyw` (double-click; any Python ≥ 3.9 with tkinter) or
  `python -m mathshuttle` (the same window; with arguments it becomes a CLI, see below).
* Build the app yourself: `.\build_exe.ps1` gives `dist\MathShuttle\MathShuttle.exe` (add `-Shortcut`
  for a Start-menu entry); `./build_app.sh` gives `dist/MathShuttle.app` on the Mac.

Requirement: **pandoc** (`winget install JohnMacFarlane.Pandoc`). The app looks for
`pandoc.exe` next to itself, on `PATH`, then in `%LOCALAPPDATA%\Pandoc`; set
`MATHSHUTTLE_PANDOC` to point at a specific copy.

## macOS

The same code runs on macOS: the window, every conversion, the command line, **Open file…**, the LaTeX
preview and the tests (checked on macOS 27.2 with Python 3.12 / Tk 9.0 and Python 3.9 / Tk 8.6, pandoc
3.14 from Homebrew, MacTeX 2026). Install pandoc with `brew install pandoc`, then either

* `MathShuttle.app`: the standalone app (no Python needed), from the release zip or built with
  `./build_app.sh` (see *Building the app* below); drag it into Applications. It is not signed by a
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

**The hotkey** on the Mac is Control+Option+Shift+M (see *Without the window* below), registered with
Carbon's `RegisterEventHotKey`, which needs no Accessibility permission; Cmd+Shift+C / Cmd+Shift+V
work while the MathShuttle window is in front.

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

Office pastes one equation at a time. With several equations in the input, **Copy for
Word/PowerPoint** opens a menu of them; its first item, *Next*, is the one after the equation
you copied last, so you can paste a paper's equations into a slide one after the other (keys: N
or the number of one of the first nine; on the Mac, then Return). On the command line: `copy --index N`.

**Office → LaTeX.** Select an equation in Word or PowerPoint, press Ctrl+C, then click
**Paste from Word/PowerPoint** (Ctrl+Shift+V): the tool reads the MathML that Office put on the
clipboard and converts it (to LaTeX by default; any other target works too). Equation arrays
that Office exports as a bare `<mtable>` are recognised and come back as `aligned`, not `matrix`
(continuation lines such as `&+ c` included). When Word copies no MathML (a sentence with
equations in it selected, or on the Mac lines aligned at `=`), the equations are read from the
HTML that Word copies along, where they are stored as OMML. Lines made with Shift+Enter and lined up
with *Align at =* come back as one `aligned` block (without *Align at* they are separate equations,
as Word stores them); so does an equation with manual line breaks (*Insert Manual Break*, whose
lines Windows Word's MathML puts on one line), lined up where Word lines them up. Also from a `.docx`.

With **Tidy LaTeX** (on by default; `--no-tidy` on the command line), the LaTeX is what an author
would write rather than texmath's literal rendering of Office's structure:

| Word holds | texmath alone | Tidy LaTeX |
|---|---|---|
| upright `OOD` (`\rm`, `\mathrm`) | `P_{OOD}` (italic) | `P_{\mathrm{OOD}}` |
| a function name | `{softmax}(z)` (reads back as s·o·f·t·m·a·x) | `\operatorname{softmax}(z)` |
| bold italic θ, w | `\mathbf{θ}` (pdfLaTeX rejects it), `\mathbf{w}` | `\boldsymbol{\theta}`, `\boldsymbol{w}` |
| a binomial | `\left( \frac{n}{k} \right)` | `\binom{n}{k}` |
| an aligned block | `a & {= b}` (the `=` loses its spacing) | `a & = b` |
| cases | `\left\{ \begin{matrix} … \right.` | `\begin{cases} … \end{cases}` |
| an arrow labelled above and below | `\underset{a}{\overset{b}{\rightarrow}}` | `\xrightarrow[a]{b}` |
| symbols such as ≔, ✓ and ℏ | `≔`, `✓`, `\hslash` | `\coloneqq`, `\checkmark`, `\hbar` |
| ℓ, set minus, ∅, bold numbers | `\mathcal{l}`, `\backslash`, `\varnothing`, `\mathbf{2}\mathbf{5}.\mathbf{2}` | `\ell`, `\setminus`, `\emptyset`, `\mathbf{25.2}` |

Tidy drops redundant `\limits` on display operators such as `\sum`, `\max` and `\lim`, empty scripts
(`x_{i}^{}`), and the `~` for a space typed after the last symbol in Word. Each result is checked to
convert again; if a clean-up would break an equation, texmath's own LaTeX is kept.

What Word itself loses (checked with Word / PowerPoint 16.0.20527, so no tool can bring it back from
the clipboard): its MathML import turns every **bold Latin letter bold italic** (`\mathbf{x}` comes
back as `\boldsymbol{x}`; Word's *own* LaTeX paste keeps it upright), stores an operator name as
upright text, not as a function (`\operatorname{f}` comes back as `\mathrm{f}`), and drops `\tag`
numbers (the tool says so when it copies one). It also makes every MathML space (`<mspace>`) one
ordinary space, so *Copy for Word/PowerPoint* gives `\,`, `\;`, `\quad`, `\qquad` as the Unicode
spaces Office keeps (thin, thick, em space), and they come back as the same commands; `\!` is left
out (Office has no negative space).

**LaTeX preview.** With a LaTeX installation on `PATH` (MiKTeX or TeX Live: `latex` and `dvipng`),
a strip above the buttons shows the equation as LaTeX sets it: what you typed when converting from
LaTeX, the LaTeX that comes back when converting from Office. It is drawn in the background after
each conversion (about half a second), with your macros, `amsmath`, `amssymb`, `mathtools`, `bm`
(and `bbm`, `dsfont` when installed); a LaTeX error is shown instead of a picture. Untick
*LaTeX preview* to hide it.

**Without the window: the hotkey.** While MathShuttle runs (minimised is fine), **Ctrl+Alt+Shift+M**
in any program shuttles the clipboard: an equation copied in Word / PowerPoint becomes LaTeX text,
and LaTeX text becomes an equation to paste with Ctrl+V. So: copy LaTeX in your editor, press the
hotkey, paste in PowerPoint; or copy an equation in Word, press the hotkey, paste in Overleaf. A
small note at the bottom right of the screen says what happened (it does not take the focus). The
key is the `hotkey` entry of `%APPDATA%\MathShuttle\settings.json`; the checkbox in the window turns
it off. Ctrl+Alt+Shift keeps clear of Word's own Ctrl+Alt shortcuts. On the Mac the same entry (in
`~/Library/Application Support/MathShuttle/settings.json`) gives Control+Option+Shift+M: Alt there
is Option, Cmd is Command.

**Whole documents.** **Open file…** lists every equation of a `.docx`, `.pptx`, `.tex` or `.md` file
with where it is (the heading above it, the slide and its title, the `\label`) and the sentence
around it. From the list, use one as input, copy it for Word / PowerPoint or as LaTeX, or export
them all to a `.tex` file, or to a Word document (`.docx`) holding each as a native equation under
the line that says where it was, for a collaborator who edits them in Word; on the command line,
`python -m mathshuttle extract FILE [-t FORMAT] [--docx OUT]`.
Office equations go through the same Tidy LaTeX as a pasted one. A `.tex` file is read with its
`\input` / `\include` files, and its own definitions apply to its equations without a macro file:
those pandoc expands itself and those it does not (`\DeclarePairedDelimiter`, xparse's
`\NewDocumentCommand`, the `\foreach \x in {A,...,Z}` loop that defines `\calA`...`\calZ`), also from
a `.sty` next to it. With the `.aux` file LaTeX wrote next to it, `\eqref{eq:loss}` inside an equation
shows its number, (2.1). When pandoc cannot read the preamble at all (a conference style file it trips
over), the body is read alone, with the preamble's definitions, and a note says so. An equation that
still cannot be converted is listed with its reason; in the Word export it is kept as LaTeX text
under its line, with the reason. Equations made with the old Equation Editor 3.0 or MathType (objects
without Office Math inside) cannot be read; their number is reported.

**A passage or a whole paper as a Word file.** **Save as Word...** writes the input, a chat answer,
Markdown notes or a piece of LaTeX, as a Word document: its prose, headings, lists, tables and code as
they are, and each equation a native Word equation, made as *Copy for Word/PowerPoint* makes it (your
macros, the built-in packs). On the command line, `mathshuttle word FILE -o OUT.docx` does the same for a
passage, and for a whole `.tex` or `.md` file it writes the document: with the paper's own definitions,
and with the `.aux` file LaTeX wrote next to it, each numbered equation's number at the right margin as
Word numbers equations itself (`\tag{..}` too), and `\eqref`, `\ref` and numeric `\cite` showing their
numbers. An equation that cannot be converted stays LaTeX text, followed by the reason. The rest is
pandoc's reading of the paper: figures, tables and sections as pandoc converts them, and a command
pandoc does not know is left out, as in pandoc's own conversions.

MathML input may also come from elsewhere: prefixed elements (`<mml:math>`, as written by
Word's XSLT, MathType or JATS journal XML), named entities (`&InvisibleTimes;`, from
Mathematica / Maple), several documents each with its own XML declaration. MathML that carries its
TeX source as an annotation (MathType, pages rendered with KaTeX or MathJax, the *TeX annotation*
option) is converted from what it shows; if the annotation shows other symbols, the equation was
edited after the TeX was written, and a note says so.

Tip: LaTeX **→** OMML text output is still there for people who work with the XML directly
(`document.xml`, python-docx, …); for pasting into Office use the clipboard route above, since
raw OMML cannot be pasted.

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
separated by a blank line; *Copy for Word/PowerPoint* lets you pick one, see above).

**A chat answer or Markdown notes** can be pasted as they are. Text that LaTeX source would not
contain (`50% of`, `$5 and`, `` `code` ``, `**bold**`, a `#` heading or a code fence, outside
`%` comment lines) is read as Markdown: `%` is a percent sign there, `$5` is money, code spans hold
no maths, and the equations are those in `$…$`, `$$…$$`, `\(…\)`, `\[…\]` and environments such as
`\begin{align}` written straight into the text. So is text that the LaTeX reader cannot read at all
(unpaired `$`). The text is read both ways, though: if the Markdown reading misses maths that the
LaTeX one finds (`$ a + b $` with spaces, which Markdown does not take as maths), the text was LaTeX
after all. The status bar says when the Markdown reading was used. `.md` files are read as pandoc's
Markdown, with environments written into the text; there `\(…\)` and `\[…\]` are escaped brackets
(pandoc itself writes `[1]` as `\[1\]`), not maths.

**LaTeX that texmath does not know** is rewritten before conversion (the LaTeX output keeps what
you wrote): plain-TeX font switches (`{\rm OOD}`, `\rm{OOD}`, `\bf`, `\it`, `\cal`, `\sf`, `\tt`,
scoped like TeX does), `\notag`, `\def` / `\let`, `\mathbbm`, `\Bbb`, `\frak`, `dcases`, `rcases`,
`cases*` / `dcases*` (text column), `matrix*` (and `pmatrix*`…), `\tbinom` / `\dbinom` /
`\nicefrac` (plain size), `\textcolor`, `\smash`, `\vphantom`, `\mathclap`, and standard LaTeX that
texmath 0.13 rejects: `\hspace` in mm or ex, `\kern`, `\mkern`, `\text{a \textbf{b}}`, `\textnormal`,
`\cfrac`, `\intertext` (a row of text), `\sideset`, `\varlimsup` / `\varliminf` / `\varinjlim`,
`\operatornamewithlimits`, `\bigm|`, `\widetilde\mathcal K`, `aligned[t]`, booktabs rules in an
`array`, `\qedhere`, `\eqref` / `\ref` / `\cite` (as text). Rewrites that lose something (a colour, a
strut, the size of a fraction) are noted in the status bar.

**Built-in packs.** Commands of packages that papers load and texmath does not know are written out as
the package defines them, unless your macros or the paper define them: **physics** (`\ket`, `\bra`,
`\braket`, `\mel`, `\expval`, `\dv`, `\pdv`, `\comm`, `\qty(...)`, `\abs`, `\norm`, `\vb`, `\vu`,
`\order`, `\eval`, `\grad`, `\curl`, `\laplacian`, `\mqty`, `\Tr`...; its `\div` only when the paper
loads physics, since `\div` is ÷ in LaTeX), **braket** (`\Bra`, `\Ket`, `\Braket`, `\set`, `\Set`),
**slashed**, **tensor** (`\tensor{T}{^a_b}`), AASTeX's `\farcs`, and siunitx quantities whose unit is
written as letters (`\qty{12}{kHz}`, set upright as LaTeX does). A note names the commands written so.

**Errors in plain words.** When an equation still cannot be converted, the first line says why:
which command texmath does not know (with a "did you mean `\alpha`?" for a typo, and where to define
your own macro), a `{` that is never closed, a `tikzcd` diagram (which is no equation); with several
equations in the input, which one. texmath's own message follows below it.

Also corrected: texmath 0.13 writes `\mathbf{x}`
as bold *italic*, `\|` and a sized `\lVert` / `\rVert` (`\left\lVert`, so also a `\norm{x}` from
`\DeclarePairedDelimiter`) as "parallel to" (U+2225, not the norm bar U+2016), `\setminus` as a
backslash and `\varnothing` as a diameter sign.

### Macros

**Macros…** (or `--macros FILE` on the command line, repeatable) takes your papers' macro files
(`math_commands.tex`, a preamble) plus definitions typed in the dialog. Read from them:
`\newcommand` / `\renewcommand` / `\providecommand` (with arguments and an optional default),
`\def` (also with delimited parameters), `\let`, `\DeclareMathOperator(*)`,
`\DeclarePairedDelimiter` (with `\abs*{x}` and `\abs[\big]{x}`) and `\DeclarePairedDelimiterX`,
xparse's `\NewDocumentCommand` (mandatory arguments and a first optional one), and the `\foreach`
alphabet loop (`\csname cal\x\endcsname`); when one name is defined twice, the later definition
holds; anything else in the file
(`\usepackage`, text) is ignored, and a definition that is never closed is reported. The macros an
equation uses (and those they use) are expanded by pandoc's LaTeX reader, which follows TeX
(`\let` copies the definition it sees, a font switch in a macro acts where the macro is used),
so one bad macro in a 300-line file does not break the others; a definition in the input itself
wins over the file. Files are re-read when they change.

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

`mathshuttle` opens the window when run without arguments. Use `mathshuttle --help` for commands
and examples, or `mathshuttle extract --help` for the options of one command. From a source
checkout, use `python -m mathshuttle` in place of `mathshuttle`.

```
mathshuttle convert -e 'x^2' -t mathml
mathshuttle copy -e 'x^2'
mathshuttle paste --wrapper bare
mathshuttle extract paper.tex -t typst
mathshuttle --version
```

* `convert` writes equations to standard output. The default input is LaTeX (`-f latex`).
  The default output is OMML text (`-t omml`) for working with Office XML. Use `copy` for pasting
  into Word or PowerPoint. Use `--annotate` to include the TeX source in MathML output.
  Conversion errors go to standard error and set exit code 1.
* `convert` and `copy` take a file name or a literal expression with `-e TEXT` (`--expr TEXT`).
  Omit both to read standard input. When typing at the keyboard, finish on a new line with
  Ctrl+Z then Enter on Windows or Ctrl+D on macOS and Linux. Input and output use UTF-8,
  including in pipes. Input files may also use UTF-16 with a BOM.
* `copy --index N` puts equation N on the clipboard. The default is the first equation.
  `paste` reads an equation copied in Office and writes it to standard output as LaTeX by default.
  Use `--mode inline` or `--mode display` with `convert`, `copy` or `paste` to override the detected mode.
* `extract FILE` writes the equations from a `.docx`, `.pptx`, `.tex` or `.md` document in order,
  each with a location comment. The default output is LaTeX. With `-t mathml`, `omml`, `typst`
  or `eqn`, a failed equation gets a comment explaining the failure, and extraction continues.
  Standard error lists the failed equations by number and location and reports the count.
  The exit code is 1 if any equation failed or 0 on success. Use `--docx OUT` to also write
  the equations to a Word document.
* `--macros FILE` loads LaTeX definitions for `convert`, `copy` and `extract` and can be repeated.
  `--wrapper` sets the delimiters or environment for LaTeX output. With `convert` or `paste`,
  `--no-tidy` keeps texmath's original LaTeX from MathML or OMML.
* `word FILE -o OUT.docx` (or `-e TEXT`, or standard input) writes a passage, or a whole `.tex` / `.md`
  document, as a Word file with native equations (see *A passage or a whole paper as a Word file*).
* `--version` and `version` both show the MathShuttle, pandoc and texmath versions and the pandoc path.

## Keeping texmath up to date

pandoc is statically linked against texmath, so the texmath version you run is the one the
pandoc release was built with - it cannot be updated separately. When a texmath release
carries a fix you care about, upgrade pandoc once a pandoc release built against it exists
(`winget upgrade JohnMacFarlane.Pandoc`); the two are maintained together and pandoc releases
normally follow within days or weeks.

The right-hand pane of the status bar, the Help window (`?` / F1) and
`python -m mathshuttle version` show the pandoc and embedded texmath versions; the latter
two also print which `pandoc.exe` is in use. Check that path before upgrading: a copy bundled
with `build_exe.ps1 -BundlePandoc` or selected through `MATHSHUTTLE_PANDOC` takes precedence
over the installed pandoc and is not touched by `winget upgrade` - rebuild with `-BundlePandoc`
(or replace that copy) and restart the app. A Scoop shim on `PATH` is followed to the real
executable when reading the texmath version and when bundling. The current directory is never
searched for `pandoc.exe`, and an `MATHSHUTTLE_PANDOC` that names no file is an error.

## Building the exe

```powershell
.\build_exe.ps1                     # creates .venv, installs PyInstaller, builds dist\MathShuttle\
.\build_exe.ps1 -Shortcut           # + Start-menu shortcut   (-DesktopShortcut for the desktop)
.\build_exe.ps1 -BundlePandoc       # + copies pandoc.exe into dist\MathShuttle\ (self-contained, see Licence)
```

### Building the app (macOS)

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

### What Word does with the clipboard (16.0.20527)

Word pastes from the plain-text clipboard format, not from the registered `MathML` formats that
Microsoft documents as taking priority: with the text `x` next to the MathML it inserts the text x,
and with LaTeX there it runs its own LaTeX import. So *Copy for Word/PowerPoint* puts the MathML
in the plain-text format too. Equation arrays need `columnspacing="0em"` (how Word exports them),
otherwise a table pastes as a centred matrix.

## Layout

```
mathshuttle/
  pandoc_engine.py   find/run pandoc; read latex|mathml|omml → Equation(tex, display); write mathml|omml|eqn|typst
  latex_wrap.py      delimiter / environment detection and re-wrapping for LaTeX output
  compat.py          LaTeX texmath does not take → LaTeX it does; macro files
  packs.py           built-in packs: physics, braket, slashed, tensor, siunitx literal units, AASTeX
  diagnose.py        texmath's parse errors in plain words (unknown command, unclosed brace...)
  tidy.py            texmath's LaTeX from MathML / OMML → the LaTeX an author writes
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

## Known texmath behaviour (same as the website)

* Office → LaTeX output follows texmath's style where *Tidy LaTeX* has no rule: `\widehat{x}`,
  `\rightarrow` for `\to`, `\left( … \right)` for Word's (always stretchy) brackets, extra braces
  around sub-expressions.
* In the OMML *text* output, `gather`, `multline`, `eqnarray`, `flalign` become an OMML matrix
  (`<m:m>`); only `align`-style arrays become an equation array (`<m:eqArr>`). (The clipboard
  route to Office also makes `eqnarray` an equation array, see above.)
* Unsupported macros stop the conversion with texmath's parse error shown in the output pane
  (define them in *Macros…*).

## Licence

MIT, see [LICENSE](LICENSE). pandoc (which contains texmath) is a separate program under the GPL
(version 2 or later); this tool only runs it, and you install it yourself. A build made with
`build_exe.ps1 -BundlePandoc` contains `pandoc.exe`: if you pass such a build on, the GPL applies
to that copy of pandoc (include its licence and offer its source code).
