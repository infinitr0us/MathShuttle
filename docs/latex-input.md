# LaTeX input and output

## Modes and delimiters

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
separated by a blank line; *Copy for Word/PowerPoint* lets you pick one, see
[Word and PowerPoint](word-and-powerpoint.md)).

**Mode** = Auto keeps the detected mode; Display / Inline override it (affects `display="block"`
in MathML, `<m:oMathPara>` vs `<m:oMath>` in OMML, `$ x $` vs `$x$` in Typst, `.EQ` vs inline eqn).

## Chat answers and Markdown notes

A chat answer or Markdown notes can be pasted as they are. Text that LaTeX source would not
contain (`50% of`, `$5 and`, `` `code` ``, `**bold**`, a `#` heading or a code fence, outside
`%` comment lines) is read as Markdown: `%` is a percent sign there, `$5` is money, code spans hold
no maths, and the equations are those in `$…$`, `$$…$$`, `\(…\)`, `\[…\]` and environments such as
`\begin{align}` written straight into the text. So is text that the LaTeX reader cannot read at all
(unpaired `$`). The text is read both ways, though: if the Markdown reading misses maths that the
LaTeX one finds (`$ a + b $` with spaces, which Markdown does not take as maths), the text was LaTeX
after all. The status bar says when the Markdown reading was used. `.md` files are read as pandoc's
Markdown, with environments written into the text; there `\(…\)` and `\[…\]` are escaped brackets
(pandoc itself writes `[1]` as `\[1\]`), not maths.

## LaTeX that texmath does not know

LaTeX that texmath does not know is rewritten before conversion (the LaTeX output keeps what
you wrote): plain-TeX font switches (`{\rm OOD}`, `\rm{OOD}`, `\bf`, `\it`, `\cal`, `\sf`, `\tt`,
scoped like TeX does), `\notag`, `\def` / `\let`, `\mathbbm`, `\Bbb`, `\frak`, `dcases`, `rcases`,
`cases*` / `dcases*` (text column), `matrix*` (and `pmatrix*`…), `\tbinom` / `\dbinom` /
`\nicefrac` (plain size), `\textcolor`, `\smash`, `\vphantom`, `\mathclap`, and standard LaTeX that
texmath 0.13 rejects: `\hspace` in mm or ex, `\kern`, `\mkern`, `\text{a \textbf{b}}`, `\textnormal`,
`\cfrac`, `\intertext` (a row of text), `\sideset`, `\varlimsup` / `\varliminf` / `\varinjlim`,
`\operatornamewithlimits`, `\bigm|`, `\widetilde\mathcal K`, `aligned[t]`, booktabs rules in an
`array`, `\qedhere`, `\eqref` / `\ref` / `\cite` (as text). Rewrites that lose something (a colour, a
strut, the size of a fraction) are noted in the status bar.

## Built-in packs

Commands of packages that papers load and texmath does not know are written out as
the package defines them, unless your macros or the paper define them: **physics** (`\ket`, `\bra`,
`\braket`, `\mel`, `\expval`, `\dv`, `\pdv`, `\comm`, `\qty(...)`, `\abs`, `\norm`, `\vb`, `\vu`,
`\order`, `\eval`, `\grad`, `\curl`, `\laplacian`, `\mqty`, `\Tr`...; its `\div` only when the paper
loads physics, since `\div` is ÷ in LaTeX), **braket** (`\Bra`, `\Ket`, `\Braket`, `\set`, `\Set`),
**slashed**, **tensor** (`\tensor{T}{^a_b}`), AASTeX's `\farcs`, and siunitx quantities whose unit is
written as letters (`\qty{12}{kHz}`, set upright as LaTeX does). A note names the commands written so.

## Errors in plain words

When an equation still cannot be converted, the first line says why:
which command texmath does not know (with a "did you mean `\alpha`?" for a typo, and where to define
your own macro), a `{` that is never closed, a `tikzcd` diagram (which is no equation); with several
equations in the input, which one. texmath's own message follows below it.

## Symbols texmath gets wrong

texmath 0.13 writes `\mathbf{x}` as bold *italic*, `\|` and a sized `\lVert` / `\rVert`
(`\left\lVert`, so also a `\norm{x}` from `\DeclarePairedDelimiter`) as "parallel to" (U+2225, not
the norm bar U+2016), `\setminus` as a backslash and `\varnothing` as a diameter sign. MathShuttle
corrects these. They are reported to texmath ([#307](https://github.com/jgm/texmath/issues/307),
fixed for the next pandoc release; [#312](https://github.com/jgm/texmath/issues/312),
[#313](https://github.com/jgm/texmath/issues/313)).

## Macros

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

## Expand macros: LaTeX for a place without your macro file

With **Expand macros** ticked (LaTeX to LaTeX; `--expand-macros` for `convert` and `extract` on the
command line), the LaTeX output has your macros written out: those of your macro files, of the input
and, for a document, its own definitions, so that the equation can go where your macro file is not: a
web page with KaTeX or MathJax, a chat, a journal's submission form, a co-author's document.
`\DeclareMathOperator` names become `\operatorname{...}` (`\operatorname*` for the starred form),
`\DeclarePairedDelimiter` pairs their delimiters, and commands of the built-in packs (physics, braket,
siunitx...) standard LaTeX. Everything else stays as you wrote it: `\label`, `\tag`, spacing, the
environment. A macro used as a bare argument (`\widetilde\K`) gets braces, so it stays one argument
(`\widetilde{\mathcal K}`). A macro made of LaTeX's internal commands (`\@namedef`, `\global`,
`\expandafter`, as in some style files) is kept as written, and a note names it: written out without
`\makeatletter` it would break. The status bar lists the macros written out.
LaTeX → LaTeX never re-renders the equation; only the delimiters change.

## MathML and OMML input

MathML input may also come from elsewhere: prefixed elements (`<mml:math>`, as written by
Word's XSLT, MathType or JATS journal XML), named entities (`&InvisibleTimes;`, from
Mathematica / Maple), several documents each with its own XML declaration. MathML that carries its
TeX source as an annotation (MathType, pages rendered with KaTeX or MathJax, the *TeX annotation*
option) is converted from what it shows; if the annotation shows other symbols, the equation was
edited after the TeX was written, and a note says so.

LaTeX → OMML text output is there for people who work with the XML directly (`document.xml`,
python-docx, …). For pasting into Office use *Copy for Word/PowerPoint* (see
[Word and PowerPoint](word-and-powerpoint.md)), since raw OMML cannot be pasted.
