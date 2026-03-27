"""Windows system configuration for geo-fix.

Manages: WinINET proxy (registry), Firefox proxy (prefs.js), CA certificate,
optional firewall rules, and atomic state file for crash recovery.
"""

import json
import logging
import os
import subprocess
import sys
import tempfile
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Optional

logger = logging.getLogger("geo-fix.config")

# State file location: alongside the executable
STATE_FILE = Path(os.path.dirname(os.path.abspath(sys.argv[0]))) / ".geo-fix-state.json"

# Firewall rule naming prefix
FW_RULE_PREFIX = "geo-fix-webrtc"

# Proxy address (hardcoded, never stored in state file)
PROXY_HOST = "127.0.0.1"
PROXY_PORT = 8080
PROXY_ADDR = f"{PROXY_HOST}:{PROXY_PORT}"

# mitmproxy CA cert paths
MITMPROXY_CA_DIR = Path.home() / ".mitmproxy"
MITMPROXY_CA_CERT = MITMPROXY_CA_DIR / "mitmproxy-ca-cert.pem"

# STUN ports to block for WebRTC
STUN_PORTS = [3478, 5349, 19302, 19303, 19304, 19305]

# Browsers for firewall rules
BROWSER_EXES = ["chrome.exe", "msedge.exe", "firefox.exe"]


@dataclass
class ProxyState:
    """State saved for crash recovery."""
    pid: int
    preset_code: str
    timestamp: str
    original_proxy_enable: Optional[int] = None
    original_proxy_server: Optional[str] = None
    original_proxy_override: Optional[str] = None
    firewall_rules_created: bool = False
    firefox_prefs_modified: bool = False
    firefox_prefs_backup: Optional[str] = None

    def to_json(self) -> str:
        return json.dumps(asdict(self), indent=2)

    @classmethod
    def from_json(cls, data: str) -> "ProxyState":
        parsed = json.loads(data)
        # Strict schema validation: only known fields
        valid_fields = {f.name for f in cls.__dataclass_fields__.values()}
        unknown = set(parsed.keys()) - valid_fields
        if unknown:
            raise ValueError(f"Unknown fields in state file: {unknown}")
        return cls(**parsed)


def save_state(state: ProxyState) -> None:
    """Save state atomically (write temp + rename)."""
    temp_path = STATE_FILE.with_suffix(".tmp")
    temp_path.write_text(state.to_json(), encoding="utf-8")
    temp_path.replace(STATE_FILE)
    logger.info("State saved to %s", STATE_FILE)


def load_state() -> Optional[ProxyState]:
    """Load state from file. Returns None if not found or invalid."""
    if not STATE_FILE.exists():
        return None
    try:
        data = STATE_FILE.read_text(encoding="utf-8")
        return ProxyState.from_json(data)
    except (json.JSONDecodeError, ValueError, TypeError) as e:
        logger.warning("Invalid state file: %s", e)
        return None


def delete_state() -> None:
    """Remove state file."""
    if STATE_FILE.exists():
        STATE_FILE.unlink()
        logger.info("State file removed")


# === WinINET Proxy (Registry HKCU) ===

def _get_registry_proxy_settings() -> dict:
    """Read current proxy settings from registry. Returns dict with keys."""
    if sys.platform != "win32":
        return {"ProxyEnable": 0, "ProxyServer": "", "ProxyOverride": ""}

    import winreg
    key_path = r"Software\Microsoft\Windows\CurrentVersion\Internet Settings"
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, key_path) as key:
            enable = winreg.QueryValueEx(key, "ProxyEnable")[0]
            try:
                server = winreg.QueryValueEx(key, "ProxyServer")[0]
            except FileNotFoundError:
                server = ""
            try:
                override = winreg.QueryValueEx(key, "ProxyOverride")[0]
            except FileNotFoundError:
                override = ""
            return {"ProxyEnable": enable, "ProxyServer": server, "ProxyOverride": override}
    except FileNotFoundError:
        return {"ProxyEnable": 0, "ProxyServer": "", "ProxyOverride": ""}


def set_wininet_proxy() -> dict:
    """Set system proxy to our mitmproxy. Returns original settings."""
    original = _get_registry_proxy_settings()

    if sys.platform != "win32":
        logger.warning("Not on Windows — skipping WinINET proxy setup")
        return original

    import winreg
    key_path = r"Software\Microsoft\Windows\CurrentVersion\Internet Settings"
    with winreg.OpenKey(winreg.HKEY_CURRENT_USER, key_path, 0, winreg.KEY_SET_VALUE) as key:
        winreg.SetValueEx(key, "ProxyEnable", 0, winreg.REG_DWORD, 1)
        winreg.SetValueEx(key, "ProxyServer", 0, winreg.REG_SZ, PROXY_ADDR)
        winreg.SetValueEx(key, "ProxyOverride", 0, winreg.REG_SZ, "<local>")

    # Notify system of proxy change
    _notify_proxy_change()
    logger.info("WinINET proxy set to %s", PROXY_ADDR)
    return original


