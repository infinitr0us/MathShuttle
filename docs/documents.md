# Documents

## Open file: the equations of a document

**Open file…** lists the equations in a `.docx`, `.pptx`, `.tex` or `.md` file
with the surrounding sentence and location (heading, slide title or LaTeX label). If a matching
`.aux` file is available, the location also shows the equation number LaTeX assigned.
Type in **Filter** to search the LaTeX or location, ignoring case. **Display only** starts checked
for documents with more than 200 equations. **Distinct only** groups matching occurrences with
identical LaTeX, showing the first occurrence and the count.

Select a row to use it as input or copy it for Word / PowerPoint or as LaTeX. Use Ctrl (Cmd on
macOS) or Shift to select several rows. **Export selected as .tex…** writes those rows in document
order with their original positions. Use and Copy act on the first selected row. The two **Export
all** options write the entire document to a LaTeX file or to a Word document containing native
equations under their locations.
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

## A passage or a whole paper as a Word file

**Save as Word...** writes the input, a chat answer,
Markdown notes or a piece of LaTeX, as a Word document: its prose, headings, lists, tables and code as
they are, and each equation a native Word equation, made as *Copy for Word/PowerPoint* makes it (your
macros, the built-in packs). On the command line, `mathshuttle word FILE -o OUT.docx` does the same for a
passage. For a whole `.tex` or `.md` file, **Whole file as Word...** in the window of **Open file...** (or
`mathshuttle word paper.tex -o paper.docx`) writes the document: its title, authors and abstract,
with the paper's own definitions, and each numbered equation's number at the right margin as Word
numbers equations itself. The numbers are the ones LaTeX gives: counted as LaTeX counts them
(`equation` and `multline` one number, `align`, `gather`, `eqnarray`, `flalign` and `alignat` one per
row, `\nonumber`, `\notag`, `\tag` and starred environments as LaTeX has them) when the paper numbers
equations (1), (2)... in order, and checked against the `.aux` file LaTeX wrote next to it when there
is one. A paper that numbers equations by chapter or section (a thesis, `\numberwithin`) or sets the
counter itself gets the `.aux` numbers of its labelled equations (so compile it once first), and a
note says what was done.

`\eqref`, `\ref`, `\cref`, `\Cref` and `\autoref` show what LaTeX prints
("(2)", "eq. (2)", "Section 3", "Figure 1"), and citations their numbers from the `.aux` file, or
author and year for natbib without its `numbers` option ("(Smith et al., 2020)"). An equation Word
cannot take as an equation (a `tikzcd` diagram, `\ce{...}` chemistry, `xy`) goes in as a picture drawn
by your LaTeX installation (pdfLaTeX, with Ghostscript or Poppler, both part of MiKTeX and MacTeX), with
the paper's drawing packages and its LaTeX as the picture's alt text; a display one is centred with its
number at the right margin, and a note says how many went in this way. Such a picture cannot be edited
as an equation in Word, but **Compare** still knows it: a comment on it, or its deletion, is reported.
Without LaTeX, or with `--no-pictures` on the command line, the equation stays LaTeX text, followed
by the reason. The rest is pandoc's reading of the
paper: figures (found as LaTeX finds them, also in `\graphicspath` folders; Word takes no PDF or EPS
figure, so one goes in only when a PNG or JPEG of the same name is beside it, and the others are
named), tables and sections as pandoc converts them, and a command pandoc does not know is left out,
as in pandoc's own conversions (`\pageref` and `\nameref` among them).

## Slides made with IguanaTeX or TeXPoint

Many lecture decks hold their equations as pictures made by the PowerPoint add-ins IguanaTeX or
TeXPoint, which keep the LaTeX source inside the slide. **Open file…** on such a `.pptx` (or
`mathshuttle extract deck.pptx`) lists those equations too, with the native ones, in slide order, located
as "slide 3: Title (IguanaTeX)". Each equation comes with the macros of its own picture written out
(IguanaTeX keeps a whole LaTeX document per picture, its `\newcommand`s included), so it can be copied
into Word or PowerPoint as a native equation, or exported, without that preamble. A note says how many
such pictures the deck has, and names any whose LaTeX could not be read. LaTeXiT pictures and
MathType objects cannot be read.

## A Word file that came back: compare it with the paper

When a co-author edits the Word file of your paper (made with **Whole file as Word...** or
`mathshuttle word paper.tex -o paper.docx`) and sends it back, MathShuttle tells you what happened to
its equations. Open the paper with **Open file…**, click **Compare with a returned Word file...** and
choose the file that came back (or run `mathshuttle compare returned.docx paper.tex`). The list shows each
equation that was changed, deleted, added or moved (with Track Changes), and each one with a comment:
its number, label and section, the LaTeX as it is now in Word, who made the tracked changes and the
comments. Select one to see the paper's version next to Word's. **Copy LaTeX from Word** and **Use as
input** take the new version. Two rows need a look of your own: *Could not be read* (an equation Word
holds in a form MathShuttle cannot read) and *Unmatched* (a repeated equation, such as `x = 1` in several
places, whose place in the paper cannot be told for sure); neither is written into the corrected copy.
An equation that a macro of yours writes in the prose (`\newcommand{\eps}{\ensuremath{\varepsilon}}`)
has no LaTeX of its own in the paper: a change to it is shown with a note to change the macro.

**Write a corrected copy of the paper...** (`compare ... -o paper.revised.tex`) writes a copy of the
paper, never the paper itself, with each changed equation replaced by the LaTeX from Word, in the same
environment, rows and `\label`s kept (rows that were reordered in Word keep their own labels); your
macros are written back where the original equation used them (`\mathbb{R}` becomes `\R` again when the
equation had `\R`). A deleted equation stays, with a comment line above it saying who deleted it; a
moved one stays in place with a comment saying where it went; an added one comes as a commented block
after the equation before it, for you to place. A `.diff` file next to the copy shows every change; an
equation in an `\input` or `\include` file goes into a copy of that file in a folder next to the
corrected paper (`paper.revised-inputs`), and the copy's `\input` lines point there. Nothing is
overwritten: if that `.diff` or one of those copies already exists, MathShuttle stops and names it. If
the paper was edited since the export and an equation cannot be found any more for sure, it is left as
it is and named, as is an equation written between shorthand macros of yours (`\be ... \ee`). An
`\eqref` inside an equation, which Word shows as its number, is put back where its place is certain
(`\text{by \eqref{a}}`); any other is named, for you to put back. A corrected copy written to another
folder than the paper's needs the paper's own
`.sty` files and figures there to compile; a note says so.

How it knows: a Word export of a whole paper marks each equation with a hidden bookmark (Word shows it
only with *Hidden bookmarks* ticked) and keeps, in the file, the LaTeX each one came from. Word keeps
both when the file is edited and saved, with or without Track Changes. A Word file made by MathShuttle
0.8.0 or earlier has no marks: its equations are matched with the paper's by order and by what they show.
Tracked changes are read as the co-author left them: what Word shows with all changes accepted.
