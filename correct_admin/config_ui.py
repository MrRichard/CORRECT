"""CORRECT configuration editor (Tkinter).

    correct-config [--config PATH]

Edits the same config.yaml the service reads; comments in the file are kept.
Needs Administrator (Windows) or root (Linux) to save and to control the service;
otherwise it opens read-only.
"""

import argparse
import os
import subprocess
import sys
import threading
import tkinter as tk
from tkinter import filedialog, messagebox, ttk

from correct_admin import service_control as sc
from correct_admin.config_model import SECTIONS, ConfigModel, resolve_config_path, validate_value

IS_WINDOWS = sys.platform == "win32"


# ── privileges ──────────────────────────────────────────────────────────────
def is_admin():
    if IS_WINDOWS:
        try:
            import ctypes
            return bool(ctypes.windll.shell32.IsUserAnAdmin())
        except Exception:
            return False
    return os.geteuid() == 0


def relaunch_elevated(argv):
    """Windows only: re-run this program through UAC. Returns True if a relaunch was started."""
    if not IS_WINDOWS:
        return False
    import ctypes
    params = subprocess.list2cmdline(["-m", "correct_admin.config_ui", *argv, "--no-elevate"])
    rc = ctypes.windll.shell32.ShellExecuteW(None, "runas", sys.executable, params, None, 1)
    return rc > 32


# ── C-ECHO test ─────────────────────────────────────────────────────────────
def c_echo(host, port, called_aet, calling_aet="CORRECT", timeout=10):
    from pynetdicom import AE
    from pynetdicom.sop_class import Verification

    ae = AE(ae_title=calling_aet or "CORRECT")
    ae.add_requested_context(Verification)
    ae.acse_timeout = ae.dimse_timeout = ae.network_timeout = timeout
    assoc = ae.associate(host, int(port), ae_title=called_aet)
    if not assoc.is_established:
        return False, "Association rejected, aborted or never connected"
    try:
        status = assoc.send_c_echo()
        if status and status.Status == 0x0000:
            return True, "C-ECHO succeeded"
        return False, f"C-ECHO returned status {getattr(status, 'Status', 'none')}"
    finally:
        assoc.release()


