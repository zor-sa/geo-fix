"""Watchdog subprocess for geo-fix crash recovery.

Monitors the main process PID and performs cleanup if it dies unexpectedly.
Spawned by main.py before any system modifications.

Usage: python watchdog.py <main_pid> <state_file> <session_tmpdir> <session_id> <stop_token>
"""

import logging
import os
import shutil
import signal
import subprocess
import sys
import time
from pathlib import Path

# Add project root to path for imports
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src.system_config import cleanup, load_state

logger = logging.getLogger("geo-fix.watchdog")

POLL_INTERVAL = 2  # seconds
STOP_FLAG_NAME = ".geo-fix-watchdog-stop"


def _is_process_alive(pid: int) -> bool:
    """Check if a process is alive."""
    if sys.platform == "win32":
        try:
            import ctypes
            SYNCHRONIZE = 0x00100000
            handle = ctypes.windll.kernel32.OpenProcess(SYNCHRONIZE, False, pid)
            if handle:
                WAIT_OBJECT_0 = 0
                result = ctypes.windll.kernel32.WaitForSingleObject(handle, 0)
                ctypes.windll.kernel32.CloseHandle(handle)
                return result != WAIT_OBJECT_0
            return False
        except Exception:
            return False
    else:
        try:
            os.kill(pid, 0)
            return True
        except ProcessLookupError:
            return False
        except PermissionError:
            return True  # process exists but we lack permission


def _check_stop_flag(session_tmpdir: str, stop_token: str) -> bool:
    """Check if the clean shutdown stop flag exists with correct token."""
    flag_path = Path(session_tmpdir) / STOP_FLAG_NAME
    if flag_path.exists():
        try:
            content = flag_path.read_text(encoding="utf-8").strip()
            return content == stop_token
        except Exception:
            pass
    return False


def _remove_onlogon_task() -> None:
    """Remove the ONLOGON scheduled task."""
    try:
        subprocess.run(
            ["schtasks", "/delete", "/tn", "geo-fix-cleanup", "/f"],
            capture_output=True, timeout=10
        )
        logger.info("Removed ONLOGON scheduled task")
    except Exception as e:
        logger.warning("Could not remove ONLOGON task: %s", e)


def _register_onlogon_task(exe_path: str) -> None:
    """Register an ONLOGON scheduled task for boot-time cleanup."""
    if sys.platform != "win32":
        return
    try:
        subprocess.run(
            ["schtasks", "/create", "/sc", "ONLOGON",
             "/tn", "geo-fix-cleanup",
             "/tr", f'"{exe_path}" --cleanup',
             "/rl", "LIMITED", "/f"],
            capture_output=True, timeout=10
        )
        logger.info("Registered ONLOGON scheduled task")
    except Exception as e:
        logger.warning("Could not register ONLOGON task: %s", e)


def run_watchdog(main_pid: int, state_file: str, session_tmpdir: str,
                 session_id: str, stop_token: str) -> None:
    """Main watchdog loop."""
    logger.info("Watchdog started: monitoring PID %d, session %s", main_pid, session_id)

    while True:
        time.sleep(POLL_INTERVAL)

        # Check for clean shutdown signal
        if _check_stop_flag(session_tmpdir, stop_token):
            logger.info("Stop token received — clean shutdown, exiting watchdog")
            return

        # Check if main process is alive
        if _is_process_alive(main_pid):
            continue

        # Main process is dead — perform crash recovery
        logger.warning("Main process %d is dead — performing crash recovery", main_pid)

        try:
            state = load_state()
            if state is None:
                logger.info("No state file found — nothing to clean up")
                return

            # Verify session ID to avoid cleaning up a different session
            if state.session_id != session_id:
                logger.warning(
                    "Session ID mismatch: expected %s, got %s — skipping cleanup",
                    session_id, state.session_id
                )
                return

            cleanup(state)
            # Belt-and-suspenders: ensure tmpdir is deleted
            shutil.rmtree(session_tmpdir, ignore_errors=True)
            _remove_onlogon_task()
            logger.info("Crash recovery complete")
        except Exception as e:
            logger.error("Watchdog cleanup error: %s", e)

        return


def main():
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(name)s] %(levelname)s: %(message)s",
        datefmt="%H:%M:%S",
    )

    if len(sys.argv) != 6:
        print(f"Usage: {sys.argv[0]} <main_pid> <state_file> <session_tmpdir> <session_id> <stop_token>")
        sys.exit(1)

    main_pid = int(sys.argv[1])
    state_file = sys.argv[2]
    session_tmpdir = sys.argv[3]
    session_id = sys.argv[4]
    stop_token = sys.argv[5]

    # Override STATE_FILE path
    from src import system_config
    system_config.STATE_FILE = Path(state_file)

    # Register ONLOGON task
    _register_onlogon_task(sys.argv[0])

    try:
        run_watchdog(main_pid, state_file, session_tmpdir, session_id, stop_token)
    except KeyboardInterrupt:
        logger.info("Watchdog interrupted")
    except Exception as e:
        logger.error("Watchdog fatal error: %s", e)


if __name__ == "__main__":
    main()
