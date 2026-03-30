"""Windows system configuration for geo-fix.

Manages: WinINET proxy (registry), Firefox proxy (prefs.js), CA certificate,
optional firewall rules, and atomic state file for crash recovery.
"""

import json
import logging
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Optional

logger = logging.getLogger("geo-fix.config")

# State file location: alongside the executable (binary encrypted blob)
STATE_FILE = Path(os.path.dirname(os.path.abspath(sys.argv[0]))) / ".geo-fix-state.bin"

# DPAPI entropy for state encryption
_DPAPI_ENTROPY = b"geo-fix-state-v1"

# Firewall rule naming prefix
FW_RULE_PREFIX = "geo-fix-webrtc"

# Proxy address (hardcoded, never stored in state file)
PROXY_HOST = "127.0.0.1"
PROXY_PORT = 8080
PROXY_ADDR = f"{PROXY_HOST}:{PROXY_PORT}"

# Cleanup pending file (app data dir)
CLEANUP_PENDING_FILE = Path(os.environ.get("APPDATA", str(Path.home()))) / "geo-fix" / "cleanup_pending.json"

# Cleanup step labels
CLEANUP_LABEL_CA_CERT = "CA cert removal"
CLEANUP_LABEL_SESSION_TMPDIR = "Session tmpdir deletion"
CLEANUP_LABEL_PROXY = "Proxy restore"
CLEANUP_LABEL_FIREFOX = "Firefox restore"
CLEANUP_LABEL_FIREWALL = "Firewall removal"
CLEANUP_LABEL_LOCATION_SERVICES = "Location Services restore"

# Retry delay for cleanup steps (seconds)
_CLEANUP_RETRY_DELAY = 3

# mitmproxy CA cert paths
MITMPROXY_CA_DIR = Path.home() / ".mitmproxy"
MITMPROXY_CA_CERT = MITMPROXY_CA_DIR / "mitmproxy-ca-cert.pem"

# STUN ports to block for WebRTC
STUN_PORTS = [3478, 5349, 19302, 19303, 19304, 19305]

# Browsers for firewall rules
BROWSER_EXES = ["chrome.exe", "msedge.exe", "firefox.exe"]

# Standard browser installation paths (filesystem fallback for _find_browser_path)
_STANDARD_BROWSER_PATHS = {
    "chrome.exe": [
        r"C:\Program Files\Google\Chrome\Application\chrome.exe",
        r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe",
    ],
    "msedge.exe": [
        r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe",
        r"C:\Program Files\Microsoft\Edge\Application\msedge.exe",
    ],
    "firefox.exe": [
        r"C:\Program Files\Mozilla Firefox\firefox.exe",
        r"C:\Program Files (x86)\Mozilla Firefox\firefox.exe",
    ],
}


@dataclass
class ProxyState:
    """State saved for crash recovery."""
    pid: int
    preset_code: str
    timestamp: str
    original_proxy_enable: Optional[int] = None
    original_proxy_server: Optional[str] = None
    original_proxy_override: Optional[str] = None
    firefox_prefs_modified: bool = False
    firefox_prefs_backup: Optional[str] = None
    session_id: Optional[str] = None
    session_tmpdir: Optional[str] = None
    ca_thumbprint: Optional[str] = None
    proxy_port: Optional[int] = None
    original_location_services: Optional[str] = None

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


def _dpapi_encrypt(plaintext: bytes) -> bytes:
    """Encrypt data using DPAPI (user-scope). Passthrough on non-Windows."""
    if sys.platform != "win32":
        logger.warning("DPAPI not available on this platform — state file is NOT encrypted")
        return plaintext

    import ctypes
    import ctypes.wintypes

    class DATA_BLOB(ctypes.Structure):
        _fields_ = [("cbData", ctypes.wintypes.DWORD),
                     ("pbData", ctypes.POINTER(ctypes.c_char))]

    crypt32 = ctypes.windll.crypt32
    kernel32 = ctypes.windll.kernel32

    input_blob = DATA_BLOB(len(plaintext), ctypes.create_string_buffer(plaintext, len(plaintext)))
    entropy_blob = DATA_BLOB(len(_DPAPI_ENTROPY), ctypes.create_string_buffer(_DPAPI_ENTROPY, len(_DPAPI_ENTROPY)))
    output_blob = DATA_BLOB()

    if not crypt32.CryptProtectData(
        ctypes.byref(input_blob), None, ctypes.byref(entropy_blob),
        None, None, 0, ctypes.byref(output_blob)
    ):
        raise OSError("CryptProtectData failed")

    encrypted = ctypes.string_at(output_blob.pbData, output_blob.cbData)
    kernel32.LocalFree(output_blob.pbData)
    return encrypted


