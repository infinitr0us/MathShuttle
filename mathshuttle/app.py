"""tkinter desktop UI: a local stand-in for https://johnmacfarlane.net/texmath.html
with direct Word / PowerPoint clipboard exchange."""
from __future__ import annotations

import ctypes
import json
import os
import queue
import re
import sys
import threading
import tkinter as tk
import traceback
from tkinter import filedialog
from tkinter import font as tkfont
from tkinter import messagebox, ttk
from typing import Optional

from . import __version__, clipboard, compat, hotkeys, latex_wrap, preview
from .convert import FORMAT_LABELS, MODES, Result, convert, macro_notes, office_copy
from .latex_wrap import WRAPPERS
from .pandoc_engine import (INSTALL_HINT, READ_FORMATS, WRITE_FORMATS, ConversionError, PandocNotFound, engine_description,
                            engine_report)

SAMPLE = "\\begin{align}\n  \\nabla \\cdot \\mathbf{E} &= \\frac{\\rho}{\\varepsilon_0} \\\\\n  \\nabla \\times \\mathbf{B} - \\frac{1}{c^2}\\frac{\\partial \\mathbf{E}}{\\partial t} &= \\mu_0 \\mathbf{J}\n\\end{align}"

MAC = sys.platform == "darwin"
# the modifier of the app's own shortcuts, and of copy / paste in Office, as the user knows it
KEY = "Cmd" if MAC else "Ctrl"
APP_DATA = (os.path.join(os.path.expanduser("~"), "Library", "Application Support") if MAC
            else os.environ.get("APPDATA", os.path.expanduser("~")))
SETTINGS_PATH = os.path.join(APP_DATA, "MathShuttle", "settings.json")
# where the tool kept them under its former name (Equation Tool): read once if there are no settings yet
OLD_SETTINGS_PATH = os.path.join(APP_DATA, "EquationTool", "settings.json")
DEFAULT_GEOMETRY = "1200x720"
DEBOUNCE_MS = 450
MAX_SAVED_INPUT = 200_000  # characters of input kept for the next start

_HOTKEY_HELP = """  Hotkey: not on macOS yet.""" if MAC else """\
  Hotkey (Ctrl+Alt+Shift+M, works in any program while MathShuttle runs, even minimised):
          an equation copied in Word / PowerPoint becomes LaTeX text on the clipboard;
          LaTeX text on the clipboard becomes an equation - then Ctrl+V in Office.  The key
          is "hotkey" in the settings file (%APPDATA%\\MathShuttle\\settings.json)."""

HELP = """MathShuttle %(version)s  -  texmath (via pandoc) running locally

INPUT (From)
  LaTeX   Any delimiters are recognised: bare, $...$, \\(...\\), $$...$$, \\[...\\],
          equation / align / gather / multline / eqnarray / alignat (+ starred),
          \\label, \\tag, \\nonumber, \\newcommand definitions, and several equations
          mixed with prose (each one is converted).  Input without any delimiter is
          one bare equation (even with $...$ inside \\text{} / \\mbox{}); it uses the Mode box.
          Plain-TeX font switches ({\\rm OOD}, \\cal L), \\notag, \\def / \\let macros,
          \\mathbbm, dcases, \\smash ... are rewritten for texmath, which does not know them.
  Macros...  your papers' macro files (math_commands.tex, a preamble) and extra
          definitions: \\newcommand, \\def, \\let, \\DeclareMathOperator,
          \\DeclarePairedDelimiter.  Every LaTeX input may then use those macros;
          LaTeX -> LaTeX output keeps them as you wrote them.
  MathML  <math> element(s), with or without the xmlns / XML prolog, also prefixed
          (<mml:math>) and with named entities (&InvisibleTimes;).
  OMML    <m:oMath> or <m:oMathPara>, alone or inside any Word XML.

OUTPUT (To)
  LaTeX   The "LaTeX wrapper" box picks the delimiters / environment.  Auto keeps
          the input's environment and otherwise uses \\[ \\] (display) or $ $ (inline).
          align <-> aligned, gather <-> gathered are converted as needed.
          Tidy LaTeX (MathML / OMML input): writes what an author would - \\mathrm{OOD},
          \\operatorname{softmax}, \\boldsymbol{\\theta}, \\binom, cases, a & = b - instead of
          texmath's literal rendering.
  MathML / OMML / eqn / Typst   Mode = Auto follows the input; Display / Inline force it.

WORD / POWERPOINT
  Copy for Word/PowerPoint   converts the input to MathML and places it on the clipboard
          in the "MathML" clipboard format; %(key)s+V in Word, PowerPoint or OneNote then
          inserts a native, editable equation (%(key)s+Shift+C).  align / aligned / split
          blocks arrive as an Office equation array, lined up at the = signs.
  Paste from Word/PowerPoint   reads the equation you copied in Word / PowerPoint
          (select the equation, %(key)s+C) and converts it to LaTeX (%(key)s+Shift+V).
%(hotkey)s
  Open file...   every equation of a .docx / .pptx / .tex / .md file, with where it is.

LaTeX preview: the LaTeX side of the conversion as LaTeX sets it (needs latex + dvipng on PATH).

%(key)s+Enter converts; Auto-convert re-runs the conversion as you type.
""" % {"version": __version__, "key": KEY, "hotkey": _HOTKEY_HELP}


class _POINT(ctypes.Structure):
    _fields_ = [("x", ctypes.c_long), ("y", ctypes.c_long)]


class _CGPoint(ctypes.Structure):
    _fields_ = [("x", ctypes.c_double), ("y", ctypes.c_double)]


def _on_a_display_macos(x: int, y: int) -> bool:
    """CGGetDisplaysWithPoint; Quartz's global coordinates are Tk's on macOS (from the top left corner
    of the main display, growing downwards)."""
    try:
        displays_with_point = ctypes.CDLL("/System/Library/Frameworks/CoreGraphics.framework/CoreGraphics").CGGetDisplaysWithPoint
    except (OSError, AttributeError):
        return True
    displays_with_point.argtypes = [_CGPoint, ctypes.c_uint32, ctypes.c_void_p, ctypes.POINTER(ctypes.c_uint32)]
    displays_with_point.restype = ctypes.c_int32  # CGError, 0 = success
    count = ctypes.c_uint32(0)
    error = displays_with_point(_CGPoint(x, y), 0, None, ctypes.byref(count))
    return error != 0 or count.value > 0  # if Quartz cannot tell, trust the saved position


