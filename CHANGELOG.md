# Changelog

What changed in each release of MathShuttle. The release pages on GitHub have the download files,
their SHA-256 sums and what each release was tested with.

## 0.8.0 (2026-11-28)

### New
- No terminal needed for pandoc: when it is not installed, the window offers to download it (pandoc's own
  release from GitHub, its size and SHA-256 checked, installed for you only), and so does
  `mathshuttle get-pandoc` on the command line.
- A Windows 11 look, light or dark as the system is set (the Sun Valley theme), with a calmer layout;
  the Mac keeps its own look.
- *Expand macros*: LaTeX output with your macros, the input's and the document's definitions and the
  built-in packs written out, for a place without your macro file: a web page, a chat, a submission form
  (`--expand-macros` for `convert` and `extract`).
- Help > *Copy details for a bug report*, issue templates, a shorter README with the reference in
  `docs/`, and this changelog.
- `pipx install mathshuttle` (on PyPI from this release on).
- Tests run on GitHub Actions on Windows, macOS and Linux for every change.

### Fixed
- In `.md` files, `\DeclarePairedDelimiter` definitions now apply to the equations.
- A macro named by one character (`\newcommand{\1}{\mathbf 1}`) is now read from macro files.

## 0.7.0 (2026-11-28)

### New
- A passage or a whole paper as a Word file: *Save as Word...* writes the input (a chat answer,
  Markdown notes, LaTeX) as a Word document with native equations; *Whole file as Word...* (or
  `mathshuttle word paper.tex -o paper.docx`) writes a whole `.tex` or `.md` paper with its title,
  authors, abstract, figures and own definitions.
- Equation numbers as LaTeX gives them, at the right margin, counted as LaTeX counts them and checked
  against the `.aux` file; `\eqref`, `\ref`, `\cref`, `\Cref`, `\autoref` and citations show what LaTeX
  prints.
- Your own LaTeX back: an equation copied with MathShuttle that returns from Word or PowerPoint unedited
  comes back as the LaTeX you wrote (macros, `\label`, spacing).
- Open file at document size: a filter, *Display only*, *Distinct only*, and the `.aux` numbers;
  `extract --display --unique --match`; one pandoc run for all equations of a document.

### Fixed
- A negative space as the base of a script (`e\!^{-x}`) gave Word incomplete MathML since 0.6.1.
- Word export details found by the reviews: tags with maths in them, footnotes, `subequations`,
  commented-out tags, raw LaTeX environments in Markdown.

## 0.6.1 (2026-11-27)

### Fixed
- Commas between brackets: Word made a comma or semicolon between brackets the separator of one bracket
  group and got nested groups wrong (`\max(\cos(a,b), 0)` showed as max(cos,(a,b)0)).
- Spaces: Word turned every space of a pasted equation into one ordinary space. `\,`, `\;`, `\quad` and
  `\qquad` now go to Office as the Unicode spaces Word keeps, and come back as the same commands.

## 0.6.0 (2026-11-27)

### New
- A paper's own definitions apply when you open it, also those pandoc does not apply itself
  (`\DeclarePairedDelimiter`, the `\foreach` alphabet loop), from the file, its `\input`s and a `.sty`
  next to it; the body alone is read when pandoc trips over a style file.
- Built-in packs: commands of physics, braket, slashed, tensor, AASTeX and siunitx are written out as
  their packages define them, unless you define them.
- Standard LaTeX that texmath rejected converts: `\hspace` in mm, `\kern`, `\text{a \textbf{b}}`,
  `\cfrac`, `\intertext`, `\sideset`, `\varlimsup`, `aligned[t]`, `\NewDocumentCommand` and more.
- Errors in plain words: the unknown command and a "did you mean", an unclosed brace, which equation.
- `-e TEXT`, `--version`, help texts with examples.

On 20 recent arXiv papers, the equations that could not be converted went from 829 of 9388 to 16 of 9550.

## 0.5.0 (2026-11-27)

### New
- Chat answers and Markdown notes can be pasted as they are; `.md` files are read the same way.
- Export a document's equations as a Word file (`extract FILE --docx OUT`).
- Install with pip: `mathshuttle` and `mathshuttle-gui`.

### Fixed
- Sized norm bars (`\left\lVert`, `\norm{x}` from `\DeclarePairedDelimiter`) paste into Word with the
  norm bar, not "parallel to".

## 0.4.0 (2026-11-27)

### New
- Pick which equation *Copy for Word/PowerPoint* copies; *Next* is the one after the last copied.
- Lines broken with Shift+Enter or *Insert Manual Break* in Word come back as one `aligned` block.
- The hotkey on the Mac (Control+Option+Shift+M).
- A note when a TeX annotation no longer matches its MathML.

## 0.3.0 (2026-11-27)

### New
- macOS: the whole app as a double-clickable `MathShuttle.app`, and the clipboard exchange with Word and
  PowerPoint for Mac.
- Paste from Word when Word copies no MathML (a sentence with equations; aligned lines on the Mac): the
  equations are read from the HTML Word copies along.

## 0.2.0 (2026-11-22)

First public release: LaTeX to Word and PowerPoint as native equations and back, your macros, Tidy
LaTeX, the hotkey on Windows, Open file for `.docx`, `.pptx`, `.tex` and `.md`, the LaTeX preview, and
conversion between LaTeX, MathML and OMML with eqn and Typst output.