# ── UI ──────────────────────────────────────────────────────────────────────
class ConfigApp(ttk.Frame):
    def __init__(self, master, model, writable):
        super().__init__(master, padding=8)
        self.master = master
        self.model = model
        self.writable = writable
        self.controller = sc.get_controller()
        self.widgets = {}  # field.key -> (field, getter, setter)
        self.error_labels = {}
        self.status_var = tk.StringVar(value="")
        self.service_var = tk.StringVar(value="…")
        self.account_var = tk.StringVar(value="…")

        master.title(f"CORRECT Configuration — {model.path}")
        master.minsize(720, 540)
        self.grid(sticky="nsew")
        master.columnconfigure(0, weight=1)
        master.rowconfigure(0, weight=1)
        self.columnconfigure(0, weight=1)
        self.rowconfigure(1, weight=1)

        self._build_banner()
        self._build_notebook()
        self._build_buttons()
        self.load_values()
        self.refresh_service()

    # -- layout ----------------------------------------------------------------
    def _build_banner(self):
        text = f"Config file: {self.model.path}"
        if not self.model.exists:
            text += "   (does not exist yet — it will be created on save)"
        ttk.Label(self, text=text).grid(row=0, column=0, sticky="w")
        if not self.writable:
            hint = "Run as Administrator" if IS_WINDOWS else "Run with sudo"
            ttk.Label(self, text=f"Read-only: {hint} to save changes or control the service.",
                      foreground="#b00020").grid(row=0, column=0, sticky="e")

    def _build_notebook(self):
        nb = ttk.Notebook(self)
        nb.grid(row=1, column=0, sticky="nsew", pady=(6, 6))
        for tab_name, fields in SECTIONS.items():
            frame = ttk.Frame(nb, padding=10)
            frame.columnconfigure(1, weight=1)
            nb.add(frame, text=tab_name)
            for row, fld in enumerate(fields):
                self._build_field(frame, row, fld)
            if tab_name == "Destination":
                ttk.Button(frame, text="Test C-ECHO", command=self.test_echo).grid(
                    row=len(fields), column=1, sticky="w", pady=(10, 0))
        nb.add(self._build_service_tab(nb), text="Service")

    def _build_field(self, frame, row, fld):
        state = "normal" if self.writable else "disabled"
        if fld.kind == "bool":
            var = tk.BooleanVar()
            ttk.Checkbutton(frame, text=fld.label, variable=var, state=state).grid(
                row=row, column=0, columnspan=3, sticky="w", pady=2)
            self.widgets[fld.key] = (fld, var.get, var.set)
            return

        ttk.Label(frame, text=fld.label).grid(row=row, column=0, sticky="nw", padx=(0, 8), pady=2)
        if fld.kind == "list":
            text = tk.Text(frame, height=5, width=40, wrap="none")
            text.grid(row=row, column=1, sticky="ew", pady=2)
            if not self.writable:
                text.configure(state="disabled")

            def getter(t=text):
                return t.get("1.0", "end").strip()

            def setter(v, t=text):
                prev = t.cget("state")
                t.configure(state="normal")
                t.delete("1.0", "end")
                t.insert("1.0", "\n".join(v))
                t.configure(state=prev)
            self.widgets[fld.key] = (fld, getter, setter)
        elif fld.kind == "choice":
            var = tk.StringVar()
            ttk.Combobox(frame, textvariable=var, values=fld.choices,
                         state="readonly" if self.writable else "disabled", width=12).grid(
                row=row, column=1, sticky="w", pady=2)
            self.widgets[fld.key] = (fld, var.get, lambda v, var=var: var.set(str(v).upper()))
        else:
            var = tk.StringVar()
            ttk.Entry(frame, textvariable=var, state=state).grid(row=row, column=1, sticky="ew", pady=2)
            if fld.kind in ("dir", "file") and self.writable:
                ttk.Button(frame, text="Browse…",
                           command=lambda f=fld, v=var: self._browse(f, v)).grid(row=row, column=2, padx=(4, 0))
            self.widgets[fld.key] = (fld, var.get, lambda v, var=var: var.set("" if v is None else str(v)))

        err = ttk.Label(frame, text="", foreground="#b00020")
        err.grid(row=row, column=3, sticky="w", padx=(6, 0))
        self.error_labels[fld.key] = err
        if fld.help:
            ttk.Label(frame, text=fld.help, foreground="#666").grid(
                row=row, column=4, sticky="w", padx=(6, 0))

    def _browse(self, fld, var):
        initial = os.path.expanduser(var.get() or "")
        if fld.kind == "dir":
            path = filedialog.askdirectory(initialdir=initial if os.path.isdir(initial) else None)
        else:
            path = filedialog.asksaveasfilename(
                initialdir=os.path.dirname(initial) or None,
                initialfile=os.path.basename(initial) or None,
                confirmoverwrite=False)
        if path:
            var.set(os.path.normpath(path))

    def _build_service_tab(self, nb):
        frame = ttk.Frame(nb, padding=10)
        frame.columnconfigure(1, weight=1)
        ttk.Label(frame, text="Status:").grid(row=0, column=0, sticky="w")
        ttk.Label(frame, textvariable=self.service_var).grid(row=0, column=1, sticky="w")
        ttk.Label(frame, text="Runs as:").grid(row=1, column=0, sticky="w")
        ttk.Label(frame, textvariable=self.account_var).grid(row=1, column=1, sticky="w")

        btns = ttk.Frame(frame)
        btns.grid(row=2, column=0, columnspan=2, sticky="w", pady=(8, 12))
        state = "normal" if self.writable else "disabled"
        for i, (label, action) in enumerate((("Start", "start"), ("Stop", "stop"),
                                             ("Restart", "restart"), ("Refresh", None))):
            cmd = self.refresh_service if action is None else (lambda a=action: self.service_action(a))
            ttk.Button(btns, text=label, command=cmd,
                       state="normal" if action is None else state).grid(row=0, column=i, padx=(0, 6))

        if IS_WINDOWS:
            box = ttk.LabelFrame(frame, text="Service account", padding=8)
            box.grid(row=3, column=0, columnspan=2, sticky="ew")
            box.columnconfigure(1, weight=1)
            self.acct_kind = tk.StringVar(value=sc.ACCOUNT_LOCALSYSTEM)
            options = (
                (sc.ACCOUNT_LOCALSYSTEM, "Local System"),
                (sc.ACCOUNT_VIRTUAL, f"Virtual account ({sc.VIRTUAL_ACCOUNT_NAME}) — least privilege, local disks only"),
                (sc.ACCOUNT_USER, "Specific user / gMSA (needed for network shares)"),
            )
            for r, (val, text) in enumerate(options):
                ttk.Radiobutton(box, text=text, value=val, variable=self.acct_kind, state=state,
                                command=self._toggle_user_fields).grid(row=r, column=0, columnspan=2, sticky="w")
            self.acct_user = tk.StringVar()
            self.acct_pass = tk.StringVar()
            ttk.Label(box, text="User (DOMAIN\\name, .\\local, DOMAIN\\gmsa$)").grid(row=3, column=0, sticky="w", pady=(6, 0))
            self.user_entry = ttk.Entry(box, textvariable=self.acct_user)
            self.user_entry.grid(row=3, column=1, sticky="ew", pady=(6, 0))
            ttk.Label(box, text="Password (blank for gMSA)").grid(row=4, column=0, sticky="w")
            self.pass_entry = ttk.Entry(box, textvariable=self.acct_pass, show="•")
            self.pass_entry.grid(row=4, column=1, sticky="ew")
            ttk.Button(box, text="Apply account & restart", state=state,
                       command=self.apply_account).grid(row=5, column=1, sticky="e", pady=(8, 0))
            self._toggle_user_fields()
        else:
            ttk.Label(frame, foreground="#666",
                      text="The Linux service account is chosen at install time (install.sh --user).").grid(
                row=3, column=0, columnspan=2, sticky="w")
        return frame

    def _toggle_user_fields(self):
        on = self.writable and self.acct_kind.get() == sc.ACCOUNT_USER
        for w in (self.user_entry, self.pass_entry):
            w.configure(state="normal" if on else "disabled")

    def _build_buttons(self):
        bar = ttk.Frame(self)
        bar.grid(row=2, column=0, sticky="ew")
        bar.columnconfigure(0, weight=1)
        ttk.Label(bar, textvariable=self.status_var).grid(row=0, column=0, sticky="w")
        state = "normal" if self.writable else "disabled"
        ttk.Button(bar, text="Open raw YAML", command=self.open_raw).grid(row=0, column=1, padx=4)
        ttk.Button(bar, text="Reload", command=self.reload).grid(row=0, column=2, padx=4)
        ttk.Button(bar, text="Save", state=state, command=self.save).grid(row=0, column=3, padx=4)
        ttk.Button(bar, text="Save & Restart service", state=state,
                   command=lambda: self.save(restart=True)).grid(row=0, column=4, padx=4)

    # -- data ----------------------------------------------------------------
    def load_values(self):
        for fld, _getter, setter in self.widgets.values():
            setter(self.model.get(fld))
        for lbl in self.error_labels.values():
            lbl.configure(text="")

    def reload(self):
        self.model.load()
        self.load_values()
        self.status_var.set("Reloaded from disk.")

    def collect(self):
        """Push widget values into the model; return {key: error}."""
        errors = {}
        for key, (fld, getter, _setter) in self.widgets.items():
            raw = getter()
            msg = validate_value(fld, self.model._coerce(fld, raw))
            if key in self.error_labels:
                self.error_labels[key].configure(text=msg or "")
            if msg:
                errors[key] = msg
            else:
                self.model.set(fld, raw)
        return errors

    def save(self, restart=False):
        errors = self.collect()
        if errors:
            messagebox.showerror("Invalid settings",
                                 "Fix these before saving:\n\n" + "\n".join(f"• {k}: {v}" for k, v in errors.items()))
            return
        try:
            self.model.save()
        except Exception as e:
            messagebox.showerror("Save failed", str(e))
            return
        self.status_var.set(f"Saved {self.model.path} (previous version kept as .bak)")
        if restart:
            self.service_action("restart")

    def open_raw(self):
        path = self.model.path
        try:
            if IS_WINDOWS:
                subprocess.Popen(["notepad.exe", path])
            else:
                editor = os.environ.get("VISUAL") or os.environ.get("EDITOR")
                subprocess.Popen([editor, path] if editor else ["xdg-open", path])
        except Exception as e:
            messagebox.showerror("Open failed", str(e))

    # -- service ---------------------------------------------------------------
    def _in_thread(self, work, done):
        def runner():
            try:
                result = (True, work())
            except Exception as e:
                result = (False, e)
            self.master.after(0, lambda: done(*result))
        threading.Thread(target=runner, daemon=True).start()

    def refresh_service(self):
        def work():
            status = self.controller.status()
            account = self.controller.get_account() if status != "not installed" else "—"
            return status, account

        def done(ok, result):
            if not ok:
                self.service_var.set(f"unavailable ({result})")
                return
            status, account = result
            self.service_var.set(status)
            self.account_var.set(account)
            if IS_WINDOWS and status != "not installed":
                kind, user = sc.classify_account(account)
                self.acct_kind.set(kind)
                self.acct_user.set(user or "")
                self._toggle_user_fields()
        self._in_thread(work, done)

    def service_action(self, action):
        self.status_var.set(f"Service: {action}…")

        def done(ok, result):
            self.status_var.set(f"Service {action}: {'done' if ok else 'failed'}")
            if not ok:
                messagebox.showerror(f"Service {action} failed", str(result))
            self.refresh_service()
        self._in_thread(getattr(self.controller, action), done)

    def apply_account(self):
        kind = self.acct_kind.get()
        user = self.acct_user.get().strip() or None
        password = self.acct_pass.get() if kind == sc.ACCOUNT_USER else None
        if kind == sc.ACCOUNT_USER and not user:
            messagebox.showerror("Service account", "Enter a user name.")
            return
        # Folders the service writes to, from the settings currently in the form.
        self.collect()
        get = lambda *path: self.model.get(next(f for fs in SECTIONS.values() for f in fs if f.path == path))
        writable = [get("directories", "working"), get("directories", "logs"),
                    get("reporting", "output_directory"),
                    os.path.dirname(get("anonymization", "pid_mapping_file"))]

        def work():
            account = self.controller.set_account(kind, user, password)
            sc.apply_windows_acls(account, [os.path.expanduser(p) for p in writable],
                                  [os.path.dirname(os.path.abspath(self.model.path)), self.model.path])
            self.controller.restart()
            return account

        def done(ok, result):
            self.acct_pass.set("")
            if ok:
                self.status_var.set(f"Service now runs as {result}")
            else:
                messagebox.showerror("Service account", str(result))
            self.refresh_service()
        self.status_var.set("Changing service account…")
        self._in_thread(work, done)

    def test_echo(self):
        values = {k: g() for k, (_f, g, _s) in self.widgets.items()}
        host = values.get("dicom_destination.ip", "")
        port = values.get("dicom_destination.port", "")
        aet = values.get("dicom_destination.ae_title", "")
        calling = values.get("dicom_listener.ae_title", "CORRECT")
        self.status_var.set(f"C-ECHO {aet}@{host}:{port}…")

        def done(ok, result):
            if ok and result[0]:
                self.status_var.set(result[1])
                messagebox.showinfo("C-ECHO", f"{aet}@{host}:{port}\n\n{result[1]}")
            else:
                msg = result[1] if ok else str(result)
                self.status_var.set("C-ECHO failed")
                messagebox.showerror("C-ECHO", f"{aet}@{host}:{port}\n\n{msg}")
        self._in_thread(lambda: c_echo(host, port, aet, calling), done)


def main(argv=None):
    argv = list(sys.argv[1:] if argv is None else argv)
    parser = argparse.ArgumentParser(description="CORRECT configuration editor")
    parser.add_argument("--config", help="config.yaml to edit (default: installed location)")
    parser.add_argument("--no-elevate", action="store_true", help=argparse.SUPPRESS)
    parser.add_argument("--read-only", action="store_true", help="open without saving or service control")
    args = parser.parse_args(argv)

    admin = is_admin()
    if not admin and IS_WINDOWS and not args.no_elevate and not args.read_only:
        if relaunch_elevated([a for a in argv if a != "--no-elevate"]):
            return 0

    path = resolve_config_path(args.config)
    root = tk.Tk()
    try:
        model = ConfigModel(path)
    except Exception as e:
        messagebox.showerror("CORRECT", f"Could not read {path}:\n\n{e}")
        return 1
    ConfigApp(root, model, writable=admin and not args.read_only)
    root.mainloop()
    return 0


if __name__ == "__main__":
    sys.exit(main())