def _on_a_monitor(x: int, y: int) -> bool:
    if MAC:
        return _on_a_display_macos(x, y)
    try:
        monitor_from_point = ctypes.windll.user32.MonitorFromPoint
    except AttributeError:  # neither Windows nor macOS: trust the saved position
        return True
    monitor_from_point.argtypes = [_POINT, ctypes.c_uint32]
    monitor_from_point.restype = ctypes.c_void_p  # HMONITOR
    return bool(monitor_from_point(_POINT(x, y), 0))  # 0 = MONITOR_DEFAULTTONULL


def _enable_dpi_awareness() -> None:
    try:
        ctypes.windll.shcore.SetProcessDpiAwareness(1)
    except Exception:
        try:
            ctypes.windll.user32.SetProcessDPIAware()
        except Exception:
            pass


class App(tk.Tk):
    def __init__(self) -> None:
        super().__init__()
        self.title("MathShuttle  -  LaTeX / MathML / OMML converter")
        self._seq = 0
        self._after_id: Optional[str] = None
        self._results: "queue.Queue[tuple]" = queue.Queue()
        self._polling = False
        self._running = 0  # conversion threads whose result has not been collected yet
        self._shown_args: Optional[tuple] = None  # conversion arguments of the result in the output pane
        self.settings = self._load_settings()
        # Tk's native macOS windowing, which has the Command modifier and the tk::mac:: hooks
        self.aqua = self.tk.call("tk", "windowingsystem") == "aqua"

        self._scale_fonts()
        self._build()
        self._apply_settings()
        self._bind_keys()
        self.protocol("WM_DELETE_WINDOW", self._on_close)
        self.after(50, self._startup_check)

    # ---- UI construction --------------------------------------------------------------------
    def _scale_fonts(self) -> None:
        try:
            dpi = self.winfo_fpixels("1i")
            self.tk.call("tk", "scaling", dpi / 72.0)
        except Exception:
            pass
        ui = tkfont.nametofont("TkDefaultFont")
        if MAC:
            # Tk's default font there is the system font at the system size, which is 13 at Tk 8.6's 72 dpi
            # and 10 at Tk 9's 96 dpi: it stays as it is, and the monospace font (Menlo comes with every
            # macOS; Consolas does not) gets the same size
            self.mono = tkfont.Font(family="Menlo", size=abs(int(ui.actual("size"))) or 13)
        else:
            self.mono = tkfont.Font(family="Consolas", size=11)
            ui.configure(size=10)
        self.option_add("*Font", ui)
        ttk.Style(self).configure("TButton", padding=(8, 3))

    def _build(self) -> None:
        pad = {"padx": 4, "pady": 3}
        self.src_var = tk.StringVar(value="latex")
        self.dst_var = tk.StringVar(value="omml")
        self.mode_var = tk.StringVar(value="auto")
        self.wrapper_var = tk.StringVar(value="auto")
        self.annotate_var = tk.BooleanVar(value=False)
        self.tidy_var = tk.BooleanVar(value=True)
        self.auto_var = tk.BooleanVar(value=True)
        self.hotkey_var = tk.BooleanVar(value=True)
        self.preview_var = tk.BooleanVar(value=True)
        self._latex_found: Optional[bool] = None  # unknown until the startup check
        self.hotkey = hotkeys.DEFAULT
        self._listener: Optional[hotkeys.HotkeyListener] = None
        self.macro_files: list = []  # macro files and extra definitions for LaTeX input (Macros...)
        self.macro_text = ""

        # --- option rows (fixed height) ---
        top = ttk.Frame(self)
        top.pack(fill="x", padx=6, pady=(6, 0))
        top.columnconfigure(4, weight=1)

        ttk.Label(top, text="From:").grid(row=0, column=0, sticky="w", **pad)
        self.src_box = self._combo(top, [(k, FORMAT_LABELS[k]) for k in READ_FORMATS], self.src_var, 18)
        self.src_box.grid(row=0, column=1, sticky="w", **pad)
        ttk.Label(top, text="Mode:").grid(row=0, column=2, sticky="w", **pad)
        self.mode_box = self._combo(top, MODES, self.mode_var, 9)
        self.mode_box.grid(row=0, column=3, sticky="w", **pad)
        inputs = ttk.Frame(top)  # what applies to the input: LaTeX macros, tidying of Office's LaTeX
        inputs.grid(row=0, column=4, sticky="w")
        self.macros_button = ttk.Button(inputs, text="Macros...", command=self.edit_macros)
        self.macros_button.pack(side="left", padx=(12, 4))
        self.tidy_chk = ttk.Checkbutton(inputs, text="Tidy LaTeX from Office", variable=self.tidy_var,
                                        command=self._schedule)
        self.tidy_chk.pack(side="left", padx=(12, 4))
        ttk.Button(top, text="\u21c4 Swap", command=self.swap, width=8).grid(row=0, column=5, rowspan=2, padx=8)
        ttk.Button(top, text="?", width=3, command=self.show_help).grid(row=0, column=6, rowspan=2, padx=(8, 0))

        ttk.Label(top, text="To:").grid(row=1, column=0, sticky="w", **pad)
        self.dst_box = self._combo(top, [(k, FORMAT_LABELS[k]) for k in WRITE_FORMATS], self.dst_var, 18)
        self.dst_box.grid(row=1, column=1, sticky="w", **pad)
        ttk.Label(top, text="LaTeX wrapper:").grid(row=1, column=2, sticky="w", **pad)
        opts = ttk.Frame(top)
        opts.grid(row=1, column=3, columnspan=2, sticky="w")
        self.wrapper_box = self._combo(opts, WRAPPERS, self.wrapper_var, 32)
        self.wrapper_box.pack(side="left", **pad)
        self.annotate_chk = ttk.Checkbutton(opts, text="TeX annotation", variable=self.annotate_var,
                                            command=self._schedule)
        self.annotate_chk.pack(side="left", padx=(12, 4))

        # third row: how the app works (the button bar below keeps only actions, so that it fits)
        ttk.Label(top, text="Options:").grid(row=2, column=0, sticky="w", **pad)
        switches = ttk.Frame(top)
        switches.grid(row=2, column=1, columnspan=5, sticky="w")
        ttk.Checkbutton(switches, text="Auto-convert", variable=self.auto_var, command=self._schedule).pack(side="left", **pad)
        self.preview_chk = ttk.Checkbutton(switches, text="LaTeX preview", variable=self.preview_var,
                                           command=self._toggle_preview, state="disabled")
        self.preview_chk.pack(side="left", padx=(14, 4))
        self.hotkey_chk = ttk.Checkbutton(switches, variable=self.hotkey_var, command=self._toggle_hotkey)
        self.hotkey_chk.pack(side="left", padx=(14, 4))

        # --- status bar and button bar are packed before the panes so they always stay visible ---
        # Two panes: conversion progress / messages on the left, the engine version (pandoc +
        # texmath) permanently on the right, so the messages never hide it.
        bar = ttk.Frame(self)
        bar.pack(fill="x", side="bottom")
        self.engine_var = tk.StringVar(value="")
        ttk.Label(bar, textvariable=self.engine_var, relief="sunken", padding=(6, 2)).pack(side="right")
        self.status_var = tk.StringVar(value="")
        self.status_label = ttk.Label(bar, textvariable=self.status_var, anchor="w", relief="sunken", padding=(6, 2))
        self.status_label.pack(side="left", fill="x", expand=True)

        bottom = ttk.Frame(self)
        bottom.pack(fill="x", side="bottom", padx=6, pady=(0, 4))
        self._bottom_bar = bottom
        ttk.Button(bottom, text="Convert", command=self.convert_now).pack(side="left", **pad)
        ttk.Button(bottom, text="Clear", command=self.clear).pack(side="left", padx=(10, 4))
        ttk.Button(bottom, text="Open file...", command=self.open_document).pack(side="left", padx=(10, 4))

        # --- the LaTeX preview (above the buttons; shown when a LaTeX installation is found) ---
        self.preview_frame = tk.Frame(self, bg="white", height=10, highlightthickness=1, highlightbackground="#c8c8c8")
        self.preview_label = tk.Label(self.preview_frame, bg="white", anchor="w", justify="left", padx=8, pady=6)
        self.preview_label.pack(fill="x")
        self._preview_image = None  # keeps the PhotoImage alive
        self._preview_seq = 0
        self._last_result: Optional[tuple] = None  # (Result, conversion args) of the output on show
        ttk.Button(bottom, text="Paste from Word/PowerPoint", command=self.paste_from_office).pack(side="right", **pad)
        ttk.Button(bottom, text="Copy for Word/PowerPoint", command=self.copy_for_office).pack(side="right", **pad)
        ttk.Button(bottom, text="Copy output", command=self.copy_output).pack(side="right", **pad)

        # --- input / output panes (take the remaining space) ---
        paned = ttk.PanedWindow(self, orient="horizontal")
        paned.pack(fill="both", expand=True, padx=6, pady=6)
        self.input = self._text(paned)
        self.output = self._text(paned)
        paned.add(self.input.master, weight=1)
        paned.add(self.output.master, weight=1)
        self.output.tag_configure("error", foreground="#b00020")

        for var in (self.src_var, self.dst_var, self.mode_var, self.wrapper_var):
            var.trace_add("write", lambda *_: self._on_option_change())
        self.input.bind("<<Modified>>", self._on_input_modified)

    def _combo(self, parent, items, var, width):
        keys = [k for k, _ in items]
        labels = [lbl for _, lbl in items]
        box = ttk.Combobox(parent, values=labels, state="readonly", width=width)

        def sync_from_var(*_):
            key = var.get()
            if key in keys:
                box.current(keys.index(key))

        box.bind("<<ComboboxSelected>>", lambda e: var.set(keys[box.current()]))
        var.trace_add("write", sync_from_var)
        sync_from_var()
        return box

    def _text(self, parent) -> tk.Text:
        frame = ttk.Frame(parent)
        text = tk.Text(frame, wrap="word", undo=True, font=self.mono, padx=6, pady=6, width=40, height=8)
        scroll = ttk.Scrollbar(frame, command=text.yview)
        text.configure(yscrollcommand=scroll.set)
        scroll.pack(side="right", fill="y")
        text.pack(side="left", fill="both", expand=True)
        text.bind("<Control-a>", lambda e: (text.tag_add("sel", "1.0", "end-1c"), "break")[1])
        if self.aqua:
            text.bind("<Command-a>", lambda e: (text.tag_add("sel", "1.0", "end-1c"), "break")[1])
        return text

    def _bind_keys(self) -> None:
        self.bind_all("<Control-Return>", lambda e: self.convert_now())
        for w in (self.input, self.output):  # do not also insert a newline
            w.bind("<Control-Return>", lambda e: (self.convert_now(), "break")[1])
        self.bind_all("<Control-Shift-C>", lambda e: self.copy_for_office())
        self.bind_all("<Control-Shift-c>", lambda e: self.copy_for_office())
        self.bind_all("<Control-Shift-V>", lambda e: self.paste_from_office())
        self.bind_all("<Control-Shift-v>", lambda e: self.paste_from_office())
        self.bind_all("<F1>", lambda e: self.show_help())
        if self.aqua:  # the Command key too: what Mac users press (the Control keys above keep working)
            self.bind_all("<Command-Return>", lambda e: self.convert_now())
            for w in (self.input, self.output):
                w.bind("<Command-Return>", lambda e: (self.convert_now(), "break")[1])
            self.bind_all("<Command-Shift-C>", lambda e: self.copy_for_office())
            self.bind_all("<Command-Shift-c>", lambda e: self.copy_for_office())
            self.bind_all("<Command-Shift-V>", lambda e: self.paste_from_office())
            self.bind_all("<Command-Shift-v>", lambda e: self.paste_from_office())
            # Quit in the application menu (Cmd+Q) would end Tk without WM_DELETE_WINDOW, losing the
            # settings and the input; Help in the Help menu opens this app's help
            self.createcommand("tk::mac::Quit", self._on_close)
            self.createcommand("tk::mac::ShowHelp", self.show_help)

    # ---- settings -----------------------------------------------------------------------------
    def _load_settings(self) -> dict:
        path = SETTINGS_PATH
        if not os.path.exists(path) and SETTINGS_PATH.endswith(os.path.join("MathShuttle", "settings.json")):
            path = OLD_SETTINGS_PATH  # first start after the rename; they are saved under the new name on exit
        try:
            with open(path, encoding="utf-8") as f:
                data = json.load(f)
        except Exception:
            return {}
        return data if isinstance(data, dict) else {}

    def _restore_geometry(self, geometry) -> None:
        """Apply a saved geometry; drop a position that is on no monitor any more (a disconnected
        second screen), and ignore a malformed value rather than failing to start."""
        m = re.fullmatch(r"(\d+)x(\d+)([+-])(-?\d+)([+-])(-?\d+)", geometry) if isinstance(geometry, str) else None
        # An offset from the right / bottom edge (-X-Y: only from an edited settings file, Tk reports a
        # position the app set itself as +X+Y) cannot be checked against the monitors: keep the size only.
        if m and ("-" in m.group(3, 5) or not _on_a_monitor(int(m.group(4)) + 60, int(m.group(6)) + 15)):
            geometry = "%sx%s" % m.group(1, 2)  # (x + 60, y + 15) is a point in the title bar
        try:
            self.geometry(geometry)
        except tk.TclError:
            self.geometry(DEFAULT_GEOMETRY)

    def _apply_settings(self) -> None:
        s = self.settings
        for var, key, allowed in ((self.src_var, "src", READ_FORMATS), (self.dst_var, "dst", WRITE_FORMATS),
                                  (self.mode_var, "mode", [k for k, _ in MODES]),
                                  (self.wrapper_var, "wrapper", [k for k, _ in WRAPPERS])):
            if s.get(key) in allowed:
                var.set(s[key])
        self.auto_var.set(bool(s.get("auto_convert", True)))
        self.annotate_var.set(bool(s.get("annotate", False)))
        self.tidy_var.set(bool(s.get("tidy", True)))
        self.hotkey_var.set(bool(s.get("hotkey_enabled", True)))
        self.preview_var.set(bool(s.get("preview", True)))
        if isinstance(s.get("hotkey"), str):
            try:
                hotkeys.parse(s["hotkey"])
                self.hotkey = s["hotkey"]
            except ValueError:
                pass  # keep the default; the settings file was edited by hand
        self.hotkey_chk.configure(text="Hotkey %s" % self.hotkey)
        if hotkeys.UNAVAILABLE:
            self.hotkey_var.set(False)
            self.hotkey_chk.configure(text="Hotkey (%s)" % hotkeys.UNAVAILABLE, state="disabled")
        files = s.get("macro_files")
        self.macro_files = [f for f in files if isinstance(f, str)] if isinstance(files, list) else []
        self.macro_text = s.get("macro_text") if isinstance(s.get("macro_text"), str) else ""
        self._update_macros_label()
        self._restore_geometry(s.get("geometry", DEFAULT_GEOMETRY))
        self.minsize(960, 420)
        self.input.insert("1.0", s.get("input") or SAMPLE)
        self.input.edit_modified(False)
        self._update_option_states()

    def _save_settings(self) -> None:
        text = self.input.get("1.0", "end-1c")
        data = {"src": self.src_var.get(), "dst": self.dst_var.get(), "mode": self.mode_var.get(),
                "wrapper": self.wrapper_var.get(), "auto_convert": self.auto_var.get(),
                "annotate": self.annotate_var.get(), "tidy": self.tidy_var.get(),
                "hotkey": self.hotkey,
                # where there is no hotkey, the box is off without the user's say: keep their choice
                "hotkey_enabled": (bool(self.settings.get("hotkey_enabled", True)) if hotkeys.UNAVAILABLE
                                   else self.hotkey_var.get()),
                "preview": self.preview_var.get(),
                "macro_files": self.macro_files, "macro_text": self.macro_text, "geometry": self.geometry(),
                # whole or not at all: a cut could land inside an equation, and a very large input
                # (a whole document.xml) would be reloaded and converted again at every start
                "input": text if len(text) <= MAX_SAVED_INPUT else ""}
        try:
            os.makedirs(os.path.dirname(SETTINGS_PATH), exist_ok=True)
            with open(SETTINGS_PATH, "w", encoding="utf-8") as f:
                json.dump(data, f, indent=1)
        except Exception:
            pass

    def _on_close(self) -> None:
        self._save_settings()
        self._stop_hotkey()
        self.destroy()

    # ---- events ---------------------------------------------------------------------------------
    def _startup_check(self) -> None:
        try:
            self.engine_var.set(engine_description())
        except PandocNotFound as e:
            self.engine_var.set("pandoc not found")
            self._set_status("pandoc not found  -  install it (%s) "
                             "or set MATHSHUTTLE_PANDOC, then restart" % INSTALL_HINT, error=True)
            messagebox.showerror("pandoc not found", str(e))
            return
        except Exception as e:  # pragma: no cover
            self.engine_var.set("engine check failed")
            self._set_status("pandoc check failed: %s" % e, error=True)
            return
        self._set_status("Ready")
        self._toggle_hotkey()
        self._in_background(preview.tools, self._latex_checked)  # latex --version takes a moment
        self._schedule()

    def _update_option_states(self) -> None:
        self.wrapper_box.configure(state="readonly" if self.dst_var.get() == "latex" else "disabled")
        self.annotate_chk.configure(state="normal" if self.dst_var.get() == "mathml" else "disabled")
        self.tidy_chk.configure(state="normal" if self.src_var.get() != "latex" else "disabled")

    def _macro_key(self) -> tuple:
        """The macro files (with their modification times: an edited file gives another result), the text."""
        return (tuple(self.macro_files), self.macro_text, compat.file_stamps(self.macro_files))

    def _macros(self, key: Optional[tuple] = None) -> Optional[compat.Macros]:
        files, text, _ = key or self._macro_key()
        return compat.load_macros(files, text)

    def _update_macros_label(self) -> None:
        macros = self._macros()
        self.macros_button.configure(text="Macros (%d)..." % len(macros) if macros is not None else "Macros...")

    def _on_option_change(self) -> None:
        self._update_option_states()
        self._schedule()

    def _on_input_modified(self, _event=None) -> None:
        if self.input.edit_modified():
            self.input.edit_modified(False)
            self._schedule()

    def _schedule(self) -> None:
        if self._after_id:
            self.after_cancel(self._after_id)
            self._after_id = None
        if self.auto_var.get():
            self._after_id = self.after(DEBOUNCE_MS, self.convert_now)

    # ---- actions --------------------------------------------------------------------------------
    def _conversion_args(self) -> tuple:
        """(text, src, dst, mode, wrapper, annotate, tidy, macro key): everything a result depends on."""
        return (self.input.get("1.0", "end-1c"), self.src_var.get(), self.dst_var.get(), self.mode_var.get(),
                self.wrapper_var.get(), self.annotate_var.get(), self.tidy_var.get(), self._macro_key())

    def _new_conversion(self) -> None:
        """Results of conversions still running are stale from now on, and so is a pending debounced
        one (typing, option change): the conversion about to start replaces them."""
        if self._after_id:
            self.after_cancel(self._after_id)
            self._after_id = None
        self._seq += 1

    def convert_now(self) -> None:
        self._new_conversion()
        args = self._conversion_args()
        if not args[0].strip():
            self._set_output("")
            self._set_status("")
            self._clear_preview()
            return
        self._start(args)

    def _start(self, args, swap: bool = False) -> None:
        """Convert `args` in a background thread; with `swap`, the result completes a Swap."""
        self._set_status("Converting...")
        self._running += 1
        threading.Thread(target=self._worker, args=(self._seq, args, swap), daemon=True).start()
        if not self._polling:
            self._polling = True
            self.after(30, self._poll_results)

    def _worker(self, seq: int, args, swap: bool) -> None:
        """Runs in a background thread; only ever touches the queue."""
        try:
            result: object = convert(*args[:6], macros=self._macros(args[7]), tidy=args[6])
        except (ConversionError, PandocNotFound) as e:
            result = e
        except Exception:  # pragma: no cover
            result = ConversionError("Unexpected error:\n" + traceback.format_exc())
        self._results.put((seq, args, result, swap))

    def _poll_results(self) -> None:
        while True:
            try:
                seq, args, result, swap = self._results.get_nowait()
            except queue.Empty:
                break
            self._running -= 1
            self._finish(seq, args, result, swap)
        if self._running:
            self.after(30, self._poll_results)
        else:
            self._polling = False

    def _finish(self, seq: int, args, result, swap: bool = False) -> None:
        if seq != self._seq:
            return  # a newer conversion has been started, or the input was cleared
        if swap and self._conversion_args() != args:
            # input or options changed while converting (auto-convert off: no newer conversion)
            self._set_status("Swap cancelled: the input or the options changed", error=True)
            return
        if isinstance(result, Exception):
            self._clear_preview()  # the picture of the previous result no longer applies
            self._set_output(str(result), error=True)
            self._set_status("Cannot swap: the input does not convert" if swap else "Conversion failed", error=True)
            return
        assert isinstance(result, Result)
        if swap:
            self._swap_with(args, result.text)
            return
        self._set_output(result.text)
        self._shown_args = args
        self._last_result = (result, args)
        self._start_preview(result, args)
        n = len(result.equations)
        mode = "display" if result.equations[0].display else "inline"
        summary = "%s \u2192 %s  (%s%s)" % (FORMAT_LABELS[args[1]], FORMAT_LABELS[args[2]],
                                             "%d equations, " % n if n > 1 else "", mode)
        self._set_status("  ".join([summary] + result.notes))

    def copy_output(self) -> None:
        text = self.output.get("1.0", "end-1c")
        if not text.strip():
            self._set_status("Nothing to copy", error=True)
            return
        self.clipboard_clear()
        self.clipboard_append(text)
        self._set_status("Output copied to the clipboard as text")

    def copy_for_office(self) -> None:
        text = self.input.get("1.0", "end-1c")
        if not text.strip():
            self._set_status("Nothing to copy", error=True)
            return
        try:
            copy = office_copy(text, self.src_var.get(), self.mode_var.get(), self._macros(), self.tidy_var.get())
            clipboard.copy_mathml_for_office(copy.mathml)  # the plain text must be the MathML too (see there)
        except (ConversionError, PandocNotFound, clipboard.ClipboardError) as e:
            self._set_output(str(e), error=True)
            self._set_status("Could not copy the equation", error=True)
            return
        msg = "Equation copied  -  press %s+V in Word / PowerPoint / OneNote to insert it as a native equation" % KEY
        self._set_status("   ".join([msg] + copy.notes))

    def paste_from_office(self) -> None:
        try:
            found = clipboard.read_office_equation()
        except clipboard.ClipboardError as e:
            self._set_status(str(e), error=True)
            return
        if not found:
            self._set_status("No equation on the clipboard  -  select an equation in Word / PowerPoint and press %s+C first"
                             % KEY, error=True)
            return
        src, text = found  # MathML, or OMML from the HTML that Word copies with a selection of text
        self.src_var.set(src)
        if self.dst_var.get() == src:
            self.dst_var.set("latex")
        self.input.delete("1.0", "end")
        self.input.insert("1.0", text)
        self.input.edit_modified(False)
        self.convert_now()

    def swap(self) -> None:
        dst = self.dst_var.get()
        if dst not in READ_FORMATS:
            self._set_status("Cannot swap: %s cannot be used as input" % FORMAT_LABELS[dst], error=True)
            return
        args = self._conversion_args()
        if self._shown_args == args:
            self._swap_with(args, self.output.get("1.0", "end-1c"))  # the current result (possibly edited by hand)
        elif args[0].strip():
            # The output pane is stale (auto-convert off, or a conversion still pending) or shows an
            # error: it must not become input in the new format.  Convert the current input first, in
            # the background like any conversion; _finish completes the swap.
            self._new_conversion()
            self._start(args, swap=True)
        else:
            self._swap_with(args, "")

    def _swap_with(self, args, output: str) -> None:
        """Swap From and To of `args`; `output` (their result) becomes the input."""
        self.src_var.set(args[2])
        self.dst_var.set(args[1])
        if output.strip():
            self.input.delete("1.0", "end")
            self.input.insert("1.0", output)
            self.input.edit_modified(False)
        self.convert_now()

    def clear(self) -> None:
        self._seq += 1  # drop the result of a conversion that is still running
        self.input.delete("1.0", "end")
        self.input.edit_modified(False)
        self._set_output("")
        self._set_status("")
        self._clear_preview()
        self.input.focus_set()

    # ---- the LaTeX preview ------------------------------------------------------------------------
    PREVIEW_MAX_HEIGHT = 240

    def _latex_checked(self, found, error) -> None:
        self._latex_found = bool(found) and error is None
        if self._latex_found:
            self.preview_chk.configure(state="normal")
            preview.prune()
        else:
            self.preview_chk.configure(state="disabled", text="LaTeX preview (no LaTeX installation found)")
        self._toggle_preview()

    def _toggle_preview(self) -> None:
        if self._latex_found and self.preview_var.get():
            if not self.preview_frame.winfo_ismapped():
                # packed after the button bar (both at the bottom): it sits above the buttons
                self.preview_frame.pack(fill="x", side="bottom", padx=6, pady=(0, 2), after=self._bottom_bar)
            if self._last_result is not None and self._shown_args == self._last_result[1]:
                self._start_preview(*self._last_result)  # the result on show (not converted again: it may be edited)
        else:
            self._clear_preview()
            self.preview_frame.pack_forget()

    def _clear_preview(self) -> None:
        """No picture (Clear, an empty input, an error); renders still running are stale."""
        self._preview_seq += 1
        self._preview_image = None
        self.preview_label.configure(image="", text="")

    def _start_preview(self, result, args) -> None:
        """Draw the LaTeX side of a result in the background: the input when converting from LaTeX,
        else the LaTeX the input reads as (tidied, as Paste from Office gives it)."""
        if not (self._latex_found and self.preview_var.get()):
            return
        self._preview_seq += 1
        seq = self._preview_seq
        src, tidy, macros = args[1], args[6], self._macros(args[7])
        equations = list(result.equations[:6])
        dpi = max(96, int(self.winfo_fpixels("1i") * 1.15))

        def work():
            from .convert import tidied
            eqs = [tidied(eq) if src != "latex" and tidy else eq for eq in equations]
            definitions = preview.definitions_for(" ".join(e.tex for e in eqs), macros if src == "latex" else None)
            return preview.render(eqs, definitions, dpi=dpi), len(result.equations)

        self._in_background(work, lambda value, error: self._show_preview(seq, value, error))

    def _show_preview(self, seq, value, error) -> None:
        if seq != self._preview_seq:
            return  # a newer result is being drawn
        if error is not None:
            self._preview_image = None
            message = str(error) if isinstance(error, (preview.PreviewError, ConversionError)) else repr(error)
            self.preview_label.configure(image="", text="Preview: " + message.splitlines()[0], fg="#8b1a1a")
            return
        path, total = value
        try:
            image = tk.PhotoImage(file=path)
        except tk.TclError as e:
            self.preview_label.configure(image="", text="Preview: %s" % e, fg="#8b1a1a")
            return
        width = max(200, self.winfo_width() - 40)
        factor = max(1, -(-image.width() // width), -(-image.height() // self.PREVIEW_MAX_HEIGHT))
        if factor > 1:
            image = image.subsample(factor)
        self._preview_image = image
        more = "  (first 6 of %d equations)" % total if total > 6 else ""
        self.preview_label.configure(image=image, text=more, compound="left", fg="#555555")

    # ---- the hotkey: shuttle the clipboard ------------------------------------------------------
    def _toggle_hotkey(self) -> None:
        """Listen for the hotkey while the box is ticked (and the app runs, even minimised)."""
        self._stop_hotkey()
        if not self.hotkey_var.get() or hotkeys.UNAVAILABLE:
            return
        listener = hotkeys.HotkeyListener(self.hotkey)
        error = listener.start_and_wait()
        if error:
            self.hotkey_var.set(False)
            self._set_status("Hotkey off: %s (change \"hotkey\" in %s)" % (error, SETTINGS_PATH), error=True)
            return
        self._listener = listener
        self._poll_hotkey(listener)

    def _stop_hotkey(self) -> None:
        if self._listener is not None:
            self._listener.stop()
            self._listener = None

    def _poll_hotkey(self, listener) -> None:
        if listener is not self._listener:
            return  # replaced or stopped
        pressed = False
        while True:
            try:
                listener.presses.get_nowait()
                pressed = True
            except queue.Empty:
                break
        if pressed:
            self.shuttle_clipboard()
        self.after(120, self._poll_hotkey, listener)

    def shuttle_clipboard(self, done=None) -> None:
        """The hotkey: an equation copied in Office (or MathML / OMML text) becomes LaTeX text; LaTeX text
        becomes an equation to paste into Office.  Uses the Mode, LaTeX wrapper, Macros and Tidy settings."""
        mode, wrapper, tidy, macros = self.mode_var.get(), self.wrapper_var.get(), self.tidy_var.get(), self._macros()

        def work():
            # what was copied: if something else is copied while this converts, it is not overwritten
            sequence = clipboard.sequence_number()
            found = clipboard.read_office_equation()
            text = clipboard.get_text() or ""
            if found:
                src, text = found
            else:
                src = ("mathml" if re.search(r"<(\w+:)?math\b", text) else
                       "omml" if re.search(r"<m:oMath", text) else "latex")
            if src != "latex":
                latex = convert(text, src, "latex", mode, wrapper, tidy=tidy).text
                clipboard.set_text(latex, expect_sequence=sequence)
                return "LaTeX copied: %s" % " ".join(latex.split())[:70], False
            if not text.strip():
                return "Nothing to convert: copy LaTeX, or an equation in Word / PowerPoint, first", True
            copy = office_copy(text, "latex", mode, macros)
            clipboard.copy_mathml_for_office(copy.mathml, expect_sequence=sequence)
            return "   ".join(["Equation ready: press %s+V in Word / PowerPoint" % KEY] + copy.notes), False

        def finished(value, error):
            if error is not None:
                lines = str(error).strip().splitlines() or [repr(error)]
                message, failed = "Could not convert the clipboard: %s" % lines[-1], True
            else:
                message, failed = value
            self._set_status(message, error=failed)
            Toast(self, message, error=failed)
            if done:
                done(message, failed)

        self._in_background(work, finished)

    # ---- documents ------------------------------------------------------------------------------
    def _in_background(self, work, done) -> None:
        """Run `work()` in a thread; call `done(value, error)` in the Tk thread when it ends."""
        result: dict = {}

        def run():
            try:
                result["value"] = work()
            except Exception as e:  # reported by `done`
                result["error"] = e

        thread = threading.Thread(target=run, daemon=True)
        thread.start()

        def poll():
            if thread.is_alive():
                self.after(50, poll)
            else:
                done(result.get("value"), result.get("error"))

        self.after(50, poll)

    def open_document(self, path: Optional[str] = None) -> None:
        """List the equations of a .docx / .pptx / .tex / .md file (Open file...)."""
        from .documents import FILE_TYPES, read_document
        path = path or filedialog.askopenfilename(parent=self, title="Open a document with equations",
                                                  filetypes=FILE_TYPES + [("All files", "*.*")])
        if not path:
            return
        self._set_status("Reading %s..." % os.path.basename(path))

        def done(doc, error):
            if error is not None:
                message = str(error) if isinstance(error, (ConversionError, PandocNotFound)) else repr(error)
                self._set_status("Could not read %s: %s" % (os.path.basename(path), message.splitlines()[0]), error=True)
                return
            self._set_status("%d equations in %s" % (len(doc.found), os.path.basename(path)))
            self._show_equations(doc)

        self._in_background(lambda: read_document(path), done)

    def _show_equations(self, doc) -> "EquationList":
        return EquationList(self, doc)

    def edit_macros(self) -> None:
        """Macro files and extra definitions applied to every LaTeX input."""
        win = tk.Toplevel(self)
        win.title("MathShuttle - macros")
        win.transient(self)
        pad = {"padx": 8, "pady": 4}
        ttk.Label(win, text="Macro files (definitions are read from them; everything else is ignored):").pack(anchor="w", **pad)
        files = list(self.macro_files)
        listbox = tk.Listbox(win, height=5, width=90, font=self.mono)
        listbox.pack(fill="x", **pad)
        for f in files:
            listbox.insert("end", f)
        row = ttk.Frame(win)
        row.pack(fill="x", **pad)

        def add():
            for path in filedialog.askopenfilenames(parent=win, title="Macro files",
                                                    filetypes=[("LaTeX", "*.tex *.sty *.cls"), ("All files", "*.*")]):
                path = os.path.normpath(path)
                if path not in files:
                    files.append(path)
                    listbox.insert("end", path)
            refresh()

        def remove():
            for i in reversed(listbox.curselection()):
                del files[i]
                listbox.delete(i)
            refresh()

        ttk.Button(row, text="Add files...", command=add).pack(side="left")
        ttk.Button(row, text="Remove", command=remove).pack(side="left", padx=6)
        ttk.Label(win, text="More definitions (\\newcommand, \\def, \\let, \\DeclareMathOperator, \\DeclarePairedDelimiter):").pack(anchor="w", **pad)
        text = tk.Text(win, height=8, width=90, font=self.mono, undo=True)
        text.insert("1.0", self.macro_text)
        text.pack(fill="both", expand=True, **pad)
        info = tk.StringVar()
        ttk.Label(win, textvariable=info, wraplength=700, justify="left").pack(anchor="w", **pad)

        def refresh(*_):
            macros = compat.load_macros(files, text.get("1.0", "end-1c"))
            if macros is None:
                info.set("No macros: LaTeX input uses only what it defines itself.")
                return
            names = sorted(macros.definitions)
            shown = ", ".join(names[:30]) + (" ..." if len(names) > 30 else "")
            info.set("\n".join(["%d definitions: %s" % (len(names), shown)] + macro_notes(macros)))

        text.bind("<KeyRelease>", refresh)
        refresh()

        def ok():
            self.macro_files = list(files)
            self.macro_text = text.get("1.0", "end-1c")
            self._update_macros_label()
            self._save_settings()
            win.destroy()
            self._schedule()

        buttons = ttk.Frame(win)
        buttons.pack(fill="x", **pad)
        ttk.Button(buttons, text="Cancel", command=win.destroy).pack(side="right")
        ttk.Button(buttons, text="OK", command=ok).pack(side="right", padx=6)

    def show_help(self) -> None:
        try:
            engine = engine_report()
        except (PandocNotFound, ConversionError) as e:
            engine = str(e)
        win = tk.Toplevel(self)
        win.title("MathShuttle - help")
        txt = tk.Text(win, wrap="word", font=self.mono, padx=10, pady=10, width=92, height=32)
        txt.insert("1.0", HELP + "\nENGINE\n" + "".join("  %s\n" % line for line in engine.splitlines()))
        txt.configure(state="disabled")
        txt.pack(fill="both", expand=True)
        ttk.Button(win, text="Close", command=win.destroy).pack(pady=6)
        win.transient(self)

    # ---- helpers --------------------------------------------------------------------------------
    def _set_output(self, text: str, error: bool = False) -> None:
        self._shown_args = None
        self.output.delete("1.0", "end")
        self.output.insert("1.0", text, ("error",) if error else ())

    def _set_status(self, text: str, error: bool = False) -> None:
        self.status_var.set(text)
        self.status_label.configure(foreground="#b00020" if error else "")


class Toast(tk.Toplevel):
    """A short message at the bottom right of the screen that does not take the focus (the user is
    working in Word / PowerPoint when the hotkey is pressed)."""

    def __init__(self, app: App, text: str, error: bool = False, ms: int = 2600) -> None:
        super().__init__(app)
        self.withdraw()
        self.overrideredirect(True)
        tk.Label(self, text="MathShuttle: " + text, padx=14, pady=9, wraplength=520, justify="left",
                 bg="#8b1a1a" if error else "#202124", fg="white").pack()
        self.update_idletasks()
        self.geometry("+%d+%d" % (self.winfo_screenwidth() - self.winfo_reqwidth() - 24,
                                  self.winfo_screenheight() - self.winfo_reqheight() - 72))
        try:
            user32 = ctypes.windll.user32
            user32.GetParent.restype = ctypes.c_void_p
            user32.GetParent.argtypes = [ctypes.c_void_p]
            user32.GetWindowLongW.argtypes = [ctypes.c_void_p, ctypes.c_int]
            user32.SetWindowLongW.argtypes = [ctypes.c_void_p, ctypes.c_int, ctypes.c_long]
            hwnd = user32.GetParent(self.winfo_id()) or self.winfo_id()
            # WS_EX_NOACTIVATE | WS_EX_TOOLWINDOW: never the active window, no taskbar button
            user32.SetWindowLongW(hwnd, -20, user32.GetWindowLongW(hwnd, -20) | 0x08000000 | 0x80)
        except (AttributeError, OSError):
            pass
        self.attributes("-topmost", True)
        self.deiconify()  # (a program in the background cannot take the foreground from Word anyway)
        self.after(ms, self.destroy)


class EquationList(tk.Toplevel):
    """The equations of a document: use one as input, copy it for Office or as LaTeX, export all."""

    def __init__(self, app: App, doc) -> None:
        super().__init__(app)
        self.app, self.doc = app, doc
        self.title("MathShuttle - equations in %s" % os.path.basename(doc.path))
        self.transient(app)
        pad = {"padx": 6, "pady": 4}
        summary = "%d equation%s" % (len(doc.found), "" if len(doc.found) == 1 else "s")
        self.info = tk.StringVar(value="   ".join([summary] + doc.notes))
        ttk.Label(self, textvariable=self.info, wraplength=900, justify="left").pack(anchor="w", **pad)

        style = ttk.Style(self)
        style.configure("Equations.Treeview", rowheight=int(app.mono.metrics("linespace") * 1.35), font=app.mono)
        frame = ttk.Frame(self)
        frame.pack(fill="both", expand=True, **pad)
        self.tree = ttk.Treeview(frame, columns=("where", "equation", "context"), show="headings",
                                 selectmode="browse", style="Equations.Treeview", height=14)
        for column, title, width in (("where", "Where", 220), ("equation", "Equation (LaTeX)", 520),
                                     ("context", "Context", 300)):
            self.tree.heading(column, text=title, anchor="w")
            self.tree.column(column, width=width, anchor="w")
        scroll = ttk.Scrollbar(frame, command=self.tree.yview)
        self.tree.configure(yscrollcommand=scroll.set)
        scroll.pack(side="right", fill="y")
        self.tree.pack(side="left", fill="both", expand=True)
        for i, found in enumerate(doc.found):
            self.tree.insert("", "end", iid=str(i),
                             values=(found.where, " ".join(found.equation.tex.split()), found.context))
        if doc.found:
            self.tree.selection_set("0")
            self.tree.focus("0")
        self.tree.bind("<Double-1>", lambda e: self.use())
        self.tree.bind("<Return>", lambda e: self.use())

        buttons = ttk.Frame(self)
        buttons.pack(fill="x", **pad)
        ttk.Button(buttons, text="Use as input", command=self.use).pack(side="left")
        ttk.Button(buttons, text="Copy for Word/PowerPoint", command=self.copy_for_office).pack(side="left", padx=6)
        ttk.Button(buttons, text="Copy LaTeX", command=self.copy_latex).pack(side="left")
        ttk.Button(buttons, text="Export all as .tex...", command=self.export).pack(side="left", padx=6)
        ttk.Button(buttons, text="Close", command=self.destroy).pack(side="right")

    def _selected(self):
        selection = self.tree.selection()
        if not selection:
            self.info.set("Select an equation first")
            return None
        return self.doc.found[int(selection[0])]

    @staticmethod
    def _latex(found) -> str:
        return latex_wrap.wrap(found.equation.tex, "auto", found.equation.display)

    def use(self) -> None:
        found = self._selected()
        if found is None:
            return
        app = self.app
        app.src_var.set("latex")
        app.input.delete("1.0", "end")
        app.input.insert("1.0", self._latex(found))
        app.input.edit_modified(False)
        app.convert_now()
        app.lift()

    def copy_for_office(self) -> None:
        found = self._selected()
        if found is None:
            return
        try:
            copy = office_copy(self._latex(found), "latex", self.app.mode_var.get(), self.app._macros())
            clipboard.copy_mathml_for_office(copy.mathml)
        except (ConversionError, PandocNotFound, clipboard.ClipboardError) as e:
            self.info.set("Could not copy: %s" % str(e).splitlines()[-1])
            return
        self.info.set("   ".join(["Copied: press %s+V in Word / PowerPoint" % KEY] + copy.notes))

    def copy_latex(self) -> None:
        found = self._selected()
        if found is None:
            return
        self.clipboard_clear()
        self.clipboard_append(self._latex(found))
        self.info.set("LaTeX copied")

    def export(self, path: Optional[str] = None) -> None:
        path = path or filedialog.asksaveasfilename(
            parent=self, title="Export the equations", defaultextension=".tex",
            initialfile=os.path.splitext(os.path.basename(self.doc.path))[0] + "-equations.tex",
            filetypes=[("LaTeX", "*.tex"), ("All files", "*.*")])
        if not path:
            return
        text = "".join("%% [%d] %s\n%s\n\n" % (i, found.where, self._latex(found))
                       for i, found in enumerate(self.doc.found, 1))
        try:
            with open(path, "w", encoding="utf-8", newline="\n") as f:
                f.write("%% equations of %s (MathShuttle)\n\n%s" % (os.path.basename(self.doc.path), text))
        except OSError as e:
            self.info.set("Could not write %s: %s" % (path, e.strerror or e))
            return
        self.info.set("%d equations written to %s" % (len(self.doc.found), path))


def main() -> int:
    _enable_dpi_awareness()
    app = App()
    app.mainloop()
    return 0


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
