"""Build script for geo-fix.

Creates PyInstaller package and desktop shortcuts.
Run from the project root: python build/build.py
"""

import os
import subprocess
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).parent.parent
SRC_DIR = PROJECT_ROOT / "src"
DIST_DIR = PROJECT_ROOT / "dist" / "geo-fix"


def build_exe():
    """Build the exe using PyInstaller."""
    cmd = [
        sys.executable, "-m", "PyInstaller",
        "--onedir",
        "--name", "geo-fix",
        "--noconfirm",
        "--clean",
        # Add data files
        "--add-data", f"{SRC_DIR / 'inject.js'}{os.pathsep}src",
        "--add-data", f"{SRC_DIR / 'watchdog.py'}{os.pathsep}src",
        # Hidden imports for mitmproxy
        "--hidden-import", "mitmproxy.addons",
        "--hidden-import", "mitmproxy.tools.dump",
        # Entry point
        str(SRC_DIR / "main.py"),
    ]

    print("Building geo-fix.exe...")
    result = subprocess.run(cmd, cwd=str(PROJECT_ROOT))
    if result.returncode != 0:
        print("Build failed!")
        sys.exit(1)
    print(f"Build complete: {DIST_DIR}")


def create_shortcuts():
    """Create Windows .lnk shortcuts on the desktop."""
    if sys.platform != "win32":
        print("Shortcuts can only be created on Windows")
        _create_shortcut_scripts()
        return

    try:
        import win32com.client
    except ImportError:
        print("pywin32 not installed — creating .bat files instead")
        _create_bat_shortcuts()
        return

    desktop = Path(os.environ.get("USERPROFILE", "")) / "Desktop"
    exe_path = DIST_DIR / "geo-fix.exe"

    shortcuts = {
        "geo-fix Включить (US)": "US",
        "geo-fix Включить (DE)": "DE",
        "geo-fix Включить (NL)": "NL",
        "geo-fix Включить (GB)": "GB",
        "geo-fix Выключить": "--stop",
    }

    shell = win32com.client.Dispatch("WScript.Shell")
    for name, arg in shortcuts.items():
        lnk_path = str(desktop / f"{name}.lnk")
        shortcut = shell.CreateShortCut(lnk_path)
        shortcut.Targetpath = str(exe_path)
        shortcut.Arguments = arg
        shortcut.WorkingDirectory = str(DIST_DIR)
        shortcut.Description = f"geo-fix: {name}"
        shortcut.save()
        print(f"Created shortcut: {lnk_path}")


def _create_bat_shortcuts():
    """Create .bat shortcuts as fallback."""
    desktop = Path(os.environ.get("USERPROFILE", "")) / "Desktop"
    exe_path = DIST_DIR / "geo-fix.exe"

    shortcuts = {
        "geo-fix Включить (US).bat": "US",
        "geo-fix Включить (DE).bat": "DE",
        "geo-fix Включить (NL).bat": "NL",
        "geo-fix Включить (GB).bat": "GB",
        "geo-fix Выключить.bat": "--stop",
    }

    for name, arg in shortcuts.items():
        bat_path = desktop / name
        bat_path.write_text(f'@echo off\n"{exe_path}" {arg}\n', encoding="utf-8")
        print(f"Created: {bat_path}")


def _create_shortcut_scripts():
    """Create shell scripts for non-Windows (development only)."""
    scripts_dir = DIST_DIR / "shortcuts"
    scripts_dir.mkdir(parents=True, exist_ok=True)

    shortcuts = {"US": "US", "DE": "DE", "NL": "NL", "GB": "GB", "stop": "--stop"}
    for name, arg in shortcuts.items():
        script = scripts_dir / f"geo-fix-{name}.sh"
        script.write_text(f'#!/bin/bash\n./geo-fix {arg}\n')
        script.chmod(0o755)
        print(f"Created: {script}")


def create_dist_launchers():
    """Create .bat launchers inside the dist folder for portable use."""
    DIST_DIR.mkdir(parents=True, exist_ok=True)

    launchers = {
        "Включить (US).bat": "US",
        "Включить (DE).bat": "DE",
        "Включить (NL).bat": "NL",
        "Включить (GB).bat": "GB",
        "Выключить.bat": "--stop",
        "Починить интернет.bat": "--cleanup",
    }

    for name, arg in launchers.items():
        bat = DIST_DIR / name
        bat.write_text(f'@echo off\nstart "" "%~dp0geo-fix.exe" {arg}\n', encoding="utf-8")
        print(f"Created launcher: {bat}")


def main():
    build_exe()
    create_dist_launchers()
    create_shortcuts()
    print("\nDone! geo-fix is ready in dist/geo-fix/")


if __name__ == "__main__":
    main()
