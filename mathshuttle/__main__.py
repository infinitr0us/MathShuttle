"""Open the MathShuttle window without arguments, or run a command."""
from __future__ import annotations

import argparse
import os
import re
import shlex
import sys

from . import __version__
from .compat import load_macros, merge_notes
from .convert import MODES, convert, convert_equations, macro_notes, office_copy
from .documents import READABLE, read_document
from .latex_wrap import WRAPPER_KEYS
from .pandoc_engine import READ_FORMATS, WRITE_FORMATS, ConversionError, PandocNotFound, _mathy, engine_report

# how a line of comment (the equation's place in the document) is written in each output format
_COMMENT = {"latex": "%% %s", "mathml": "<!-- %s -->", "omml": "<!-- %s -->", "typst": "// %s", "eqn": '.\\" %s'}


def _comment(fmt: str, text: str, keep_spacing: bool = False) -> str:
    """A one-line comment in `fmt`; an XML comment may not hold "--" nor end with "-" (a heading "A--B")."""
    text = " ".join(text.splitlines() if keep_spacing else text.split())
    if fmt in ("mathml", "omml"):
        while "--" in text:
            text = text.replace("--", "- -")
        text = text + " " if text.endswith("-") else text
    return _COMMENT[fmt] % text


def _failure_reason(error: ConversionError) -> str:
    """In a texmath parse error the diagnosis follows the source line and its caret."""
    if getattr(error, "short", ""):  # the explanation in plain words (diagnose.explained), on one line
        return error.short
    lines = [line.strip() for line in str(error).splitlines() if line.strip()]
    for marker, reason in zip(lines, lines[1:]):
        if marker == "^" and reason != "^":
            return reason
    return lines[0].rstrip(":") if lines else "Unknown conversion error"


class InputError(ValueError):
    """The input file cannot be read; the message is meant for the user."""


def _quote_arg(value: str) -> str:
    """Quote one argument for PowerShell on Windows or a POSIX shell elsewhere."""
    if sys.platform == "win32":
        return value if re.fullmatch(r"[\w./:\\-]+", value) else "'" + value.replace("'", "''") + "'"
    return shlex.quote(value)


def _read_source(path: str | None, expr: str | None, command: str) -> str:
    """The input text: UTF-8 (with or without a BOM), or UTF-16 with a BOM - what `>` writes in
    Windows PowerShell 5.1."""
    if expr is not None:
        return expr
    if path is None or path == "-":
        if sys.stdin.isatty():
            eof = "Ctrl+Z then Enter" if sys.platform == "win32" else "Ctrl+D"
            print(f"Reading input from the keyboard. Press {eof} to finish.", file=sys.stderr, flush=True)
        return sys.stdin.read()
    try:
        with open(path, "rb") as f:
            data = f.read()
    except OSError as e:
        message = f"cannot read {path}: {e.strerror or e}"
        # (a mistyped path - C:\papers\x.tex, notes/eq.md - gets no hint)
        looks_like_path = bool(re.match(r"[A-Za-z]:[\\/]|\.{1,2}[\\/]|~[\\/]|/", path) or
                               re.search(r"\.[A-Za-z][A-Za-z0-9]{0,4}$", path))
        if not os.path.exists(path) and not looks_like_path and (
                re.search(r"[\s\\^_{}=+*()$]", path) or _mathy(path)):
            expr_arg = "-e=" if path.startswith("-") else "-e "
            message += ("\nThe argument was taken as a file name. For an equation, "
                        f"try mathshuttle {command} {expr_arg}{_quote_arg(path)}")
        raise InputError(message) from e
    try:
        text = data.decode("utf-16") if data[:2] in (b"\xff\xfe", b"\xfe\xff") else data.decode("utf-8-sig")
    except UnicodeDecodeError as e:
        raise InputError("%s is neither UTF-8 nor UTF-16 text (%s)" % (path, e))
    return text.replace("\r\n", "\n").replace("\r", "\n")  # as text mode would


def _utf8_stdio() -> None:
    """Pipes and redirections default to the ANSI code page on Windows (cp1252, cp936...):
    UTF-8 input would be misread and MathML output such as <mi>ℝ</mi> fails to encode."""
    for stream, encoding in ((sys.stdin, "utf-8-sig"), (sys.stdout, "utf-8"), (sys.stderr, "utf-8")):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding=encoding)


class _CommandParser(argparse.ArgumentParser):
    def error(self, message):
        super().error(message + "\nsee mathshuttle --help")


