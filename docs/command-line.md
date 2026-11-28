# Command line

`mathshuttle` opens the window when run without arguments. Use `mathshuttle --help` for commands
and examples, or `mathshuttle extract --help` for the options of one command. From a source
checkout, use `python -m mathshuttle` in place of `mathshuttle`.

```
mathshuttle convert -e 'x^2' -t mathml
mathshuttle copy -e 'x^2'
mathshuttle paste --wrapper bare
mathshuttle extract paper.tex -t typst
mathshuttle extract thesis.tex --display --match Method --unique
mathshuttle get-pandoc
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
  each with its original position, location and available LaTeX equation number in a comment.
  Use `--display` for display equations and `--match TEXT` for a case-insensitive search of the LaTeX
  or location. Add `--unique` to keep the first occurrence of each distinct LaTeX source and show
  its count among the matches, for example `(3x)`. The filters also apply to `--docx OUT`.
  The default output is LaTeX. With `-t mathml`, `omml`, `typst` or `eqn`, equations are converted
  in a batch. A failed equation gets a comment explaining the failure, and extraction continues.
  Standard error lists the failed equations by number and location and reports the count.
  The exit code is 1 if any equation failed or 0 on success. Use `--docx OUT` to also write
  the equations to a Word document.
* `--macros FILE` loads LaTeX definitions for `convert`, `copy` and `extract` and can be repeated.
  `--wrapper` sets the delimiters or environment for LaTeX output. With `convert` or `paste`,
  `--no-tidy` keeps texmath's original LaTeX from MathML or OMML.
* `word FILE -o OUT.docx` (or `-e TEXT`, or standard input) writes a passage, or a whole `.tex` / `.md`
  document, as a Word file with native equations (see [Documents](documents.md#a-passage-or-a-whole-paper-as-a-word-file)).
* `--version` and `version` both show the MathShuttle, pandoc and texmath versions and the pandoc path.
* `get-pandoc` downloads pandoc's own release for this system from GitHub, checks its size and
  SHA-256 against the values MathShuttle carries, and installs the program for this user only
  (`%LOCALAPPDATA%\MathShuttle\pandoc` on Windows, `~/Library/Application Support/MathShuttle/pandoc`
  on macOS, `~/.local/share/MathShuttle/pandoc` on Linux), with pandoc's licence files next to it. It
  does nothing when a pandoc is already found; `--force` downloads anyway. A pandoc you installed
  yourself (on `PATH`, with winget, Homebrew or pandoc's installer) is always used first. This is the
  only time MathShuttle goes online, and only when you ask for it.
