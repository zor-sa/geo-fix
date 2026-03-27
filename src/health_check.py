"""Health check module for geo-fix.

VPN detection via local network adapter inspection (no external API calls).
Single-instance guard. Proxy status check.
"""

import logging
import os
import socket
import subprocess
import sys
from enum import Enum
from pathlib import Path
from typing import Optional

logger = logging.getLogger("geo-fix.health")

# PID file for single-instance guard (alongside executable)
PID_FILE = Path(os.path.dirname(os.path.abspath(sys.argv[0]))) / ".geo-fix.pid"


class VpnStatus(Enum):
    DETECTED = "vpn_detected"
    NOT_DETECTED = "no_vpn"
    UNKNOWN = "unknown"


def check_vpn_status() -> VpnStatus:
    """Detect VPN presence by inspecting local network adapters.

    Checks for common VPN adapter names via Windows WMI/netsh.
    No external API calls — all checks are local.
    """
    if sys.platform != "win32":
        # On non-Windows, check for tun/tap interfaces
        return _check_vpn_linux()

    return _check_vpn_windows()


def _check_vpn_windows() -> VpnStatus:
    """Check for VPN adapters on Windows via netsh."""
    vpn_keywords = [
        "tap", "tun", "wireguard", "wintun", "vpn", "nordlynx",
        "proton", "mullvad", "expressvpn", "surfshark", "cyberghost",
        "openvpn", "windscribe", "privatevpn"
    ]

    try:
        result = subprocess.run(
            ["netsh", "interface", "show", "interface"],
            capture_output=True, text=True, timeout=10
        )
        if result.returncode != 0:
            return VpnStatus.UNKNOWN

        output = result.stdout.lower()
        for keyword in vpn_keywords:
            if keyword in output:
                logger.info("VPN adapter detected (keyword: %s)", keyword)
                return VpnStatus.DETECTED

        # Also check via ipconfig for active adapters
        result2 = subprocess.run(
            ["ipconfig", "/all"],
            capture_output=True, text=True, timeout=10
        )
        if result2.returncode == 0:
            output2 = result2.stdout.lower()
            for keyword in vpn_keywords:
                if keyword in output2:
                    logger.info("VPN adapter detected via ipconfig (keyword: %s)", keyword)
                    return VpnStatus.DETECTED

        logger.warning("No VPN adapter detected")
        return VpnStatus.NOT_DETECTED

    except (subprocess.TimeoutExpired, FileNotFoundError) as e:
        logger.warning("VPN check failed: %s", e)
        return VpnStatus.UNKNOWN


def _check_vpn_linux() -> VpnStatus:
    """Check for VPN interfaces on Linux."""
    vpn_interfaces = ["tun", "tap", "wg", "nordlynx", "proton"]
    try:
        result = subprocess.run(
            ["ip", "link", "show"],
            capture_output=True, text=True, timeout=10
        )
        if result.returncode == 0:
            output = result.stdout.lower()
            for iface in vpn_interfaces:
                if iface in output:
                    return VpnStatus.DETECTED
        return VpnStatus.NOT_DETECTED
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return VpnStatus.UNKNOWN


def check_proxy_running(host: str = "127.0.0.1", port: int = 8080) -> bool:
    """Check if the proxy port is listening."""
    try:
        with socket.create_connection((host, port), timeout=2):
            return True
    except (ConnectionRefusedError, socket.timeout, OSError):
        return False


# === Single-Instance Guard ===

def _is_pid_running(pid: int) -> bool:
    """Check if a process with given PID is still running."""
    if sys.platform == "win32":
        try:
            import ctypes
            kernel32 = ctypes.windll.kernel32
            PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
            handle = kernel32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
            if handle:
                kernel32.CloseHandle(handle)
                return True
            return False
        except Exception:
            return False
    else:
        try:
            os.kill(pid, 0)
            return True
        except (ProcessLookupError, PermissionError):
            return False


def acquire_instance_lock() -> bool:
    """Acquire single-instance lock. Returns True if acquired, False if another instance running."""
    if PID_FILE.exists():
        try:
            existing_pid = int(PID_FILE.read_text().strip())
            if _is_pid_running(existing_pid):
                logger.error("Another instance is running (PID %d)", existing_pid)
                return False
            else:
                logger.info("Stale PID file found (PID %d not running), cleaning up", existing_pid)
                PID_FILE.unlink()
        except (ValueError, OSError):
            PID_FILE.unlink(missing_ok=True)

    PID_FILE.write_text(str(os.getpid()))
    logger.info("Instance lock acquired (PID %d)", os.getpid())
    return True


def release_instance_lock() -> None:
    """Release single-instance lock."""
    if PID_FILE.exists():
        try:
            stored_pid = int(PID_FILE.read_text().strip())
            if stored_pid == os.getpid():
                PID_FILE.unlink()
                logger.info("Instance lock released")
        except (ValueError, OSError):
            PID_FILE.unlink(missing_ok=True)
