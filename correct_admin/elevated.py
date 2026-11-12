"""Run a service_control command through UAC and collect its outcome (Windows).

The config UI of a "current user" install runs without admin rights. Registering or
removing the service needs admin once, so those commands are run as

    pythonw.exe -m correct_admin.service_control --result-file <tmp.json> <command...>

through ShellExecuteEx("runas"). An elevated child cannot share our pipes, so it
writes {"ok": bool, "message": str} to the result file. Passwords are never passed
on the command line: the elevated child shows its own prompt (ask_password_gui).
"""

import json
import os
import subprocess
import sys
import tempfile

ERROR_CANCELLED = 1223  # user clicked "No" on the UAC prompt


def gui_python():
    """pythonw.exe of the running interpreter (no console window), else python.exe."""
    candidate = os.path.join(sys.exec_prefix, "pythonw.exe")
    return candidate if os.path.exists(candidate) else sys.executable


def build_command(args, result_file):
    """(executable, parameters) for the elevated child. Pure function, unit-tested."""
    params = ["-m", "correct_admin.service_control", "--result-file", result_file, *args]
    return gui_python(), subprocess.list2cmdline(params)


def run_elevated(executable, parameters, cwd=None):
    """Start ``executable parameters`` elevated, wait, and return its exit code."""
    import win32con
    import win32event
    import win32process
    from win32com.shell import shell, shellcon

    try:
        info = shell.ShellExecuteEx(
            fMask=shellcon.SEE_MASK_NOCLOSEPROCESS | shellcon.SEE_MASK_NO_CONSOLE,
            lpVerb="runas",
            lpFile=executable,
            lpParameters=parameters,
            lpDirectory=cwd,
            nShow=win32con.SW_HIDE,
        )
    except Exception as e:
        if getattr(e, "winerror", None) == ERROR_CANCELLED or (e.args and e.args[0] == ERROR_CANCELLED):
            from .service_control import Cancelled
            raise Cancelled("Administrator approval was cancelled.")
        raise
    handle = info["hProcess"]
    try:
        win32event.WaitForSingleObject(handle, win32event.INFINITE)
        return win32process.GetExitCodeProcess(handle)
    finally:
        handle.Close()


def run_service_command(args):
    """Run ``service_control <args>`` elevated. Returns (ok, message)."""
    fd, result_file = tempfile.mkstemp(prefix="correct-", suffix=".json")
    os.close(fd)
    try:
        exe, params = build_command(args, result_file)
        src_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        code = run_elevated(exe, params, cwd=src_root)
        try:
            with open(result_file, "r", encoding="utf-8") as f:
                result = json.load(f)
            return bool(result.get("ok")), str(result.get("message", ""))
        except (OSError, ValueError):
            return code == 0, f"exit code {code}"
    finally:
        try:
            os.remove(result_file)
        except OSError:
            pass


def ask_password_gui(account):
    """Small modal password prompt. Returns the password, or None if cancelled."""
    import tkinter as tk
    from tkinter import ttk

    result = {"value": None}
    root = tk.Tk()
    root.title("CORRECT service account")
    root.resizable(False, False)
    root.attributes("-topmost", True)
    frame = ttk.Frame(root, padding=14)
    frame.grid()
    ttk.Label(frame, text=f"The CORRECT service will log on as:\n\n    {account}\n\n"
                          "Enter this account's Windows password.\n"
                          "It is stored by Windows (Service Control Manager), not by CORRECT.",
              justify="left").grid(row=0, column=0, columnspan=2, sticky="w")
    var = tk.StringVar()
    entry = ttk.Entry(frame, textvariable=var, show="•", width=36)
    entry.grid(row=1, column=0, columnspan=2, sticky="ew", pady=(10, 10))

    def ok(_event=None):
        result["value"] = var.get()
        root.destroy()

    def cancel(_event=None):
        root.destroy()

    ttk.Button(frame, text="Cancel", command=cancel).grid(row=2, column=0, sticky="e", padx=(0, 6))
    ttk.Button(frame, text="OK", command=ok).grid(row=2, column=1, sticky="e")
    root.bind("<Return>", ok)
    root.bind("<Escape>", cancel)
    root.protocol("WM_DELETE_WINDOW", cancel)
    entry.focus_force()
    root.mainloop()
    return result["value"]
