"""Explicit startup preference; no registry write until manually enabled."""

import subprocess
import sys
from pathlib import Path

from . import APP_NAME

LEGACY_NAMES = ("AC Engineering Data Agent",)


def set_startup(enabled):
    if sys.platform != "win32":
        raise ValueError("Start with Windows is only available on Windows")
    import winreg

    with winreg.OpenKey(
        winreg.HKEY_CURRENT_USER,
        r"Software\Microsoft\Windows\CurrentVersion\Run",
        0,
        winreg.KEY_SET_VALUE,
    ) as key:
        name = APP_NAME
        for old in LEGACY_NAMES:  # entry of the version before the rename
            try:
                winreg.DeleteValue(key, old)
            except FileNotFoundError:
                pass
        if enabled:
            if not getattr(sys, "frozen", False):
                raise ValueError("Enable startup in the packaged Windows EXE")
            command = subprocess.list2cmdline(
                [str(Path(sys.executable).resolve()), "--no-browser"]
            )
            winreg.SetValueEx(key, name, 0, winreg.REG_SZ, command)
        else:
            try:
                winreg.DeleteValue(key, name)
            except FileNotFoundError:
                pass