def unset_wininet_proxy(original: Optional[dict] = None) -> None:
    """Restore original proxy settings."""
    if sys.platform != "win32":
        return

    import winreg
    key_path = r"Software\Microsoft\Windows\CurrentVersion\Internet Settings"

    if original is None:
        original = {"ProxyEnable": 0, "ProxyServer": "", "ProxyOverride": ""}

    with winreg.OpenKey(winreg.HKEY_CURRENT_USER, key_path, 0, winreg.KEY_SET_VALUE) as key:
        winreg.SetValueEx(key, "ProxyEnable", 0, winreg.REG_DWORD, original.get("ProxyEnable", 0))
        winreg.SetValueEx(key, "ProxyServer", 0, winreg.REG_SZ, original.get("ProxyServer", ""))
        winreg.SetValueEx(key, "ProxyOverride", 0, winreg.REG_SZ, original.get("ProxyOverride", ""))

    _notify_proxy_change()
    logger.info("WinINET proxy restored to original settings")


def _notify_proxy_change() -> None:
    """Notify Windows that proxy settings changed."""
    if sys.platform != "win32":
        return
    try:
        import ctypes
        INTERNET_OPTION_SETTINGS_CHANGED = 39
        INTERNET_OPTION_REFRESH = 37
        internet_set_option = ctypes.windll.wininet.InternetSetOptionW
        internet_set_option(0, INTERNET_OPTION_SETTINGS_CHANGED, 0, 0)
        internet_set_option(0, INTERNET_OPTION_REFRESH, 0, 0)
    except Exception as e:
        logger.warning("Could not notify proxy change: %s", e)


# === Firefox Proxy Configuration ===

def _find_firefox_profile() -> Optional[Path]:
    """Find the default Firefox profile directory."""
    if sys.platform == "win32":
        profiles_dir = Path(os.environ.get("APPDATA", "")) / "Mozilla" / "Firefox" / "Profiles"
    else:
        profiles_dir = Path.home() / ".mozilla" / "firefox"

    if not profiles_dir.exists():
        return None

    # Look for *.default-release or *.default profile
    for profile in sorted(profiles_dir.iterdir()):
        if profile.is_dir() and ("default-release" in profile.name or "default" in profile.name):
            return profile
    return None


def set_firefox_proxy() -> Optional[str]:
    """Configure Firefox to use our proxy. Returns backup path if prefs were modified."""
    profile = _find_firefox_profile()
    if profile is None:
        logger.info("Firefox profile not found — skipping Firefox proxy setup")
        return None

    prefs_file = profile / "user.js"  # user.js overrides prefs.js

    # Backup existing user.js
    backup_path = None
    if prefs_file.exists():
        backup_path = str(prefs_file.with_suffix(".js.geo-fix-backup"))
        prefs_file.rename(backup_path)

    # Write proxy configuration
    proxy_prefs = f"""// geo-fix: proxy configuration (auto-generated, will be removed on stop)
user_pref("network.proxy.type", 1);
user_pref("network.proxy.http", "{PROXY_HOST}");
user_pref("network.proxy.http_port", {PROXY_PORT});
user_pref("network.proxy.ssl", "{PROXY_HOST}");
user_pref("network.proxy.ssl_port", {PROXY_PORT});
user_pref("network.proxy.no_proxies_on", "localhost, 127.0.0.1");
user_pref("security.enterprise_roots.enabled", true);
"""

    # If backup exists, prepend its content
    if backup_path and Path(backup_path).exists():
        original = Path(backup_path).read_text(encoding="utf-8")
        prefs_file.write_text(original + "\n" + proxy_prefs, encoding="utf-8")
    else:
        prefs_file.write_text(proxy_prefs, encoding="utf-8")

    logger.info("Firefox proxy configured in %s", prefs_file)
    return backup_path


def unset_firefox_proxy(backup_path: Optional[str] = None) -> None:
    """Restore Firefox proxy settings."""
    profile = _find_firefox_profile()
    if profile is None:
        return

    prefs_file = profile / "user.js"

    if backup_path and Path(backup_path).exists():
        # Restore backup
        Path(backup_path).rename(prefs_file)
        logger.info("Firefox user.js restored from backup")
    elif prefs_file.exists():
        # Remove our user.js if it contains our marker
        content = prefs_file.read_text(encoding="utf-8")
        if "geo-fix: proxy configuration" in content:
            prefs_file.unlink()
            logger.info("Firefox user.js removed (was created by geo-fix)")


