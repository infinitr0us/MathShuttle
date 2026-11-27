"""Find out how Word / PowerPoint for Mac exchange equations through the pasteboard (stage 2 of the Mac port).

    .venv/bin/python tools/mac_office_probe.py             # both parts: capture, then paste
    .venv/bin/python tools/mac_office_probe.py capture     # only what Office puts on the pasteboard
    .venv/bin/python tools/mac_office_probe.py paste       # only which pasteboard contents Office pastes
    .venv/bin/python tools/mac_office_probe.py selfcheck   # no pasteboard use: builds the test data only

Interactive, in a Terminal window next to Word and PowerPoint.

capture: you copy an equation in Word or PowerPoint (Cmd+C) and press Enter here.  Every type on the
pasteboard is saved under probe-out/<time>/capture/ with a summary, and the app's own "Paste from
Word/PowerPoint" reading is tried on it.  Reading what Word copied may make macOS ask whether Terminal
may paste from Microsoft Word: allow it.  Everything on the pasteboard is saved, so do not capture while
a password or other private text is copied.

paste: the script puts one candidate on the pasteboard at a time (MathML under a single type, plain
text, HTML, Word's own captured data...), you paste it into Word and into PowerPoint and type what
appeared.  The answers go to probe-out/<time>/report.md and report.json.  The last candidate stays on
the pasteboard afterwards.
"""
from __future__ import annotations

import datetime
import hashlib
import json
import os
import platform
import plistlib
import re
import shutil
import sys
from typing import Dict, List, Optional, Tuple, Union

