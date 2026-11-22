"""Entry point.

    python -m mathshuttle                       start the GUI
    python -m mathshuttle convert -f latex -t omml [FILE]     convert FILE (or stdin) to stdout
    python -m mathshuttle copy [-f latex] [FILE]              put the equation on the clipboard for Word/PowerPoint
    python -m mathshuttle paste [-t latex] [--wrapper W]      convert the equation copied in Word/PowerPoint
    python -m mathshuttle extract FILE [-t latex]             every equation of a .docx / .pptx / .tex / .md
    python -m mathshuttle version                             pandoc + embedded texmath version
"""
from __future__ import annotations

import argparse
import os
import sys

from .compat import load_macros
from .convert import MODES, convert, convert_equation, office_copy

# how a line of comment (the equation's place in the document) is written in each output format
_COMMENT = {"latex": "%% %s", "mathml": "<!-- %s -->", "omml": "<!-- %s -->", "typst": "// %s", "eqn": '.\\" %s'}


def _comment(fmt: str, text: str) -> str:
    """A one-line comment in `fmt`; an XML comment may not hold "--" nor end with "-" (a heading "A--B")."""
    text = " ".join(text.split())
    if fmt in ("mathml", "omml"):
        while "--" in text:
            text = text.replace("--", "- -")
        text = text + " " if text.endswith("-") else text
    return _COMMENT[fmt] % text
from .latex_wrap import WRAPPER_KEYS
from .pandoc_engine import READ_FORMATS, WRITE_FORMATS, ConversionError, PandocNotFound, engine_report


class InputError(ValueError):
    """The input file cannot be read; the message is meant for the user."""


def _read_source(path: str | None) -> str:
    """The input text: UTF-8 (with or without a BOM), or UTF-16 with a BOM - what `>` writes in
    Windows PowerShell 5.1."""
    if not path or path == "-":
        return sys.stdin.read()
    try:
        with open(path, "rb") as f:
            data = f.read()
    except OSError as e:
        raise InputError("cannot read %s: %s" % (path, e.strerror or e))
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


def _cli(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(prog="mathshuttle", description="LaTeX / MathML / OMML equation converter (texmath via pandoc)")
    sub = parser.add_subparsers(dest="cmd")

    p = sub.add_parser("convert", help="convert between formats")
    p.add_argument("-f", "--from", dest="src", choices=READ_FORMATS, default="latex")
    p.add_argument("-t", "--to", dest="dst", choices=WRITE_FORMATS, default="omml")
    p.add_argument("--mode", choices=[k for k, _ in MODES], default="auto")
    p.add_argument("--wrapper", choices=WRAPPER_KEYS, default="auto", help="delimiters for LaTeX output")
    p.add_argument("--annotate", action="store_true", help="keep the TeX source inside MathML output")
    p.add_argument("--macros", action="append", default=[], metavar="FILE",
                   help="macro definitions for LaTeX input (repeatable): math_commands.tex, a preamble...")
    p.add_argument("--no-tidy", dest="tidy", action="store_false",
                   help="LaTeX from MathML / OMML exactly as texmath writes it")
    p.add_argument("file", nargs="?", help="input file (default: stdin)")

    p = sub.add_parser("copy", help="convert and place on the clipboard for Word/PowerPoint")
    p.add_argument("-f", "--from", dest="src", choices=READ_FORMATS, default="latex")
    p.add_argument("--mode", choices=[k for k, _ in MODES], default="auto")
    p.add_argument("--macros", action="append", default=[], metavar="FILE")
    p.add_argument("file", nargs="?")

    p = sub.add_parser("paste", help="convert the equation copied in Word/PowerPoint")
    p.add_argument("-t", "--to", dest="dst", choices=WRITE_FORMATS, default="latex")
    p.add_argument("--mode", choices=[k for k, _ in MODES], default="auto")
    p.add_argument("--wrapper", choices=WRAPPER_KEYS, default="auto")
    p.add_argument("--no-tidy", dest="tidy", action="store_false")

    p = sub.add_parser("extract", help="every equation of a .docx, .pptx, .tex or .md file, with where it is")
    p.add_argument("file")
    p.add_argument("-t", "--to", dest="dst", choices=WRITE_FORMATS, default="latex")
    p.add_argument("--wrapper", choices=WRAPPER_KEYS, default="auto")
    p.add_argument("--macros", action="append", default=[], metavar="FILE")

    sub.add_parser("version", help="show the pandoc / texmath versions in use")

    args = parser.parse_args(argv)
    _utf8_stdio()
    errors: tuple = (ConversionError, PandocNotFound, InputError)
    if args.cmd in ("copy", "paste"):
        from . import clipboard
        errors += (clipboard.ClipboardError,)
    try:
        if args.cmd == "convert":
            for path in args.macros:
                if not os.path.isfile(path):
                    raise InputError("macro file %s does not exist" % path)
            result = convert(_read_source(args.file), args.src, args.dst, args.mode, args.wrapper, args.annotate,
                             macros=load_macros(args.macros), tidy=args.tidy)
            sys.stdout.write(result.text + "\n")
            for note in result.notes:
                sys.stderr.write(note + "\n")
        elif args.cmd == "copy":
            for path in args.macros:
                if not os.path.isfile(path):
                    raise InputError("macro file %s does not exist" % path)
            copy = office_copy(_read_source(args.file), args.src, args.mode, load_macros(args.macros))
            clipboard.copy_mathml_for_office(copy.mathml)  # the plain text must be the MathML too (see there)
            sys.stderr.write("".join(n + "\n" for n in copy.notes) or "Equation copied - paste it into Word / PowerPoint.\n")
        elif args.cmd == "paste":
            mathml = clipboard.read_equation_from_office()
            if not mathml:
                sys.stderr.write("No equation on the clipboard (copy one in Word / PowerPoint first).\n")
                return 2
            sys.stdout.write(convert(mathml, "mathml", args.dst, args.mode, args.wrapper, tidy=args.tidy).text + "\n")
        elif args.cmd == "extract":
            from .documents import read_document
            doc = read_document(args.file)
            macros = load_macros(args.macros)
            notes = list(doc.notes)
            for i, found in enumerate(doc.found, 1):
                text, new = convert_equation(found.equation, args.dst, args.wrapper, macros)
                notes += [n for n in new if n not in notes]
                sys.stdout.write(_comment(args.dst, "[%d] %s" % (i, found.where)) + "\n" + text + "\n\n")
            sys.stderr.write("".join(n + "\n" for n in notes) + "%d equation(s) in %s\n" % (
                len(doc.found), os.path.basename(args.file)))
        elif args.cmd == "version":
            sys.stdout.write(engine_report() + "\n")
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