def _dpapi_decrypt(ciphertext: bytes) -> bytes:
    """Decrypt DPAPI-encrypted data (user-scope). Passthrough on non-Windows."""
    if sys.platform != "win32":
        return ciphertext

    import ctypes
    import ctypes.wintypes

    class DATA_BLOB(ctypes.Structure):
        _fields_ = [("cbData", ctypes.wintypes.DWORD),
                     ("pbData", ctypes.POINTER(ctypes.c_char))]

    crypt32 = ctypes.windll.crypt32
    kernel32 = ctypes.windll.kernel32

    input_blob = DATA_BLOB(len(ciphertext), ctypes.create_string_buffer(ciphertext, len(ciphertext)))
    entropy_blob = DATA_BLOB(len(_DPAPI_ENTROPY), ctypes.create_string_buffer(_DPAPI_ENTROPY, len(_DPAPI_ENTROPY)))
    output_blob = DATA_BLOB()

    if not crypt32.CryptUnprotectData(
        ctypes.byref(input_blob), None, ctypes.byref(entropy_blob),
        None, None, 0, ctypes.byref(output_blob)
    ):
        raise OSError("CryptUnprotectData failed — data may be tampered or from another user")

    decrypted = ctypes.string_at(output_blob.pbData, output_blob.cbData)
    kernel32.LocalFree(output_blob.pbData)
    return decrypted


def save_state(state: ProxyState) -> None:
    """Save state atomically as DPAPI-encrypted binary blob."""
    plaintext = state.to_json().encode("utf-8")
    encrypted = _dpapi_encrypt(plaintext)
    temp_path = STATE_FILE.with_suffix(".tmp")
    temp_path.write_bytes(encrypted)
    temp_path.replace(STATE_FILE)
    logger.info("State saved to %s", STATE_FILE)


def load_state() -> Optional[ProxyState]:
    """Load and decrypt state from file. Returns None if not found, tampered, or invalid."""
    if not STATE_FILE.exists():
        return None
    try:
        encrypted = STATE_FILE.read_bytes()
        plaintext = _dpapi_decrypt(encrypted)
        return ProxyState.from_json(plaintext.decode("utf-8"))
    except Exception as e:
        logger.warning("State file rejected: %s", e)
        delete_state()
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


def set_wininet_proxy(port: int = PROXY_PORT) -> dict:
    """Set system proxy to our mitmproxy. Returns original settings."""
    original = _get_registry_proxy_settings()

    if sys.platform != "win32":
        logger.warning("Not on Windows — skipping WinINET proxy setup")
        return original

    proxy_addr = f"{PROXY_HOST}:{port}"
    import winreg
    key_path = r"Software\Microsoft\Windows\CurrentVersion\Internet Settings"
    with winreg.OpenKey(winreg.HKEY_CURRENT_USER, key_path, 0, winreg.KEY_SET_VALUE) as key:
        winreg.SetValueEx(key, "ProxyEnable", 0, winreg.REG_DWORD, 1)
        winreg.SetValueEx(key, "ProxyServer", 0, winreg.REG_SZ, proxy_addr)
        winreg.SetValueEx(key, "ProxyOverride", 0, winreg.REG_SZ, "<local>")

    # Notify system of proxy change
    _notify_proxy_change()
    logger.info("WinINET proxy set to %s", proxy_addr)
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