# === CA Certificate ===

def install_ca_cert() -> bool:
    """Install mitmproxy CA cert to Windows CurrentUser store. No admin needed."""
    if not MITMPROXY_CA_CERT.exists():
        logger.error("CA cert not found at %s. Run mitmproxy once to generate it.", MITMPROXY_CA_CERT)
        return False

    if sys.platform != "win32":
        logger.warning("Not on Windows — skipping cert installation")
        return True

    try:
        result = subprocess.run(
            ["certutil", "-addstore", "-user", "Root", str(MITMPROXY_CA_CERT)],
            capture_output=True, text=True, timeout=30
        )
        if result.returncode == 0:
            logger.info("CA cert installed to CurrentUser store")
            _restrict_ca_key_permissions()
            return True
        else:
            logger.error("certutil failed: %s", result.stderr)
            return False
    except (subprocess.TimeoutExpired, FileNotFoundError) as e:
        logger.error("Failed to install CA cert: %s", e)
        return False


def uninstall_ca_cert() -> None:
    """Remove mitmproxy CA cert from Windows CurrentUser store."""
    if sys.platform != "win32":
        return

    try:
        subprocess.run(
            ["certutil", "-delstore", "-user", "Root", "mitmproxy"],
            capture_output=True, text=True, timeout=30
        )
        logger.info("CA cert removed from CurrentUser store")
    except Exception as e:
        logger.warning("Failed to remove CA cert: %s", e)


def _restrict_ca_key_permissions() -> None:
    """Restrict CA key file permissions to current user only."""
    key_file = MITMPROXY_CA_DIR / "mitmproxy-ca.pem"
    if not key_file.exists():
        return

    if sys.platform == "win32":
        try:
            username = os.environ.get("USERNAME", "")
            if username:
                subprocess.run(
                    ["icacls", str(key_file), "/inheritance:r",
                     "/grant:r", f"{username}:(R)"],
                    capture_output=True, timeout=10
                )
                logger.info("CA key permissions restricted to %s", username)
        except Exception as e:
            logger.warning("Could not restrict CA key permissions: %s", e)
    else:
        os.chmod(key_file, 0o600)


# === Firewall Rules (Optional, requires admin) ===

def create_firewall_rules() -> bool:
    """Create firewall rules to block STUN/TURN for WebRTC. Requires admin."""
    if sys.platform != "win32":
        logger.warning("Not on Windows — skipping firewall rules")
        return True

    success = True
    for browser in BROWSER_EXES:
        for port in STUN_PORTS:
            rule_name = f"{FW_RULE_PREFIX}-{browser.replace('.exe', '')}-udp-{port}"
            try:
                result = subprocess.run(
                    ["netsh", "advfirewall", "firewall", "add", "rule",
                     f"name={rule_name}",
                     "dir=out", "action=block", "protocol=UDP",
                     f"remoteport={port}",
                     f"program=%ProgramFiles%\\..\\..\\{browser}",
                     "enable=yes"],
                    capture_output=True, text=True, timeout=10
                )
                if result.returncode != 0:
                    logger.warning("Failed to create rule %s: %s", rule_name, result.stderr)
                    success = False
            except Exception as e:
                logger.warning("Failed to create firewall rule: %s", e)
                success = False

    if success:
        logger.info("Firewall rules created for STUN blocking")
    return success


def remove_firewall_rules() -> None:
    """Remove all geo-fix firewall rules."""
    if sys.platform != "win32":
        return

    for browser in BROWSER_EXES:
        for port in STUN_PORTS:
            rule_name = f"{FW_RULE_PREFIX}-{browser.replace('.exe', '')}-udp-{port}"
            try:
                subprocess.run(
                    ["netsh", "advfirewall", "firewall", "delete", "rule",
                     f"name={rule_name}"],
                    capture_output=True, text=True, timeout=10
                )
            except Exception:
                pass

    logger.info("Firewall rules removed")


# === Cleanup ===

def cleanup(state: Optional[ProxyState] = None) -> None:
    """Revert all system changes. Used on stop and crash recovery."""
    if state is None:
        state = load_state()

    if state is None:
        logger.info("No state to clean up")
        return

    logger.info("Running cleanup...")

    # Restore proxy
    original = {
        "ProxyEnable": state.original_proxy_enable or 0,
        "ProxyServer": state.original_proxy_server or "",
        "ProxyOverride": state.original_proxy_override or "",
    }
    unset_wininet_proxy(original)

    # Restore Firefox
    if state.firefox_prefs_modified:
        unset_firefox_proxy(state.firefox_prefs_backup)

    # Remove firewall rules
    if state.firewall_rules_created:
        remove_firewall_rules()

    # Delete state file
    delete_state()
    logger.info("Cleanup complete")
