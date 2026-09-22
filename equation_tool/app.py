"""tkinter desktop UI: a local stand-in for https://johnmacfarlane.net/texmath.html
with direct Word / PowerPoint clipboard exchange."""
from __future__ import annotations

import ctypes
import json
import os
import queue
import sys
import threading
import tkinter as tk
import traceback
from tkinter import font as tkfont
from tkinter import messagebox, ttk
from typing import Optional

from . import __version__, clipboard
from .convert import FORMAT_LABELS, MODES, Result, convert, mathml_for_office
from .latex_wrap import WRAPPERS
from .pandoc_engine import READ_FORMATS, WRITE_FORMATS, ConversionError, PandocNotFound, pandoc_version

SAMPLE = "\\begin{align}\n  \\nabla \\cdot \\mathbf{E} &= \\frac{\\rho}{\\varepsilon_0} \\\\\n  \\nabla \\times \\mathbf{B} - \\frac{1}{c^2}\\frac{\\partial \\mathbf{E}}{\\partial t} &= \\mu_0 \\mathbf{J}\n\\end{align}"

SETTINGS_PATH = os.path.join(os.environ.get("APPDATA", os.path.expanduser("~")), "EquationTool", "settings.json")
DEBOUNCE_MS = 450

HELP = """Equation Tool %s  -  texmath (via pandoc) running locally

INPUT (From)
  LaTeX   Any delimiters are recognised: bare, $...$, \\(...\\), $$...$$, \\[...\\],
          equation / align / gather / multline / eqnarray / alignat (+ starred),
          \\label, \\tag, \\nonumber, \\newcommand definitions, and several equations
          mixed with prose (each one is converted).  Bare input uses the Mode box.
  MathML  <math> element(s), with or without the xmlns / XML prolog.
  OMML    <m:oMath> or <m:oMathPara>, alone or inside any Word XML.

OUTPUT (To)
  LaTeX   The "LaTeX wrapper" box picks the delimiters / environment.  Auto keeps
          the input's environment and otherwise uses \\[ \\] (display) or $ $ (inline).
          align <-> aligned, gather <-> gathered are converted as needed.
  MathML / OMML / eqn / Typst   Mode = Auto follows the input; Display / Inline force it.

WORD / POWERPOINT
  Copy for Word/PowerPoint   converts the input to MathML and places it on the clipboard
          in the "MathML" clipboard format; Ctrl+V in Word, PowerPoint or OneNote then
          inserts a native, editable equation (Ctrl+Shift+C).
  Paste from Word/PowerPoint   reads the equation you copied in Word / PowerPoint
          (select the equation, Ctrl+C) and converts it to LaTeX (Ctrl+Shift+V).

Ctrl+Enter converts; Auto-convert re-runs the conversion as you type.
""" % __version__


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
        self.title("Equation Tool  -  LaTeX / MathML / OMML converter")
        self._seq = 0
        self._after_id: Optional[str] = None
        self._results: "queue.Queue[tuple]" = queue.Queue()
        self._polling = False
        self.settings = self._load_settings()

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
        self.mono = tkfont.Font(family="Consolas", size=11)
        ui = tkfont.nametofont("TkDefaultFont")
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
        self.auto_var = tk.BooleanVar(value=True)

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

        # --- status bar and button bar are packed before the panes so they always stay visible ---
        self.status_var = tk.StringVar(value="")
        status = ttk.Label(self, textvariable=self.status_var, anchor="w", relief="sunken", padding=(6, 2))
        status.pack(fill="x", side="bottom")

        bottom = ttk.Frame(self)
        bottom.pack(fill="x", side="bottom", padx=6, pady=(0, 4))
        ttk.Button(bottom, text="Convert", command=self.convert_now).pack(side="left", **pad)
        ttk.Checkbutton(bottom, text="Auto-convert", variable=self.auto_var, command=self._schedule).pack(side="left", **pad)
        ttk.Button(bottom, text="Clear", command=self.clear).pack(side="left", padx=(14, 4))
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

    # ---- settings -----------------------------------------------------------------------------
    def _load_settings(self) -> dict:
        try:
            with open(SETTINGS_PATH, encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            return {}

    def _apply_settings(self) -> None:
        s = self.settings
        for var, key, allowed in ((self.src_var, "src", READ_FORMATS), (self.dst_var, "dst", WRITE_FORMATS),
                                  (self.mode_var, "mode", [k for k, _ in MODES]),
                                  (self.wrapper_var, "wrapper", [k for k, _ in WRAPPERS])):
            if s.get(key) in allowed:
                var.set(s[key])
        self.auto_var.set(bool(s.get("auto_convert", True)))
        self.annotate_var.set(bool(s.get("annotate", False)))
        self.geometry(s.get("geometry", "1200x640"))
        self.minsize(960, 420)
        self.input.insert("1.0", s.get("input") or SAMPLE)
        self.input.edit_modified(False)
        self._update_option_states()

    def _save_settings(self) -> None:
        data = {"src": self.src_var.get(), "dst": self.dst_var.get(), "mode": self.mode_var.get(),
                "wrapper": self.wrapper_var.get(), "auto_convert": self.auto_var.get(),
                "annotate": self.annotate_var.get(), "geometry": self.geometry(),
                "input": self.input.get("1.0", "end-1c")[:20000]}
        try:
            os.makedirs(os.path.dirname(SETTINGS_PATH), exist_ok=True)
            with open(SETTINGS_PATH, "w", encoding="utf-8") as f:
                json.dump(data, f, indent=1)
        except Exception:
            pass

    def _on_close(self) -> None:
        self._save_settings()
        self.destroy()

    # ---- events ---------------------------------------------------------------------------------
    def _startup_check(self) -> None:
        try:
            self._set_status("Ready  -  engine: %s" % pandoc_version())
        except PandocNotFound as e:
            self._set_status("pandoc not found", error=True)
            messagebox.showerror("pandoc not found", str(e))
            return
        except Exception as e:  # pragma: no cover
            self._set_status("pandoc check failed: %s" % e, error=True)
            return
        self._schedule()

    def _update_option_states(self) -> None:
        self.wrapper_box.configure(state="readonly" if self.dst_var.get() == "latex" else "disabled")
        self.annotate_chk.configure(state="normal" if self.dst_var.get() == "mathml" else "disabled")

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
    def convert_now(self) -> None:
        self._after_id = None
        text = self.input.get("1.0", "end-1c")
        if not text.strip():
            self._set_output("")
            self._set_status("")
            return
        self._seq += 1
        seq = self._seq
        args = (text, self.src_var.get(), self.dst_var.get(), self.mode_var.get(),
                self.wrapper_var.get(), self.annotate_var.get())
        self._set_status("Converting...")
        threading.Thread(target=self._worker, args=(seq, args), daemon=True).start()
        if not self._polling:
            self._polling = True
            self.after(30, self._poll_results)

    def _worker(self, seq: int, args) -> None:
        """Runs in a background thread; only ever touches the queue."""
        try:
            result: object = convert(*args)
        except (ConversionError, PandocNotFound) as e:
            result = e
        except Exception:  # pragma: no cover
            result = ConversionError("Unexpected error:\n" + traceback.format_exc())
        self._results.put((seq, args, result))

    def _poll_results(self) -> None:
        done = False
        while True:
            try:
                seq, args, result = self._results.get_nowait()
            except queue.Empty:
                break
            if seq == self._seq:
                done = True
            self._finish(seq, args, result)
        if done:
            self._polling = False
        else:
            self.after(30, self._poll_results)

    def _finish(self, seq: int, args, result) -> None:
        if seq != self._seq:
            return  # a newer conversion has been started
        if isinstance(result, Exception):
            self._set_output(str(result), error=True)
            self._set_status("Conversion failed", error=True)
            return
        assert isinstance(result, Result)
        self._set_output(result.text)
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
            mathml, note = mathml_for_office(text, self.src_var.get(), self.mode_var.get())
            clipboard.copy_mathml_for_office(mathml)
        except (ConversionError, PandocNotFound, clipboard.ClipboardError) as e:
            self._set_output(str(e), error=True)
            self._set_status("Could not copy the equation", error=True)
            return
        msg = "Equation copied  -  press Ctrl+V in Word / PowerPoint / OneNote to insert it as a native equation"
        self._set_status(msg if not note else msg + "   " + note)

    def paste_from_office(self) -> None:
        try:
            mathml = clipboard.read_equation_from_office()
        except clipboard.ClipboardError as e:
            self._set_status(str(e), error=True)
            return
        if not mathml:
            self._set_status("No equation on the clipboard  -  select an equation in Word / PowerPoint and press Ctrl+C first",
                             error=True)
            return
        self.src_var.set("mathml")
        if self.dst_var.get() == "mathml":
            self.dst_var.set("latex")
        self.input.delete("1.0", "end")
        self.input.insert("1.0", mathml)
        self.input.edit_modified(False)
        self.convert_now()

    def swap(self) -> None:
        src, dst = self.src_var.get(), self.dst_var.get()
        if dst not in READ_FORMATS:
            self._set_status("Cannot swap: %s cannot be used as input" % FORMAT_LABELS[dst], error=True)
            return
        output = self.output.get("1.0", "end-1c")
        if self.output.tag_ranges("error"):
            output = ""
        self.src_var.set(dst)
        self.dst_var.set(src)
        if output.strip():
            self.input.delete("1.0", "end")
            self.input.insert("1.0", output)
            self.input.edit_modified(False)
        self.convert_now()

    def clear(self) -> None:
        self.input.delete("1.0", "end")
        self.input.edit_modified(False)
        self._set_output("")
        self._set_status("")
        self.input.focus_set()

    def show_help(self) -> None:
        win = tk.Toplevel(self)
        win.title("Equation Tool - help")
        txt = tk.Text(win, wrap="word", font=self.mono, padx=10, pady=10, width=92, height=32)
        txt.insert("1.0", HELP)
        txt.configure(state="disabled")
        txt.pack(fill="both", expand=True)
        ttk.Button(win, text="Close", command=win.destroy).pack(pady=6)
        win.transient(self)

    # ---- helpers --------------------------------------------------------------------------------
    def _set_output(self, text: str, error: bool = False) -> None:
        self.output.delete("1.0", "end")
        self.output.insert("1.0", text, ("error",) if error else ())

    def _set_status(self, text: str, error: bool = False) -> None:
        self.status_var.set(text)


def main() -> int:
    _enable_dpi_awareness()
    app = App()
    app.mainloop()
    return 0


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