ROOT = os.path.abspath(os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
sys.path.insert(0, ROOT)

if sys.platform != "darwin":
    sys.exit("This probe is for macOS only.")

from mathshuttle import clipboard  # noqa: E402
from mathshuttle import _clipboard_macos as mac  # noqa: E402
from mathshuttle.convert import convert, office_copy  # noqa: E402

TEST_LATEX = r"\frac{a}{b} + \sqrt{x^2+1} = \alpha_i"
TEST_ALIGNED = "\\begin{aligned} a &= b + c \\\\ d &= e \\end{aligned}"

# what the data looks like (searched in the text when it decodes as text, else in the raw bytes)
MARKERS = [
    ("MathML", rb"<(?:[A-Za-z]+:)?math\b"),
    ("OMML", rb"<m:oMath"),
    ("RTF-math", rb"\\mmath|\\moMath"),
    ("RTF", rb"^\{\\rtf"),
    ("HTML", rb"<html|<HTML|<!DOCTYPE html"),
    ("SVG", rb"<svg"),
    ("PDF", rb"^%PDF"),
    ("PNG", rb"^\x89PNG"),
    ("TIFF", rb"^(?:MM\x00\*|II\*\x00)"),
    ("LaTeX", rb"\\frac|\\sqrt|\\begin\{|\\alpha"),
]
ANSWERS = {"e": "equation", "t": "text", "p": "picture", "n": "nothing", "s": "skipped"}
_SEL_ITEMS = mac._objc.sel_registerName(b"pasteboardItems")

Entries = Dict[str, Union[bytes, str]]


class _Quit(Exception):
    pass


# --- reading the pasteboard -------------------------------------------------------------------
def read_items() -> Tuple[int, List[List[Tuple[str, Optional[bytes]]]]]:
    """(changeCount, [[(type, data or None) for each type] for each pasteboard item])."""
    items_out = []
    with mac._lock, mac._Pool():
        pb = mac._pasteboard()
        change = mac._send_int(pb, mac._SEL["changeCount"])
        items = mac._send(pb, _SEL_ITEMS)
        for i in range(mac._send_uint(items, mac._SEL["count"]) if items else 0):
            item = mac._send_index(items, mac._SEL["objectAtIndex:"], i)
            types = mac._send(item, mac._SEL["types"])
            entries = []
            for j in range(mac._send_uint(types, mac._SEL["count"]) if types else 0):
                name = mac._str(mac._send_index(types, mac._SEL["objectAtIndex:"], j))
                data = mac._send_id(item, mac._SEL["dataForType:"], mac._nsstring(name))
                entries.append((name, mac._bytes(data) if data else None))
            items_out.append(entries)
    return change, items_out


def as_text(data: bytes) -> Optional[str]:
    if data[:2] in (b"\xff\xfe", b"\xfe\xff") or (data[1:2] == b"\0" and data[:1] not in (b"", b"\0")):
        try:
            return data[:len(data) & ~1].decode("utf-16").lstrip("﻿").rstrip("\0")
        except UnicodeDecodeError:
            return None
    try:
        return data.rstrip(b"\0").decode("utf-8")
    except UnicodeDecodeError:
        return None


def describe(data: Optional[bytes]) -> dict:
    if data is None:
        return {"size": None, "markers": [], "preview": "(no data)", "text": False}
    text = as_text(data)
    blob = text.encode("utf-8", "replace") if text is not None else data
    markers = [name for name, pattern in MARKERS if re.search(pattern, blob[:4_000_000])]
    if text is not None:
        preview = re.sub(r"\s+", " ", text).strip()[:200]
    else:
        preview = "binary: " + data[:24].hex(" ")
    return {"size": len(data), "sha256": hashlib.sha256(data).hexdigest()[:16], "markers": markers,
            "preview": preview, "text": text is not None}


def safe(name: str) -> str:
    return re.sub(r"[^A-Za-z0-9._-]+", "_", name)[:80] or "_"


# --- capture ----------------------------------------------------------------------------------
CAPTURE_INTRO = """
PART 1 of 2: copy equations in Word / PowerPoint
------------------------------------------------
The script asks for one copy at a time.  Do what it says in Word or PowerPoint, come back here and
press Enter.  macOS may ask whether the terminal may paste from Word / PowerPoint: allow it.
Everything on the clipboard is saved, so do not do this while a password is copied."""

# (label, what to do); the user can skip each
CAPTURE_STEPS = [
    ("word-inline", "In Word: Insert > Equation, type  x^2+\\alpha  then a space.\n"
                    "Select the whole equation and press Cmd+C."),
    ("word-display", "In Word: on a new empty line, Insert > Equation, type  a/b  then a space.\n"
                     "Select the whole equation and press Cmd+C."),
    ("word-sentence", "(optional) In Word: type a few words before or after one of the equations,\n"
                      "select the whole line (words and equation) and press Cmd+C."),
    ("ppt-eq", "In PowerPoint: click into a text box, Insert > Equation, type  x^2+\\alpha  then a space.\n"
               "Select the whole equation and press Cmd+C."),
    ("word-aligned", "(optional) In Word: if a document of yours has an aligned block (several lines\n"
                     "lined up at =), select it and press Cmd+C."),
]


def _rule(title: str) -> None:
    print("\n" + "=" * min(70, _width()))
    print(title)
    print("=" * min(70, _width()))


def _width() -> int:
    return max(40, shutil.get_terminal_size((80, 24)).columns - 1)


def _short(text: str, room: int) -> str:
    return text if len(text) <= room else text[:max(1, room - 3)] + "..."


def capture(outdir: str, report: dict, raw: dict) -> None:
    os.makedirs(os.path.join(outdir, "capture"), exist_ok=True)
    print(CAPTURE_INTRO)
    steps = list(CAPTURE_STEPS)
    number = 0
    while True:
        number += 1
        if number <= len(steps):
            label, todo = steps[number - 1]
            _rule("Copy %d of %d: %s" % (number, len(steps), label))
            print(todo)
            answer = input("\nThen press Enter here   (s = skip this one, d = done with part 1): ").strip().lower()
            if answer == "s":
                continue
        else:
            _rule("Another copy?")
            answer = input("Copy something else and type a short name for it, or just press Enter for part 2: ").strip()
            label = answer
            if not answer:
                return
        if answer == "q":
            raise _Quit
        if answer == "d":
            return
        capture_one(outdir, report, raw, label)


def capture_one(outdir: str, report: dict, raw: dict, label: str) -> None:
    cap_dir = os.path.join(outdir, "capture")
    last_change = report["captures"][-1]["change_count"] if report["captures"] else None
    change, items = read_items()
    tag = "%02d-%s" % (len(report["captures"]) + 1, safe(label))
    record = {"label": label, "tag": tag, "change_count": change, "items": []}
    if change == last_change:
        print("  Careful: the clipboard has not changed since the previous copy (was Cmd+C pressed?).")
        record["unchanged"] = True
    room = min(60, _width() - 16)
    count = 0
    for i, entries in enumerate(items):
        types = []
        for j, (name, data) in enumerate(entries):
            info = describe(data)
            info["type"] = name
            if data is not None:
                fname = "%s_item%d_%02d_%s.bin" % (tag, i, j, safe(name))
                with open(os.path.join(cap_dir, fname), "wb") as f:
                    f.write(data)
                info["file"] = "capture/" + fname
            types.append(info)
            count += 1
            marks = (" [%s]" % ",".join(info["markers"])) if info["markers"] else ""
            print("  %-*s %9s B" % (room, _short(name + marks, room), info["size"] if info["size"] is not None else "-"))
        record["items"].append(types)
    raw[tag] = items
    found = None
    try:
        found = clipboard.read_office_equation()  # what Paste from Word/PowerPoint uses (MathML, else HTML)
    except clipboard.ClipboardError as e:
        record["app_read_error"] = str(e)
    record["app_found_mathml"] = bool(found and found[0] == "mathml")  # from the same read
    if found:
        record["app_source"] = found[0]
        try:
            record["app_latex"] = convert(found[1], found[0], "latex").text
        except Exception as e:  # noqa: BLE001 - recorded for the report
            record["app_latex_error"] = "%s: %s" % (type(e).__name__, e)
    print("Saved %d clipboard type(s)%s." % (count, "" if len(items) < 2 else " in %d items" % len(items)))
    print("MathShuttle's Paste from Word reads it as: %s" % (
        record.get("app_latex") or record.get("app_latex_error") or record.get("app_read_error")
        or "nothing (no MathML, no equation in the HTML)"))
    report["captures"].append(record)
    save(outdir, report)


# --- paste ------------------------------------------------------------------------------------
def test_mathml(latex: str) -> Tuple[str, str]:
    """(MathML as Copy for Word/PowerPoint makes it, the same without an XML declaration)."""
    copy = office_copy(latex, "latex", "display")
    bare = copy.mathml.split("?>", 1)[-1].strip() if copy.mathml.startswith("<?xml") else copy.mathml
    return copy.mathml, bare


OFFICE_MATHML_TYPES = ("public.mathml", "public.mathml.presentation")  # what Mac Word 16.115 writes


def variants(raw: dict, report: dict) -> List[Tuple[str, str, Optional[Entries], bool]]:
    """(name, description, entries, optional); entries None = the app's own Copy for Word/PowerPoint.
    The important candidates come first, so that the user can stop after them."""
    mathml, bare = test_mathml(TEST_LATEX)
    payload = mac._mathml_payload(mathml)
    found = []  # types Office wrote MathML under (not HTML / RTF, which need their own framing)
    for record in report["captures"]:
        for types in record["items"]:
            for info in types:
                if "MathML" in info["markers"] and not set(info["markers"]) & {"HTML", "RTF"} \
                        and info["type"] not in found and info["type"] != mac.TEXT_TYPE:
                    found.append(info["type"])
    main_types = list(OFFICE_MATHML_TYPES) + [t for t in found if t not in OFFICE_MATHML_TYPES]
    out: List[Tuple[str, str, Optional[Entries], bool]] = [
        ("app", "What MathShuttle's 'Copy for Word/PowerPoint' does today\n"
                "(MathML under %d types including public.mathml, and as plain text)." % len(mac.MATHML_TYPES),
         None, False)]
    for t in main_types:
        out.append(("only:" + t, "MathML under %s only." % t, {t: payload}, False))
    out.append(("both-office-types", "MathML under %s, nothing else." % " and ".join(OFFICE_MATHML_TYPES),
                {t: payload for t in OFFICE_MATHML_TYPES}, False))
    out.append(("text=mathml", "Plain text only, holding the MathML (what Word on Windows pastes from).",
                {mac.TEXT_TYPE: mathml}, False))
    out.append(("text=latex", "Plain text only: $...$ LaTeX (does Word convert LaTeX itself?).",
                {mac.TEXT_TYPE: "$%s$" % TEST_LATEX}, False))
    out.append(("app-aligned", "'Copy for Word/PowerPoint' of an aligned block: two lines, a = b + c and d = e.\n"
                               "Does it come out as two lines lined up at the = signs?", None, False))
    # Office's own data back onto the pasteboard: all of it, then the likely types alone (optional)
    for tag, items in raw.items():
        if not items or not items[0]:
            continue
        entries = {name: data for name, data in items[0] if data is not None}
        out.append(("replay:" + tag, "Word's own copy (%s) put back as it was, all %d types: should paste\n"
                                     "like a normal Cmd+V of that equation." % (tag, len(entries)), entries, True))
        record = next(r for r in report["captures"] if r["tag"] == tag)
        for info in record["items"][0]:
            likely = set(info["markers"]) & {"MathML", "OMML", "RTF-math", "HTML"} or \
                info["type"] == "com.microsoft.Embed-Source"
            if likely and info["type"] in entries:
                out.append(("replay:%s:only:%s" % (tag, info["type"]),
                            "Word's own %s data from that copy, alone." % info["type"],
                            {info["type"]: entries[info["type"]]}, True))
        break  # the first capture with data is enough: the singles tell which type Office reads
    out.append(("html", "public.html only, with the MathML inside.",
                {"public.html": ("<html><body>%s</body></html>" % bare).encode("utf-8")}, True))
    for t in mac.MATHML_TYPES:
        if t not in main_types:
            out.append(("only:" + t, "MathML under %s only (an old guess)." % t, {t: payload}, True))
    return out


PASTE_INTRO = """
PART 2 of 2: paste into Word and PowerPoint
-------------------------------------------
Open an empty Word document, and a PowerPoint slide with a text box.  The script now puts one
candidate at a time on the clipboard.  For each: paste it into Word (Cmd+V, then Return for a new
line), paste it into the PowerPoint text box (Cmd+V), and answer here what appeared.
Do not copy anything else meanwhile.  Test equation: %s
The candidates marked (optional) come last: you can stop with q when you reach them.""" % TEST_LATEX

LEGEND = ("Answer: e = equation, t = text, p = picture, n = nothing, s = skip, q = stop\n"
          "(a short note may follow the letter, e.g.  e root missing)")


def ask(app: str) -> dict:
    raw = input("  What appeared in %-11s > " % (app + "?")).strip()
    if raw.lower() == "q":
        raise _Quit
    key, _, note = raw.partition(" ")
    result = ANSWERS.get(key.lower())
    if result is None:
        result, note = "other", raw
    return {"result": result, "note": note.strip()}


def paste(outdir: str, report: dict, raw: dict) -> None:
    print(PASTE_INTRO)
    todo = variants(raw, report)
    for number, (name, description, entries, optional) in enumerate(todo, 1):
        if entries is None:
            latex = TEST_ALIGNED if name == "app-aligned" else TEST_LATEX
            clipboard.copy_mathml_for_office(test_mathml(latex)[0])
        else:
            clipboard.set_data(entries)
        _rule("Candidate %d of %d%s: %s" % (number, len(todo), " (optional)" if optional else "", name))
        print(description)
        print("\nIt is on the clipboard now.  Paste it into Word, then into the PowerPoint text box.")
        print(LEGEND + "\n")
        record = {"variant": name, "description": description, "types": list(entries) if entries else None}
        record["word"] = ask("Word")
        record["powerpoint"] = ask("PowerPoint")
        report["pastes"].append(record)
        save(outdir, report)


# --- report -----------------------------------------------------------------------------------
def environment() -> dict:
    env = {"macos": platform.mac_ver()[0], "python": platform.python_version(), "probe_time": report_time()}
    for app in ("Microsoft Word", "Microsoft PowerPoint"):
        try:
            with open("/Applications/%s.app/Contents/Info.plist" % app, "rb") as f:
                plist = plistlib.load(f)
            env[app] = "%s (%s)" % (plist.get("CFBundleShortVersionString"), plist.get("CFBundleVersion"))
        except OSError as e:
            env[app] = "not found: %s" % e
    return env


def report_time() -> str:
    return datetime.datetime.now().isoformat(timespec="seconds")


def save(outdir: str, report: dict) -> None:
    with open(os.path.join(outdir, "report.json"), "w", encoding="utf-8") as f:
        json.dump(report, f, indent=2, ensure_ascii=False)
    lines = ["# Mac Office pasteboard probe", ""]
    lines += ["* %s: %s" % (k, v) for k, v in report["environment"].items()]
    for record in report["captures"]:
        lines += ["", "## Capture: %s (changeCount %s)" % (record["label"], record["change_count"]), "",
                  "| item.n | type | bytes | markers | start |", "|---|---|---|---|---|"]
        for i, types in enumerate(record["items"]):
            for j, info in enumerate(types):
                lines.append("| %d.%02d | `%s` | %s | %s | %s |" % (
                    i, j, info["type"], info["size"], ",".join(info["markers"]),
                    info["preview"][:90].replace("|", "\\|")))
        lines.append("")
        lines.append("App's Paste from Word/PowerPoint: %s" % (
            record.get("app_latex") or record.get("app_latex_error") or record.get("app_read_error")
            or "nothing (no MathML, no equation in the HTML)"))
    if report["pastes"]:
        lines += ["", "## Pastes into Office", "", "| candidate | Word | PowerPoint |", "|---|---|---|"]
        for p in report["pastes"]:
            cells = []
            for app in ("word", "powerpoint"):
                a = p.get(app) or {}
                cells.append((a.get("result", "") + (" (%s)" % a["note"] if a.get("note") else "")).replace("|", "/"))
            lines.append("| %s | %s | %s |" % (p["variant"], cells[0], cells[1]))
    with open(os.path.join(outdir, "report.md"), "w", encoding="utf-8") as f:
        f.write("\n".join(lines) + "\n")


def selfcheck() -> int:
    """No pasteboard use: the test MathML and the candidate list (without captures)."""
    mathml, _ = test_mathml(TEST_LATEX)
    print(mathml)
    test_mathml(TEST_ALIGNED)
    report = {"environment": environment(), "captures": [], "pastes": []}
    for name, description, entries, optional in variants({}, report):
        print("%-28s %s%s" % (name, "(optional) " if optional else "", description.replace("\n", " ")))
    print(json.dumps(report["environment"], indent=2))
    print("selector pasteboardItems registered:", bool(_SEL_ITEMS))
    return 0


def main(argv: List[str]) -> int:
    part = argv[0] if argv else "both"
    if part == "selfcheck":
        return selfcheck()
    if part not in ("both", "capture", "paste"):
        print(__doc__)
        return 2
    outdir = os.path.join(ROOT, "probe-out", datetime.datetime.now().strftime("%Y%m%d-%H%M%S"))
    os.makedirs(outdir, exist_ok=True)
    report = {"environment": environment(), "captures": [], "pastes": []}
    raw: dict = {}
    save(outdir, report)
    try:
        if part in ("both", "capture"):
            capture(outdir, report, raw)
        if part in ("both", "paste"):
            paste(outdir, report, raw)
    except (_Quit, KeyboardInterrupt, EOFError):
        print("\nStopped.")
    save(outdir, report)
    print("\nReport: %s" % os.path.join(outdir, "report.md"))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