def set_firefox_proxy(port: int = PROXY_PORT) -> Optional[str]:
    """Configure Firefox to use our proxy. Returns backup path if prefs were modified."""
    profile = _find_firefox_profile()
    if profile is None:
        logger.info("Firefox profile not found — skipping Firefox proxy setup")
        return None

    prefs_file = profile / "user.js"  # user.js overrides prefs.js

    # Backup existing user.js (copy, not rename — original stays in place until overwritten)
    backup_path = None
    if prefs_file.exists():
        backup_path = str(prefs_file.with_suffix(".js.geo-fix-backup"))
        shutil.copy2(str(prefs_file), backup_path)

    # Write proxy configuration
    proxy_prefs = f"""// geo-fix: proxy configuration (auto-generated, will be removed on stop)
user_pref("network.proxy.type", 1);
user_pref("network.proxy.http", "{PROXY_HOST}");
user_pref("network.proxy.http_port", {port});
user_pref("network.proxy.ssl", "{PROXY_HOST}");
user_pref("network.proxy.ssl_port", {port});
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
        # Restore backup (copy + unlink: crash-safe — backup survives partial restore)
        shutil.copy2(backup_path, str(prefs_file))
        Path(backup_path).unlink()
        logger.info("Firefox user.js restored from backup")
    elif prefs_file.exists():
        # Remove our user.js if it contains our marker
        content = prefs_file.read_text(encoding="utf-8")
        if "geo-fix: proxy configuration" in content:
            prefs_file.unlink()
            logger.info("Firefox user.js removed (was created by geo-fix)")


# === Session Tmpdir ===

def create_session_tmpdir() -> str:
    """Create a per-session temp directory for mitmproxy CA with restricted ACL."""
    tmpdir = tempfile.mkdtemp(prefix="geo-fix-")
    if sys.platform == "win32":
        try:
            username = os.environ.get("USERNAME", "")
            if username:
                subprocess.run(
                    ["icacls", tmpdir, "/inheritance:r",
                     "/grant:r", f"{username}:(OI)(CI)F"],
                    capture_output=True, timeout=10
                )
                logger.info("Session tmpdir ACL restricted to %s", username)
        except Exception as e:
            logger.warning("Could not restrict session tmpdir ACL: %s", e)
    else:
        os.chmod(tmpdir, 0o700)
    logger.info("Created session tmpdir: %s", tmpdir)
    return tmpdir


def delete_ca_key_files(confdir: str) -> None:
    """Delete CA private key and related sensitive files from confdir.

    Called after mitmproxy loads the key into memory. mitmproxy caches
    the key in CertStore.default_privatekey and never re-reads from disk.
    Keeps mitmproxy-ca-cert.pem (public cert) for install_ca_cert().
    """
    sensitive_files = [
        "mitmproxy-ca.pem",       # Private key (PEM)
        "mitmproxy-ca-cert.cer",  # Public cert (DER copy)
        "mitmproxy-ca.p12",       # PKCS12 bundle (contains private key)
    ]
    for name in sensitive_files:
        f = Path(confdir) / name
        if f.exists():
            f.unlink()
            logger.info("Deleted sensitive file: %s", f.name)


def delete_ca_public_cert(confdir: str) -> None:
    """Delete the public CA cert after it has been installed to the store."""
    cert = Path(confdir) / "mitmproxy-ca-cert.pem"
    if cert.exists():
        cert.unlink()
        logger.info("Deleted public cert: %s", cert.name)


def delete_session_tmpdir(session_tmpdir: Optional[str]) -> None:
    """Delete the per-session temp directory containing CA private key."""
    if session_tmpdir is None:
        return
    if Path(session_tmpdir).exists():
        def _on_error(func, path, exc_info):
            logger.warning("Failed to delete %s: %s", path, exc_info[1])
        shutil.rmtree(session_tmpdir, onerror=_on_error)
        if Path(session_tmpdir).exists():
            logger.warning("Session tmpdir not fully deleted: %s — CA key may remain on disk", session_tmpdir)
        else:
            logger.info("Deleted session tmpdir: %s", session_tmpdir)


# === CA Certificate ===

def _parse_thumbprint(certutil_output: str) -> Optional[str]:
    """Extract SHA-1 thumbprint from certutil -dump output."""
    match = re.search(r"Cert Hash\(sha1\):\s*([0-9a-fA-F ]+)", certutil_output)
    if match:
        return match.group(1).replace(" ", "").lower()
    return None


def install_ca_cert(confdir: str) -> Optional[str]:
    """Install mitmproxy CA cert to Windows CurrentUser store. Returns thumbprint or None."""
    cert_path = Path(confdir) / "mitmproxy-ca-cert.pem"
    if not cert_path.exists():
        logger.error("CA cert not found at %s. Start mitmproxy first to generate it.", cert_path)
        return None

    try:
        result = subprocess.run(
            ["certutil", "-f", "-addstore", "-user", "Root", str(cert_path)],
            capture_output=True, text=True, timeout=30
        )
        if result.returncode != 0:
            logger.error("certutil -addstore failed: %s", result.stderr)
            return None

        logger.info("CA cert installed to CurrentUser store")

        # Extract thumbprint via certutil -dump
        dump_result = subprocess.run(
            ["certutil", "-dump", str(cert_path)],
            capture_output=True, text=True, timeout=30
        )
        thumbprint = _parse_thumbprint(dump_result.stdout)
        if thumbprint:
            logger.info("CA thumbprint: %s", thumbprint)
        else:
            logger.warning("Could not extract CA thumbprint from certutil output")
        return thumbprint
    except (subprocess.TimeoutExpired, FileNotFoundError) as e:
        logger.error("Failed to install CA cert: %s", e)
        return None


def uninstall_ca_cert(thumbprint: Optional[str] = None) -> None:
    """Remove mitmproxy CA cert from Windows CurrentUser store."""
    identifier = thumbprint if thumbprint else "mitmproxy"
    try:
        subprocess.run(
            ["certutil", "-delstore", "-user", "Root", identifier],
            capture_output=True, text=True, timeout=30
        )
        logger.info("CA cert removed from CurrentUser store (id=%s)", identifier)
    except Exception as e:
        logger.warning("Failed to remove CA cert: %s", e)


# === Firewall Rules (Optional, requires admin) ===

def _find_browser_path(exe_name: str) -> Optional[Path]:
    """Auto-detect browser executable path from registry or standard locations."""
    if sys.platform != "win32":
        return None

    try:
        import winreg
        # Try App Paths registry (both native and WOW6432Node)
        for root_key in [
            r"SOFTWARE\Microsoft\Windows\CurrentVersion\App Paths",
            r"SOFTWARE\WOW6432Node\Microsoft\Windows\CurrentVersion\App Paths",
        ]:
            try:
                with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, f"{root_key}\\{exe_name}") as key:
                    path_str = winreg.QueryValue(key, None)
                    if path_str:
                        p = Path(path_str.strip('"'))
                        if p.exists():
                            return p
            except FileNotFoundError:
                continue
    except ImportError:
        pass

    # Filesystem fallback
    for candidate in _STANDARD_BROWSER_PATHS.get(exe_name, []):
        p = Path(candidate)
        if p.exists():
            return p

    return None


def create_firewall_rules() -> bool:
    """Create firewall rules to block STUN/TURN for WebRTC. Requires admin."""
    if sys.platform != "win32":
        logger.warning("Not on Windows — skipping firewall rules")
        return True

    success = True
    for browser in BROWSER_EXES:
        browser_path = _find_browser_path(browser)
        if browser_path is None:
            logger.warning("Browser not found, skipping firewall rule: %s", browser)
            continue

        for port in STUN_PORTS:
            rule_name = f"{FW_RULE_PREFIX}-{browser.replace('.exe', '')}-udp-{port}"
            try:
                result = subprocess.run(
                    ["netsh", "advfirewall", "firewall", "add", "rule",
                     f"name={rule_name}",
                     "dir=out", "action=block", "protocol=UDP",
                     f"remoteport={port}",
                     f"program={browser_path}",
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


def _list_firewall_rules_by_prefix(prefix: str) -> list:
    """List all firewall rule names matching a prefix."""
    try:
        result = subprocess.run(
            ["netsh", "advfirewall", "firewall", "show", "rule", "name=all"],
            capture_output=True, text=True, timeout=30
        )
        rules = []
        for line in result.stdout.splitlines():
            # netsh output: "Rule Name:                            geo-fix-webrtc-chrome-udp-3478"
            if line.strip().startswith("Rule Name:"):
                name = line.split(":", 1)[1].strip()
                if name.startswith(prefix):
                    rules.append(name)
        return rules
    except Exception as e:
        logger.warning("Could not list firewall rules: %s", e)
        return []


def remove_firewall_rules() -> None:
    """Remove all geo-fix firewall rules by prefix.

    Finds all rules starting with FW_RULE_PREFIX via netsh query,
    then deletes each. Falls back to fixed-list if query fails.
    """
    if sys.platform != "win32":
        return

    rules = _list_firewall_rules_by_prefix(FW_RULE_PREFIX)
    if rules:
        for rule_name in rules:
            try:
                subprocess.run(
                    ["netsh", "advfirewall", "firewall", "delete", "rule",
                     f"name={rule_name}"],
                    capture_output=True, text=True, timeout=10
                )
            except Exception as e:
                logger.warning("Failed to delete rule %s: %s", rule_name, e)
        logger.info("Removed %d firewall rules by prefix", len(rules))
    else:
        # Fallback: try known names in case netsh query failed
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
        logger.info("Firewall rules removed (fallback method)")


# === Location Services ===

_LOCATION_KEY_PATH = (
    r"SOFTWARE\Microsoft\Windows\CurrentVersion\DeviceAccess\Global"
    r"\{BFA794E4-F964-4FDB-90F6-51056BFE4B44}"
)
_LOCATION_VALUE_NAME = "Value"
_LOCATION_VALID_VALUES = frozenset({"Allow", "Deny"})


def disable_location_services() -> Optional[str]:
    """Disable Windows Location Services by writing 'Deny' to the registry.

    Returns the original value ('Allow', 'Deny', or None if key/value was absent).
    Returns None without raising on non-Windows or if a registry error occurs.
    """
    if sys.platform != "win32":
        return None

    import winreg

    original: Optional[str] = None
    try:
        with winreg.CreateKeyEx(
            winreg.HKEY_CURRENT_USER,
            _LOCATION_KEY_PATH,
            0,
            winreg.KEY_READ | winreg.KEY_SET_VALUE,
        ) as key:
            try:
                original = winreg.QueryValueEx(key, _LOCATION_VALUE_NAME)[0]
            except FileNotFoundError:
                original = None

            winreg.SetValueEx(key, _LOCATION_VALUE_NAME, 0, winreg.REG_SZ, "Deny")
            logger.info(
                "Location Services disabled (original value: %r)", original
            )
    except OSError as exc:
        logger.warning("Could not disable Location Services: %s", exc)
        return None

    return original


def restore_location_services(original: Optional[str]) -> None:
    """Restore Windows Location Services to its original registry value.

    If *original* is None, the registry value is deleted (restoring the
    state where the value was absent).  Invalid values fall back to 'Deny'
    with a warning.
    """
    if sys.platform != "win32":
        return

    import winreg

    if original not in _LOCATION_VALID_VALUES and original is not None:
        logger.warning(
            "restore_location_services: unexpected value %r, defaulting to 'Deny'",
            original,
        )
        original = "Deny"

    try:
        with winreg.OpenKey(
            winreg.HKEY_CURRENT_USER,
            _LOCATION_KEY_PATH,
            0,
            winreg.KEY_SET_VALUE,
        ) as key:
            if original is None:
                try:
                    winreg.DeleteValue(key, _LOCATION_VALUE_NAME)
                except FileNotFoundError:
                    pass
                logger.info("Location Services registry value deleted")
            else:
                winreg.SetValueEx(key, _LOCATION_VALUE_NAME, 0, winreg.REG_SZ, original)
                logger.info("Location Services restored to %r", original)
    except OSError as exc:
        logger.warning("Could not restore Location Services: %s", exc)


# === Cleanup Persistence ===

_VALID_CLEANUP_LABELS = frozenset({
    CLEANUP_LABEL_CA_CERT,
    CLEANUP_LABEL_SESSION_TMPDIR,
    CLEANUP_LABEL_PROXY,
    CLEANUP_LABEL_FIREFOX,
    CLEANUP_LABEL_FIREWALL,
    CLEANUP_LABEL_LOCATION_SERVICES,
})


def write_cleanup_pending(failed_ops: list[str]) -> None:
    """Write failed cleanup operations to app data dir as JSON."""
    try:
        CLEANUP_PENDING_FILE.parent.mkdir(parents=True, exist_ok=True)
        CLEANUP_PENDING_FILE.write_text(json.dumps(failed_ops), encoding="utf-8")
        if sys.platform != "win32":
            os.chmod(str(CLEANUP_PENDING_FILE), 0o600)
        logger.info("Wrote cleanup_pending.json with %d operation(s)", len(failed_ops))
    except Exception as e:
        logger.warning("Could not write cleanup_pending.json: %s", e)


def delete_cleanup_pending() -> None:
    """Remove cleanup_pending.json if it exists."""
    try:
        CLEANUP_PENDING_FILE.unlink(missing_ok=True)
    except Exception as e:
        logger.warning("Could not delete cleanup_pending.json: %s", e)


def _execute_cleanup_by_label(label: str) -> None:
    """Execute a single cleanup operation by its label (stateless mode)."""
    if label == CLEANUP_LABEL_CA_CERT:
        uninstall_ca_cert(thumbprint=None)
    elif label == CLEANUP_LABEL_SESSION_TMPDIR:
        temp_dir = Path(tempfile.gettempdir())
        for d in temp_dir.glob("geo-fix-*"):
            if d.is_dir():
                shutil.rmtree(str(d), ignore_errors=True)
    elif label == CLEANUP_LABEL_PROXY:
        unset_wininet_proxy(None)
    elif label == CLEANUP_LABEL_FIREFOX:
        unset_firefox_proxy(None)
    elif label == CLEANUP_LABEL_FIREWALL:
        remove_firewall_rules()
    elif label == CLEANUP_LABEL_LOCATION_SERVICES:
        restore_location_services(original=None)
    else:
        logger.warning("Unknown cleanup label: %s — skipping", label)


def check_pending_cleanup() -> None:
    """Read cleanup_pending.json on startup and re-execute pending operations."""
    if not CLEANUP_PENDING_FILE.exists():
        return

    try:
        data = json.loads(CLEANUP_PENDING_FILE.read_text(encoding="utf-8"))
    except Exception as e:
        logger.warning("Could not read cleanup_pending.json: %s", e)
        return

    if not isinstance(data, list):
        logger.warning("cleanup_pending.json has invalid format — removing")
        CLEANUP_PENDING_FILE.unlink(missing_ok=True)
        return

    # Validate labels: only known strings are dispatched
    valid_labels = [l for l in data if isinstance(l, str) and l in _VALID_CLEANUP_LABELS]
    skipped = len(data) - len(valid_labels)
    if skipped:
        logger.warning("Skipped %d invalid label(s) in cleanup_pending.json", skipped)

    if not valid_labels:
        CLEANUP_PENDING_FILE.unlink(missing_ok=True)
        return

    logger.info("Found %d pending cleanup operation(s)", len(valid_labels))
    still_failed = []
    for label in valid_labels:
        try:
            _execute_cleanup_by_label(label)
        except Exception as e:
            logger.warning("Pending cleanup '%s' failed again: %s", label, e)
            still_failed.append(label)

    if still_failed:
        try:
            CLEANUP_PENDING_FILE.write_text(json.dumps(still_failed), encoding="utf-8")
        except Exception as e:
            logger.warning("Could not update cleanup_pending.json: %s", e)
        logger.warning("%d pending cleanup operation(s) still failing", len(still_failed))
    else:
        CLEANUP_PENDING_FILE.unlink(missing_ok=True)
        logger.info("All pending cleanup operations completed successfully")


# === Cleanup ===

def stateless_cleanup() -> None:
    """Best-effort cleanup when no state file is available.

    Detects and reverts geo-fix artifacts by well-known markers:
    - Proxy: if ProxyServer contains 127.0.0.1, disable proxy
    - CA cert: remove any mitmproxy cert by name
    - Tmpdirs: remove geo-fix-* directories in system temp
    - Firewall: remove all rules with geo-fix prefix
    - Firefox: remove user.js if it contains geo-fix marker
    All operations are idempotent and safe to run even if geo-fix was not running.
    """
    logger.info("Running stateless best-effort cleanup...")

    # Reset proxy if it points to localhost (likely ours)
    if sys.platform == "win32":
        current = _get_registry_proxy_settings()
        if current.get("ProxyEnable") == 1 and "127.0.0.1" in current.get("ProxyServer", ""):
            logger.info("Proxy points to localhost — resetting")
            unset_wininet_proxy({"ProxyEnable": 0, "ProxyServer": "", "ProxyOverride": ""})

    # Remove mitmproxy CA cert by name (fallback — no thumbprint)
    uninstall_ca_cert(thumbprint=None)

    # Clean up geo-fix-* tmpdirs in system temp
    temp_dir = Path(tempfile.gettempdir())
    for d in temp_dir.glob("geo-fix-*"):
        if d.is_dir():
            logger.info("Removing leftover tmpdir: %s", d)
            shutil.rmtree(str(d), ignore_errors=True)

    # Remove firewall rules
    remove_firewall_rules()

    # Check Firefox user.js for geo-fix marker
    profile = _find_firefox_profile()
    if profile:
        user_js = profile / "user.js"
        if user_js.exists():
            try:
                content = user_js.read_text(encoding="utf-8")
                if "geo-fix: proxy configuration" in content:
                    # Check for backup
                    backup = user_js.with_suffix(".js.geo-fix-backup")
                    if backup.exists():
                        shutil.copy2(str(backup), str(user_js))
                        backup.unlink()
                        logger.info("Firefox user.js restored from backup (stateless)")
                    else:
                        user_js.unlink()
                        logger.info("Firefox user.js removed (contained geo-fix marker)")
            except Exception as e:
                logger.warning("Could not check Firefox user.js: %s", e)

    # Delete state file if it exists
    delete_state()
    logger.info("Stateless cleanup complete")


def cleanup(state: Optional[ProxyState] = None) -> list[str]:
    """Revert all system changes. Used on stop and crash recovery.

    Each step is tried once; on failure, retried after a 3-second delay.
    Returns list of step labels that failed after retry (empty on full success).

    Cleanup order: CA cert → session tmpdir → proxy → Firefox → firewall → Location Services → state file.
    CA cert and tmpdir MUST be removed before delete_state() because they need
    thumbprint and path from the state object.
    """
    if state is None:
        state = load_state()

    if state is None:
        logger.info("No state file found — attempting best-effort stateless cleanup")
        stateless_cleanup()
        return []

    logger.info("Running cleanup...")
    failures = []

    def _try_step(label: str, func, *args, **kwargs) -> None:
        """Execute a cleanup step with one retry on failure."""
        try:
            func(*args, **kwargs)
        except Exception as e:
            logger.warning("Cleanup step '%s' failed: %s — retrying in %ds",
                           label, e, _CLEANUP_RETRY_DELAY)
            time.sleep(_CLEANUP_RETRY_DELAY)
            try:
                func(*args, **kwargs)
            except Exception as e2:
                failures.append(label)
                logger.error("Cleanup step '%s' failed after retry: %s", label, e2)

    # Remove CA cert BEFORE deleting state (needs thumbprint)
    _try_step(CLEANUP_LABEL_CA_CERT, uninstall_ca_cert, state.ca_thumbprint)

    # Delete session tmpdir (contains CA private key)
    _try_step(CLEANUP_LABEL_SESSION_TMPDIR, delete_session_tmpdir, state.session_tmpdir)

    # Restore proxy
    original = {
        "ProxyEnable": state.original_proxy_enable or 0,
        "ProxyServer": state.original_proxy_server or "",
        "ProxyOverride": state.original_proxy_override or "",
    }
    _try_step(CLEANUP_LABEL_PROXY, unset_wininet_proxy, original)

    # Restore Firefox
    if state.firefox_prefs_modified:
        _try_step(CLEANUP_LABEL_FIREFOX, unset_firefox_proxy, state.firefox_prefs_backup)

    # Remove firewall rules (unconditional — rules are created every session)
    _try_step(CLEANUP_LABEL_FIREWALL, remove_firewall_rules)

    # Restore Location Services if we disabled them
    _try_step(CLEANUP_LABEL_LOCATION_SERVICES, restore_location_services, state.original_location_services)

    # Delete state file
    delete_state()

    if failures:
        logger.warning("Cleanup completed with %d failure(s): %s", len(failures), "; ".join(failures))
    else:
        logger.info("Cleanup complete")

    return failures
