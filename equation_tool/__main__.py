"""Entry point.

    python -m equation_tool                       start the GUI
    python -m equation_tool convert -f latex -t omml [FILE]     convert FILE (or stdin) to stdout
    python -m equation_tool copy [-f latex] [FILE]              put the equation on the clipboard for Word/PowerPoint
    python -m equation_tool paste [-t latex] [--wrapper W]      convert the equation copied in Word/PowerPoint
    python -m equation_tool version                             pandoc + embedded texmath version
"""
from __future__ import annotations

import argparse
import sys

from .convert import MODES, convert, mathml_for_office
from .latex_wrap import WRAPPER_KEYS
from .pandoc_engine import READ_FORMATS, WRITE_FORMATS, ConversionError, PandocNotFound, engine_description, find_pandoc


def _read_source(path: str | None) -> str:
    if path and path != "-":
        with open(path, encoding="utf-8") as f:
            return f.read()
    return sys.stdin.read()


def _cli(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(prog="equation_tool", description="LaTeX / MathML / OMML equation converter (texmath via pandoc)")
    sub = parser.add_subparsers(dest="cmd")

    p = sub.add_parser("convert", help="convert between formats")
    p.add_argument("-f", "--from", dest="src", choices=READ_FORMATS, default="latex")
    p.add_argument("-t", "--to", dest="dst", choices=WRITE_FORMATS, default="omml")
    p.add_argument("--mode", choices=[k for k, _ in MODES], default="auto")
    p.add_argument("--wrapper", choices=WRAPPER_KEYS, default="auto", help="delimiters for LaTeX output")
    p.add_argument("--annotate", action="store_true", help="keep the TeX source inside MathML output")
    p.add_argument("file", nargs="?", help="input file (default: stdin)")

    p = sub.add_parser("copy", help="convert and place on the clipboard for Word/PowerPoint")
    p.add_argument("-f", "--from", dest="src", choices=READ_FORMATS, default="latex")
    p.add_argument("--mode", choices=[k for k, _ in MODES], default="auto")
    p.add_argument("file", nargs="?")

    p = sub.add_parser("paste", help="convert the equation copied in Word/PowerPoint")
    p.add_argument("-t", "--to", dest="dst", choices=WRITE_FORMATS, default="latex")
    p.add_argument("--mode", choices=[k for k, _ in MODES], default="auto")
    p.add_argument("--wrapper", choices=WRAPPER_KEYS, default="auto")

    sub.add_parser("version", help="show the pandoc / texmath versions in use")

    args = parser.parse_args(argv)
    try:
        if args.cmd == "convert":
            result = convert(_read_source(args.file), args.src, args.dst, args.mode, args.wrapper, args.annotate)
            sys.stdout.write(result.text + "\n")
            for note in result.notes:
                sys.stderr.write(note + "\n")
        elif args.cmd == "copy":
            from . import clipboard
            mathml, note = mathml_for_office(_read_source(args.file), args.src, args.mode)
            clipboard.copy_mathml_for_office(mathml)
            sys.stderr.write((note + "\n") if note else "Equation copied - paste it into Word / PowerPoint.\n")
        elif args.cmd == "paste":
            from . import clipboard
            mathml = clipboard.read_equation_from_office()
            if not mathml:
                sys.stderr.write("No equation on the clipboard (copy one in Word / PowerPoint first).\n")
                return 2
            sys.stdout.write(convert(mathml, "mathml", args.dst, args.mode, args.wrapper).text + "\n")
        elif args.cmd == "version":
            sys.stdout.write("%s\n%s\n" % (engine_description(), find_pandoc()))
        else:
            parser.print_help()
            return 2
    except (ConversionError, PandocNotFound) as e:
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
