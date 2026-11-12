"""CORRECT configuration and service manager (Tkinter).

    pythonw -m correct_admin.config_ui [--config PATH]      (Windows Start Menu shortcut)
    correct-config [--config PATH]                          (Linux)

Edits the config.yaml the service reads (comments are kept) and, on Windows,
installs and manages the service:

* "Current user" install — runs without admin. Installing/removing the service asks
  for UAC approval once; the service logs on as the current user.
* "All users" install — runs elevated (the config lives in ProgramData); the service
  runs as LocalSystem, the NT SERVICE\\CORRECT virtual account, or a named user.

On first launch (no config yet) it creates the data folders and a config from the
template, then walks the user through filling it in and installing the service.
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
from netrt_core.platform_paths import SCOPE_MACHINE, SCOPE_USER, install_scope

IS_WINDOWS = sys.platform == "win32"


# ── privileges ──────────────────────────────────────────────────────────────
def relaunch_elevated(argv):
    """Windows only: re-run this program through UAC. Returns True if a relaunch was started."""
    if not IS_WINDOWS:
        return False
    import ctypes
    from correct_admin.elevated import gui_python
    params = subprocess.list2cmdline(["-m", "correct_admin.config_ui", *argv, "--no-elevate"])
    src_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    rc = ctypes.windll.shell32.ShellExecuteW(None, "runas", gui_python(), params, src_root, 1)
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
    def __init__(self, master, model, writable, scope=None, elevated=False, first_run=False):
        super().__init__(master, padding=8)
        self.master = master
        self.model = model
        self.writable = writable
        self.scope = scope                    # "user", "machine" or None (dev / Linux)
        self.elevated = elevated
        self.first_run = first_run
        self.controller = sc.get_controller()
        self.widgets = {}                     # field.key -> (field, getter, setter)
        self.error_labels = {}
        self.service_buttons = {}
        self.status_var = tk.StringVar(value="")
        self.service_var = tk.StringVar(value="…")
        self.account_var = tk.StringVar(value="…")
        self.installed = False
        self.saved_port = None

        master.title(f"CORRECT Configuration — {model.path}")
        master.minsize(760, 560)
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

    # -- helpers ---------------------------------------------------------------
    @property
    def user_scope(self):
        return IS_WINDOWS and self.scope == SCOPE_USER

    def field(self, *path):
        return next(f for fs in SECTIONS.values() for f in fs if f.path == path)

    # -- layout ----------------------------------------------------------------
    def _build_banner(self):
        box = ttk.Frame(self)
        box.grid(row=0, column=0, sticky="ew")
        box.columnconfigure(0, weight=1)
        ttk.Label(box, text=f"Config file: {self.model.path}").grid(row=0, column=0, sticky="w")
        if not self.writable:
            hint = "Run as Administrator" if IS_WINDOWS else "Run with sudo"
            ttk.Label(box, text=f"Read-only: {hint} to save changes or control the service.",
                      foreground="#b00020").grid(row=0, column=1, sticky="e")
        if self.first_run:
            ttk.Label(
                box, foreground="#0b5394", justify="left",
                text=("Getting started:  ① Destination tab — set the PACS host, port and AE title; "
                      "Anonymization tab — set your site code.  ② Click Save.  "
                      "③ Service tab — click Install service."),
            ).grid(row=1, column=0, columnspan=2, sticky="w", pady=(4, 0))

    def _build_notebook(self):
        self.notebook = nb = ttk.Notebook(self)
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
        self.service_tab = self._build_service_tab(nb)
        nb.add(self.service_tab, text="Service")

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
        actions = [("Start", "start"), ("Stop", "stop"), ("Restart", "restart"), ("Refresh", "refresh")]
        if IS_WINDOWS:
            actions = [("Install service", "install"), ("Remove service", "remove")] + actions
        for i, (label, action) in enumerate(actions):
            cmd = self.refresh_service if action == "refresh" else (lambda a=action: self.service_action(a))
            b = ttk.Button(btns, text=label, command=cmd)
            b.grid(row=0, column=i, padx=(0, 6))
            self.service_buttons[action] = b

        if not IS_WINDOWS:
            ttk.Label(frame, foreground="#666",
                      text="The Linux service is created by install.sh, which also chooses its account (--user).").grid(
                row=3, column=0, columnspan=2, sticky="w")
            return frame

        self.firewall_var = tk.BooleanVar(value=True)
        ttk.Checkbutton(frame, variable=self.firewall_var,
                        text="Allow the DICOM listener port through Windows Firewall").grid(
            row=3, column=0, columnspan=2, sticky="w")

        box = ttk.LabelFrame(frame, text="Service account", padding=8)
        box.grid(row=4, column=0, columnspan=2, sticky="ew", pady=(10, 0))
        box.columnconfigure(1, weight=1)
        self.acct_kind = tk.StringVar(value=sc.ACCOUNT_LOCALSYSTEM)
        self.acct_user = tk.StringVar()
        self.acct_pass = tk.StringVar()
        self.user_entry = self.pass_entry = None

        if self.user_scope:
            me = self._current_user()
            self.acct_kind.set(sc.ACCOUNT_USER)
            self.acct_user.set(me)
            ttk.Label(box, justify="left", text=(
                f"This is a current-user install, so the service logs on as you ({me}).\n"
                "Installing it asks for Administrator approval once, then for your Windows password.\n"
                "Afterwards you can start and stop it here or in Services (services.msc) without admin rights.\n"
                "If your password changes, click Remove service and then Install service again.")).grid(
                row=0, column=0, columnspan=2, sticky="w")
            return frame

        options = (
            (sc.ACCOUNT_LOCALSYSTEM, "Local System (recommended)"),
            (sc.ACCOUNT_VIRTUAL, f"Advanced: virtual account {sc.VIRTUAL_ACCOUNT_NAME} — least privilege, local disks only"),
            (sc.ACCOUNT_USER, "Advanced: specific user or gMSA — needed if data is on a network share"),
        )
        for r, (val, text) in enumerate(options):
            ttk.Radiobutton(box, text=text, value=val, variable=self.acct_kind,
                            command=self._toggle_user_fields).grid(row=r, column=0, columnspan=2, sticky="w")
        ttk.Label(box, text="User (DOMAIN\\name, .\\local, DOMAIN\\gmsa$)").grid(row=3, column=0, sticky="w", pady=(6, 0))
        self.user_entry = ttk.Entry(box, textvariable=self.acct_user)
        self.user_entry.grid(row=3, column=1, sticky="ew", pady=(6, 0))
        ttk.Label(box, text="Password (blank for gMSA)").grid(row=4, column=0, sticky="w")
        self.pass_entry = ttk.Entry(box, textvariable=self.acct_pass, show="•")
        self.pass_entry.grid(row=4, column=1, sticky="ew")
        self.apply_button = ttk.Button(box, text="Apply account & restart", command=self.apply_account)
        self.apply_button.grid(row=5, column=1, sticky="e", pady=(8, 0))
        self._toggle_user_fields()
        return frame

    def _current_user(self):
        try:
            return sc.current_user()
        except Exception:
            return os.environ.get("USERDOMAIN", ".") + "\\" + os.environ.get("USERNAME", "")

    def _toggle_user_fields(self):
        if self.user_entry is None:
            return
        on = self.writable and self.acct_kind.get() == sc.ACCOUNT_USER
        for w in (self.user_entry, self.pass_entry):
            w.configure(state="normal" if on else "disabled")

    def _update_service_buttons(self, status):
        installed = status not in ("not installed",) and not status.startswith("unavailable")
        self.installed = installed
        can_admin = self.writable and (self.elevated or self.user_scope or not IS_WINDOWS)
        enable = {
            "install": can_admin and not installed,
            "remove": can_admin and installed,
            "start": installed and status != "running" and (self.writable or self.user_scope),
            "stop": installed and status == "running" and (self.writable or self.user_scope),
            "restart": installed and (self.writable or self.user_scope),
            "refresh": True,
        }
        for action, button in self.service_buttons.items():
            button.configure(state="normal" if enable.get(action, True) else "disabled")
        if IS_WINDOWS and not self.user_scope and hasattr(self, "apply_button"):
            self.apply_button.configure(state="normal" if (installed and self.writable) else "disabled")

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
        self.saved_port = self.model.get(self.field("dicom_listener", "port"))

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
            return False
        try:
            self.model.save()
        except Exception as e:
            messagebox.showerror("Save failed", str(e))
            return False
        self.status_var.set(f"Saved {self.model.path} (previous version kept as .bak)")
        new_port = self.model.get(self.field("dicom_listener", "port"))
        port_changed = new_port != self.saved_port
        self.saved_port = new_port
        if IS_WINDOWS and port_changed and self.installed and getattr(self, "firewall_var", None) \
                and self.firewall_var.get():
            self._run_privileged(["firewall", "--port", str(new_port)], "Updating firewall rule")
        if restart and self.installed:
            self.service_action("restart")
        return True

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

    # -- background work -------------------------------------------------------
    def _in_thread(self, work, done):
        def runner():
            try:
                result = (True, work())
            except Exception as e:
                result = (False, e)
            self.master.after(0, lambda: done(*result))
        threading.Thread(target=runner, daemon=True).start()

    def _run_privileged(self, args, label, on_done=None):
        """Run a service_control command: in-process when elevated, else through UAC."""
        self.status_var.set(f"{label}…")

        def work():
            if self.elevated or not IS_WINDOWS:
                args_ns = sc.build_parser().parse_args(args)
                return True, sc._execute(args_ns)
            from correct_admin.elevated import run_service_command
            return run_service_command(args)

        def done(ok, result):
            if ok:
                ok, message = result
            else:
                message = str(getattr(result, "strerror", None) or result)
            self.status_var.set(f"{label}: {'done' if ok else 'failed'}")
            if not ok and message != "cancelled":
                messagebox.showerror(label, message)
            elif ok and message:
                self.status_var.set(f"{label}: {message}")
            self.refresh_service()
            if on_done:
                on_done(ok)
        self._in_thread(work, done)

    # -- service ---------------------------------------------------------------
    def refresh_service(self):
        def work():
            status = self.controller.status()
            account = self.controller.get_account() if status != "not installed" else "—"
            return status, account

        def done(ok, result):
            if not ok:
                self.service_var.set(f"unavailable ({result})")
                self._update_service_buttons("unavailable")
                return
            status, account = result
            self.service_var.set(status)
            self.account_var.set(account)
            self._update_service_buttons(status)
            if IS_WINDOWS and not self.user_scope and status != "not installed":
                kind, user = sc.classify_account(account)
                self.acct_kind.set(kind)
                self.acct_user.set(user or "")
                self._toggle_user_fields()
        self._in_thread(work, done)

    def service_action(self, action):
        if action == "install":
            return self.install_service()
        if action == "remove":
            if messagebox.askyesno("Remove service",
                                   "Stop and remove the CORRECT service?\n\n"
                                   "Your configuration and data are kept."):
                self._run_privileged(["remove-service"], "Removing service")
            return

        self.status_var.set(f"Service: {action}…")

        def done(ok, result):
            if not ok and IS_WINDOWS and not self.elevated and _access_denied(result):
                # e.g. an older install without per-user rights: fall back to UAC.
                return self._run_privileged([action], f"Service {action}")
            self.status_var.set(f"Service {action}: {'done' if ok else 'failed'}")
            if not ok:
                messagebox.showerror(f"Service {action} failed", str(getattr(result, "strerror", None) or result))
            self.refresh_service()
        self._in_thread(getattr(self.controller, action), done)

    def install_service(self):
        # The service must not start on placeholder settings.
        if not self.save():
            return
        port_ok = self.firewall_var.get()
        args = ["install-service", "--config", self.model.path]
        if not port_ok:
            args.append("--no-firewall")
        if self.user_scope:
            me = self.acct_user.get()
            args += ["--scope", SCOPE_USER, "--account", sc.ACCOUNT_USER, "--user", me, "--prompt-password"]
            self._run_privileged(args, "Installing service")
            return

        kind = self.acct_kind.get()
        user = self.acct_user.get().strip() or None
        if kind == sc.ACCOUNT_USER and not user:
            messagebox.showerror("Service account", "Enter a user name, or choose Local System.")
            return
        args += ["--scope", SCOPE_MACHINE, "--account", kind]
        if user and kind == sc.ACCOUNT_USER:
            args += ["--user", user]
        password = self.acct_pass.get() if kind == sc.ACCOUNT_USER else None
        self._with_password(password, lambda: self._run_privileged(args, "Installing service",
                                                                   on_done=lambda ok: self.acct_pass.set("")))

    def apply_account(self):
        kind = self.acct_kind.get()
        user = self.acct_user.get().strip() or None
        if kind == sc.ACCOUNT_USER and not user:
            messagebox.showerror("Service account", "Enter a user name.")
            return
        args = ["set-account", "--config", self.model.path, "--account", kind]
        if user and kind == sc.ACCOUNT_USER:
            args += ["--user", user]
        password = self.acct_pass.get() if kind == sc.ACCOUNT_USER else None
        self._with_password(password, lambda: self._run_privileged(args, "Changing service account",
                                                                   on_done=lambda ok: self.acct_pass.set("")))

    def _with_password(self, password, fn):
        """Machine scope runs in-process (already elevated): hand the password over via env."""
        if password is None:
            os.environ.pop(sc.PASSWORD_ENV, None)
            return fn()
        os.environ[sc.PASSWORD_ENV] = password
        try:
            fn()
        finally:
            # The worker thread reads it immediately; clear it shortly after.
            self.master.after(5000, lambda: os.environ.pop(sc.PASSWORD_ENV, None))

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


def _access_denied(exc):
    code = getattr(exc, "winerror", None) or (exc.args[0] if getattr(exc, "args", None) else None)
    return code == 5


def main(argv=None):
    argv = list(sys.argv[1:] if argv is None else argv)
    parser = argparse.ArgumentParser(description="CORRECT configuration editor")
    parser.add_argument("--config", help="config.yaml to edit (default: installed location)")
    parser.add_argument("--no-elevate", action="store_true", help=argparse.SUPPRESS)
    parser.add_argument("--read-only", action="store_true", help="open without saving or service control")
    args = parser.parse_args(argv)

    scope = install_scope() if IS_WINDOWS else None
    admin = sc.is_admin()

    # "All users" (and development checkouts on Windows): the config lives in
    # ProgramData, which only Administrators may change — so run elevated.
    if IS_WINDOWS and scope != SCOPE_USER and not admin and not args.no_elevate and not args.read_only:
        if relaunch_elevated([a for a in argv if a != "--no-elevate"]):
            return 0

    path = resolve_config_path(args.config)
    first_run = False
    if IS_WINDOWS and scope and not os.path.exists(path) and (admin or scope == SCOPE_USER):
        from correct_admin.setup_tasks import init_config
        path, first_run = init_config(os.path.dirname(os.path.abspath(path)), scope)

    writable = (not args.read_only) and (admin or scope == SCOPE_USER)
    root = tk.Tk()
    try:
        model = ConfigModel(path)
    except Exception as e:
        messagebox.showerror("CORRECT", f"Could not read {path}:\n\n{e}")
        return 1
    ConfigApp(root, model, writable=writable, scope=scope, elevated=admin, first_run=first_run)
    root.mainloop()
    return 0


if __name__ == "__main__":
    sys.exit(main())