def _get_pandoc(force: bool) -> int:
    from . import pandoc_engine, pandoc_fetch

    name, override = pandoc_engine._env_pandoc()
    if override and not os.path.isfile(override):  # it would still win over a downloaded copy
        print(f"error: {name} is set to {override}, which is not a file. Unset it (or correct it); "
              f"then mathshuttle finds pandoc by itself, and get-pandoc can install one.", file=sys.stderr)
        return 1
    if not force:
        try:
            path = pandoc_engine.find_pandoc()
        except PandocNotFound:
            pass
        else:
            print(f"Pandoc is already available at {path}")
            return 0

    last = ""

    def progress(done, total):
        nonlocal last
        amount = f"{done * 100 // total}%" if total else f"{done / 1_000_000:.1f} MB"
        if amount != last:
            print(f"\rDownloading pandoc: {amount}", file=sys.stderr, end="", flush=True)
            last = amount

    try:
        path = pandoc_fetch.install(progress=progress)
    except (pandoc_fetch.FetchError, KeyboardInterrupt) as exc:
        if last:
            print(file=sys.stderr)
        if isinstance(exc, KeyboardInterrupt):
            print("Pandoc installation cancelled.", file=sys.stderr)
            return 130
        print(f"error: {exc}", file=sys.stderr)
        return 1
    if last:
        print(file=sys.stderr)
    pandoc_engine.reset_pandoc()
    print(f"Installed pandoc {pandoc_fetch.PANDOC_VERSION} at {path}")
    return 0


