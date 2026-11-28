# Word and PowerPoint

## LaTeX to Word or PowerPoint

Type or paste LaTeX, click **Copy for Word/PowerPoint** (Ctrl+Shift+C),
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

## Word or PowerPoint to LaTeX

Select an equation in Word or PowerPoint, press Ctrl+C, then click
**Paste from Word/PowerPoint** (Ctrl+Shift+V): the tool reads the MathML that Office put on the
clipboard and converts it (to LaTeX by default; any other target works too). Equation arrays
that Office exports as a bare `<mtable>` are recognised and come back as `aligned`, not `matrix`
(continuation lines such as `&+ c` included). When Word copies no MathML (a sentence with
equations in it selected, or on the Mac lines aligned at `=`), the equations are read from the
HTML that Word copies along, where they are stored as OMML. Lines made with Shift+Enter and lined up
with *Align at =* come back as one `aligned` block (without *Align at* they are separate equations,
as Word stores them); so does an equation with manual line breaks (*Insert Manual Break*, whose
lines Windows Word's MathML puts on one line), lined up where Word lines them up. Also from a `.docx`.

### Tidy LaTeX

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

### What Word itself loses

Checked with Word / PowerPoint 16.0.20527, so no tool can bring it back from the clipboard: Word's
MathML import turns every **bold Latin letter bold italic** (`\mathbf{x}` comes
back as `\boldsymbol{x}`; Word's *own* LaTeX paste keeps it upright), stores an operator name as
upright text, not as a function (`\operatorname{f}` comes back as `\mathrm{f}`), and drops `\tag`
numbers (the tool says so when it copies one). It also makes every MathML space (`<mspace>`) one
ordinary space, so *Copy for Word/PowerPoint* gives `\,`, `\;`, `\quad`, `\qquad` as the Unicode
spaces Office keeps (thin, thick, em space), and they come back as the same commands; `\!` is left
out (Office has no negative space).

## Your own LaTeX back

An equation copied with MathShuttle that comes back from Word or
PowerPoint unedited (pasted, then copied again) comes back as the LaTeX you wrote: your macros,
`\label`, `\tag` and spacing, not the LaTeX rebuilt from Office's MathML, and the status line says
so. This works in the window, with the hotkey and with `mathshuttle paste`. Each *Copy for
Word/PowerPoint* notes what the equation shows and the LaTeX it came from in `copied.json`, next to
`settings.json` (the newest 300 equations, on your computer only). What Word itself changes does
not count as an edit (bold made bold italic, ℓ made 𝓁, accents written with other characters,
brackets made a stretchy pair, spaces: checked on 99 equations pasted into Word 16.0.20527 and copied
back); a changed symbol, accent, script, matrix shape or font does, and then the equation is converted
from Office's MathML as usual. So does an equation you copied twice with different `\label`s, since
which one came back is not known. Recall needs the equation itself selected in Word (a sentence
around it comes back through Word's HTML, converted as usual), and it is off with *Tidy LaTeX* off.
Set the environment variable `MATHSHUTTLE_RECALL_PATH` to another file to keep the notes there, or to
an empty value to turn this off.

## LaTeX preview

With a LaTeX installation on `PATH` (MiKTeX or TeX Live: `latex` and `dvipng`),
a strip above the buttons shows the equation as LaTeX sets it: what you typed when converting from
LaTeX, the LaTeX that comes back when converting from Office. It is drawn in the background after
each conversion (about half a second), with your macros, `amsmath`, `amssymb`, `mathtools`, `bm`
(and `bbm`, `dsfont` when installed); a LaTeX error is shown instead of a picture. Untick
*LaTeX preview* to hide it.

## The hotkey: without the window

While MathShuttle runs (minimised is fine), **Ctrl+Alt+Shift+M**
in any program shuttles the clipboard: an equation copied in Word / PowerPoint becomes LaTeX text,
and LaTeX text becomes an equation to paste with Ctrl+V. So: copy LaTeX in your editor, press the
hotkey, paste in PowerPoint; or copy an equation in Word, press the hotkey, paste in Overleaf. A
small note at the bottom right of the screen says what happened (it does not take the focus). The
key is the `hotkey` entry of `%APPDATA%\MathShuttle\settings.json`; the checkbox in the window turns
it off. Ctrl+Alt+Shift keeps clear of Word's own Ctrl+Alt shortcuts. On the Mac the same entry (in
`~/Library/Application Support/MathShuttle/settings.json`) gives Control+Option+Shift+M: Alt there
is Option, Cmd is Command.

## Known texmath behaviour (same as the website)

* Office → LaTeX output follows texmath's style where *Tidy LaTeX* has no rule: `\widehat{x}`,
  `\rightarrow` for `\to`, `\left( … \right)` for Word's (always stretchy) brackets, extra braces
  around sub-expressions.
* In the OMML *text* output, `gather`, `multline`, `eqnarray`, `flalign` become an OMML matrix
  (`<m:m>`); only `align`-style arrays become an equation array (`<m:eqArr>`). (The clipboard
  route to Office also makes `eqnarray` an equation array, see above.)
* Unsupported macros stop the conversion with texmath's parse error shown in the output pane
  (define them in *Macros…*).