def _cli(argv: list[str]) -> int:
    _utf8_stdio()
    parser = _CommandParser(
        prog="mathshuttle",
        description="Convert equations between LaTeX, MathML and OMML or exchange them with Office.\n"
                    "Running mathshuttle without arguments opens the window.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""Examples:
  mathshuttle convert -e 'x^2' -t mathml
  mathshuttle copy -e 'x^2'
  mathshuttle paste --wrapper bare
  mathshuttle extract paper.tex -t typst
  mathshuttle get-pandoc
  mathshuttle --version""",
    )
    version_help = "Show the MathShuttle, pandoc and texmath versions."
    parser.add_argument("--version", action="store_true", help=version_help)
    sub = parser.add_subparsers(dest="cmd", title="commands", parser_class=argparse.ArgumentParser)

    def command(name, summary, description=None):
        return sub.add_parser(name, help=summary, description=description or summary)

    convert_cmd = command(
        "convert", "Convert equations and write them to standard output.",
        description="Convert equations and write them to standard output. The default output (-t omml) "
                    "is OMML text for people working with the XML. Use mathshuttle copy to paste into Office.",
    )
    copy_cmd = command("copy", "Copy an equation to paste into Word or PowerPoint.")
    paste_cmd = command("paste", "Convert an equation copied in Office and write it to standard output.")
    extract_cmd = command("extract", "Write each equation in a document with its location.")
    compare_cmd = command("compare", "Compare a returned Word document with its LaTeX paper.")
    compare_cmd.add_argument("returned", metavar="RETURNED.docx")
    compare_cmd.add_argument("paper", metavar="PAPER.tex")
    compare_cmd.add_argument("-o", "--output", metavar="CORRECTED.tex", help="write a corrected copy and its diff")
    compare_cmd.add_argument("--all", action="store_true", help="also show unchanged equations without comments")
    word_cmd = command(
        "word", "Write a passage or a whole .tex / .md document as a Word file with native equations.",
        description="Write a passage (a chat answer, Markdown notes, LaTeX text: a file, -e TEXT or standard "
                    "input) or a whole .tex / .md document as a Word file: its prose, headings, lists and tables, "
                    "and each equation a native Word equation. A .tex file gets its own definitions, and the "
                    "equation numbers of its .aux file at the right margin.")
    command("version", version_help)
    get_cmd = command("get-pandoc", "Download and install pandoc for this user.")
    get_cmd.add_argument("--force", action="store_true", help="download even if pandoc is already available")

    word_input = word_cmd.add_mutually_exclusive_group()
    word_input.add_argument("-e", "--expr", metavar="TEXT", help="use TEXT instead of a file or standard input")
    word_input.add_argument("file", nargs="?", metavar="FILE",
                            help="a .tex or .md document, or a text file with a passage (default: standard input)")
    word_cmd.add_argument("-o", "--output", required=True, metavar="OUT", help="the Word file to write (.docx)")
    word_cmd.add_argument("--macros", action="append", default=[], metavar="FILE",
                          help="load LaTeX macro definitions from FILE (repeatable)")

    for p in (convert_cmd, copy_cmd):
        input_group = p.add_mutually_exclusive_group()
        input_group.add_argument("-e", "--expr", metavar="TEXT", help="use TEXT instead of a file or standard input")
        input_group.add_argument("file", nargs="?", metavar="FILE", help="read input from FILE (default: standard input)")
        p.add_argument("-f", "--from", dest="src", choices=READ_FORMATS, default="latex",
                       help="input format (default: %(default)s)")
    for p, default in ((convert_cmd, "omml"), (paste_cmd, "latex"), (extract_cmd, "latex")):
        p.add_argument("-t", "--to", dest="dst", choices=WRITE_FORMATS, default=default,
                       help="output format (default: %(default)s)")
        p.add_argument("--wrapper", choices=WRAPPER_KEYS, default="auto", metavar="WRAPPER",
                       help="delimiters or environment for LaTeX output (default: auto). Choices: %(choices)s")
    for p in (convert_cmd, copy_cmd, paste_cmd):
        p.add_argument("--mode", choices=[k for k, _ in MODES], default="auto",
                       help="use inline or display math (auto keeps the detected mode)")
    for p in (convert_cmd, copy_cmd, extract_cmd):
        p.add_argument("--macros", action="append", default=[], metavar="FILE",
                       help="load LaTeX macro definitions from FILE (repeatable)")
    for p in (convert_cmd, extract_cmd):
        p.add_argument("--expand-macros", action="store_true",
                       help="write LaTeX with the macros of --macros, the input and the document written out "
                            "(only for LaTeX input and -t latex)")
    for p in (convert_cmd, paste_cmd):
        p.add_argument("--no-tidy", dest="tidy", action="store_false",
                       help="keep texmath's original LaTeX when reading MathML or OMML")
    convert_cmd.add_argument("--annotate", action="store_true", help="keep the TeX source inside MathML output")
    copy_cmd.add_argument("--index", type=int, default=1, metavar="N",
                          help="copy equation N from the input (default: 1, the first equation)")
    extract_cmd.add_argument("file", metavar="FILE", help="document to read (.tex, .md, .docx or .pptx)")
    extract_cmd.add_argument("--display", action="store_true", help="keep only display equations")
    extract_cmd.add_argument("--unique", action="store_true", help="show each distinct LaTeX source once with its count")
    extract_cmd.add_argument("--match", default="", metavar="TEXT", help="match TEXT in the LaTeX or location, ignoring case")
    extract_cmd.add_argument("--docx", metavar="OUT", help="also write the equations to the Word file OUT")

    if argv and argv[0] not in sub.choices and argv[0] not in ("--help", "-h", "--version"):
        name = argv[0]
        if os.path.isfile(name) or name.lower().endswith(tuple(READABLE)):
            end_options = "-- " if name.startswith("-") else ""
            parser.error(f"to read equations from a document, try mathshuttle extract {end_options}{_quote_arg(name)}")
    args = parser.parse_args(argv)
    errors: tuple = (ConversionError, PandocNotFound, InputError)
    if args.cmd in ("copy", "paste"):
        from . import clipboard
        errors += (clipboard.ClipboardError,)
    try:
        if args.version or args.cmd == "version":
            print(f"MathShuttle {__version__}")
            print(engine_report())
            return 0
        if args.cmd == "get-pandoc":
            return _get_pandoc(args.force)
        if args.cmd in ("convert", "copy", "extract", "word"):
            for path in args.macros:
                if not os.path.isfile(path):
                    raise InputError("macro file %s does not exist" % path)
        if args.cmd == "compare":
            from .compare import compare, write_corrected
            result = compare(args.returned, args.paper)
            originals = {row.original.n: row.original for row in result.rows if row.original}
            for row in result.rows:
                if row.status == "unchanged" and not args.all and not row.comments:
                    continue  # (an unchanged equation with a co-author's comment is shown)
                eq = row.original
                if eq:
                    where = [eq.number or "equation %d" % eq.n, *eq.labels, eq.section]
                else:
                    anchor = originals.get(row.after)
                    where = []
                    if anchor:
                        location = anchor.numbers[-1] if anchor.numbers else "equation %d" % anchor.n
                        where = ["after " + location, anchor.section]
                print(" ".join(filter(None, [row.status, *where])))
                if eq:
                    print("Original:", "\n".join(eq.tex.splitlines()))
                if row.returned:
                    print("Returned:", row.returned)
                for r in row.revisions:
                    print("Tracked change:", ", ".join(filter(None, (r.author, r.date))))
                for c in row.comments:
                    print("Comment by %s: %s" % (c.author, c.text))
                print()
            if args.output:
                for note in write_corrected(result, args.output):
                    print(note, file=sys.stderr)
                print("Written to %s and %s.diff" % (args.output, os.path.splitext(args.output)[0]), file=sys.stderr)
        elif args.cmd == "word":
            from . import wordexport
            if args.file and os.path.splitext(args.file)[1].lower() in (".tex", ".md"):
                notes = wordexport.document_to_word(args.file, args.output, load_macros(args.macros))
            else:
                source = _read_source(args.file, args.expr, args.cmd)
                notes = wordexport.passage_to_word(source, args.output, load_macros(args.macros))
            sys.stderr.write("".join(n + "\n" for n in notes) + "written to %s\n" % args.output)
        elif args.cmd == "convert":
            source = _read_source(args.file, args.expr, args.cmd)
            result = convert(source, args.src, args.dst, args.mode, args.wrapper, args.annotate,
                             macros=load_macros(args.macros), tidy=args.tidy, expand_macros=args.expand_macros)
            sys.stdout.write(result.text + "\n")
            for note in result.notes:
                sys.stderr.write(note + "\n")
        elif args.cmd == "copy":
            if args.index < 1:
                raise InputError("--index counts from 1")
            source = _read_source(args.file, args.expr, args.cmd)
            copy = office_copy(source, args.src, args.mode, load_macros(args.macros), index=args.index - 1)
            clipboard.copy_mathml_for_office(copy.mathml)  # the plain text must be the MathML too (see there)
            copy.remember()
            sys.stderr.write("".join(n + "\n" for n in copy.notes) or "Equation copied. Paste it into Word or PowerPoint.\n")
        elif args.cmd == "paste":
            found = clipboard.read_office_equation()
            if not found:
                sys.stderr.write("No equation on the clipboard (copy one in Word / PowerPoint first).\n")
                return 2
            src, text = found
            from . import recall
            recalled = recall.latex_for(text, args.wrapper, args.mode) if src == "mathml" and args.dst == "latex" \
                and args.tidy else None
            if recalled is not None:  # copied from here and unedited: the LaTeX as written
                sys.stdout.write(recalled + "\n")
                sys.stderr.write(recall.NOTE + "\n")
                return 0
            result = convert(text, src, args.dst, args.mode, args.wrapper, tidy=args.tidy)
            sys.stdout.write(result.text + "\n")
            for note in result.notes:  # e.g. several equations in the copied text
                sys.stderr.write(note + "\n")
        elif args.cmd == "extract":
            expand = args.expand_macros and args.dst == "latex"
            doc = read_document(args.file, expand_macros=expand)
            macros = load_macros(args.macros)
            notes = list(doc.notes) + macro_notes(macros)
            rows = doc.rows(args.display, args.unique, args.match)
            if args.docx:  # first, and said at once: an equation that -t cannot write below does not stop it
                from .documents import write_docx
                word_doc = read_document(args.file) if expand and doc.source == "latex" else doc
                word_rows = rows
                if word_doc is not doc:  # the same occurrences (filtered on the LaTeX as written), read as usual
                    from .documents import EquationRow
                    word_rows = [EquationRow(r.number, word_doc.found[r.number - 1], r.where, r.count)
                                 if len(word_doc.found) == len(doc.found) else r for r in rows]
                written = write_docx(word_doc, args.docx, macros, word_rows)
                sys.stderr.write("".join(n + "\n" for n in written) + "written to %s\n" % args.docx)
                sys.stderr.flush()
            own = doc.macros(macros)  # a document's own LaTeX: the macro files and its own definitions
            failures = []
            equations = [row.found.equation for row in rows]
            if expand and doc.source == "latex":
                converted = convert_equations(equations, args.dst, args.wrapper, own, expand_macros=True)
            else:
                converted = convert_equations(equations, args.dst, args.wrapper, own)
            for row, result in zip(rows, converted):
                # Keep the original one-space comments unless we have a new number or count to show.
                enhanced = bool(row.count or row.where != row.found.where)
                where = row.location if enhanced else " ".join(row.where.split())
                location = f"[{row.number}] {where}"
                if isinstance(result, ConversionError):
                    reason = _failure_reason(result)
                    text = _comment(args.dst, "Could not be converted: " + reason)
                    failures.append(f"{location}: {reason}")
                else:
                    text, new = result
                    notes += [n for n in new if n not in notes]
                sys.stdout.write(_comment(args.dst, location, keep_spacing=enhanced) + "\n" + text + "\n\n")
            count = f"{len(rows)} equation(s) in {os.path.basename(args.file)}"
            for line in merge_notes(notes) + [count] + failures:
                print(line, file=sys.stderr)
            if failures:
                print(f"{len(failures)} of {count} could not be converted", file=sys.stderr)
                return 1
        else:
            parser.print_help()
            return 2
    except errors as e:
        sys.stderr.write("error: %s\n" % e)
        return 1
    return 0


def main(argv: list[str] | None = None) -> int:
    argv = sys.argv[1:] if argv is None else argv
    if argv:
        return _cli(argv)
    from .app import main as gui_main
    return gui_main()


if __name__ == "__main__":
    sys.exit(main())
