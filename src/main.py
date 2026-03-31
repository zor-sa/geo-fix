"""geo-fix: Main entry point and CLI.

Usage:
    geo-fix US          Start spoofing with US preset
    geo-fix DE          Start spoofing with DE preset
    geo-fix --stop      Stop running instance
    geo-fix --cleanup   Clean up stale state (manual recovery)
    geo-fix --setup     Re-run setup wizard
"""

import argparse
import atexit
import datetime
import logging
import os
from pathlib import Path
import secrets
import signal
import socket
import subprocess
import sys
import threading
import time
import uuid

# Add project root to path for imports
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src.presets import PRESETS, get_preset
from src.health_check import (
    VpnStatus,
    acquire_instance_lock,
    check_proxy_running,
    check_vpn_status,
    release_instance_lock,
)
from src.system_config import (
    PROXY_HOST,
    PROXY_PORT,
    STATE_FILE,
    ProxyState,
    check_pending_cleanup,
    cleanup,
    create_session_tmpdir,
    delete_ca_key_files,
    delete_ca_public_cert,
    delete_cleanup_pending,
    delete_state,
    disable_location_services,
    install_ca_cert,
    load_state,
    remove_firewall_rules,
    restore_location_services,
    save_state,
    set_firefox_proxy,
    set_wininet_proxy,
    unset_firefox_proxy,
    unset_wininet_proxy,
    write_cleanup_pending,
)
from src.setup_wizard import is_setup_complete, run_setup_wizard
from src.proxy_addon import FlowCleanup, GeoFixAddon
from src.tray import GeoFixTray

from src.watchdog import STOP_FLAG_NAME

logger = logging.getLogger("geo-fix")

# Global state for cleanup
_cleanup_done = False
_cleanup_lock = threading.Lock()
_watchdog_proc = None
_stop_token = None
_session_tmpdir = None
_session_id = None

# RAM monitor state
_last_restart_time: float = 0.0
_restart_timestamps: list[float] = []

# RAM monitor constants
_RAM_THRESHOLD_MB = 300.0
_COOLDOWN_SECONDS = 600  # 10 minutes
_RATE_LIMIT_MAX = 3
_RATE_LIMIT_WINDOW = 3600  # 1 hour
_IDLE_GUARD_SECONDS = 10


def _setup_logging():
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(name)s] %(levelname)s: %(message)s",
        datefmt="%H:%M:%S",
    )


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="geo-fix: Spoof geolocation signals to complement VPN",
        usage="geo-fix [COUNTRY_CODE | --stop | --cleanup | --setup]",
    )
    parser.add_argument(
        "country",
        nargs="?",
        help=f"Country code to activate: {', '.join(sorted(PRESETS.keys()))}",
    )
    parser.add_argument("--stop", action="store_true", help="Stop running instance")
    parser.add_argument("--cleanup", action="store_true", help="Clean up stale state")
    parser.add_argument("--setup", action="store_true", help="Re-run setup wizard")
    parser.add_argument("--port", type=int, default=None,
                        help="Proxy port (default: 8080, auto-select if occupied)")
    return parser.parse_args()


def _validate_country(code: str) -> str:
    """Validate and normalize country code."""
    code = code.upper().strip()
    if len(code) != 2 or not code.isalpha():
        print(f"Ошибка: '{code}' — неверный код страны. Ожидается 2 буквы.")
        sys.exit(1)
    if code not in PRESETS:
        valid = ", ".join(sorted(PRESETS.keys()))
        print(f"Ошибка: '{code}' — неизвестная страна. Доступные: {valid}")
        sys.exit(1)
    return code


def _select_port(requested: int = None) -> int:
    """Select a proxy port. Tries requested (or 8080), falls back to auto-select."""
    target = requested if requested is not None else PROXY_PORT
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            s.bind((PROXY_HOST, target))
            return target
    except OSError:
        logger.info("Port %d is occupied, auto-selecting...", target)

    # Auto-select
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            s.bind((PROXY_HOST, 0))
            port = s.getsockname()[1]
            logger.info("Auto-selected port %d", port)
            return port
    except OSError as e:
        raise RuntimeError(
            "Cannot bind any proxy port — aborting. Registry not modified."
        ) from e


def _get_watchdog_path() -> str:
    """Get path to watchdog.py, handling PyInstaller bundles."""
    if getattr(sys, "frozen", False):
        return str(Path(sys._MEIPASS) / "src" / "watchdog.py")
    return str(Path(__file__).parent / "watchdog.py")


def _spawn_watchdog(main_pid: int, state_file: str, session_tmpdir: str,
                    session_id: str, stop_token: str) -> subprocess.Popen:
    """Spawn watchdog subprocess."""
    watchdog_path = _get_watchdog_path()
    cmd = [sys.executable, watchdog_path, str(main_pid), state_file,
           session_tmpdir, session_id, stop_token]

    kwargs = {}
    if sys.platform == "win32":
        CREATE_NEW_PROCESS_GROUP = 0x00000200
        CREATE_NO_WINDOW = 0x08000000
        kwargs["creationflags"] = CREATE_NEW_PROCESS_GROUP | CREATE_NO_WINDOW

    proc = subprocess.Popen(cmd, **kwargs)
    logger.info("Watchdog spawned: PID %d", proc.pid)
    return proc


def _signal_watchdog_stop(session_tmpdir: str, stop_token: str) -> None:
    """Write stop token to flag file so watchdog exits cleanly."""
    if session_tmpdir and stop_token:
        flag_path = Path(session_tmpdir) / STOP_FLAG_NAME
        try:
            flag_path.write_text(stop_token, encoding="utf-8")
        except Exception as e:
            logger.warning("Could not write watchdog stop flag: %s", e)


def _remove_onlogon_task() -> None:
    """Remove the ONLOGON scheduled task."""
    try:
        subprocess.run(
            ["schtasks", "/delete", "/tn", "geo-fix-cleanup", "/f"],
            capture_output=True, timeout=10
        )
    except Exception as e:
        logger.debug("Could not remove ONLOGON task: %s", e)


def _handle_stop():
    """Stop a running instance by reading its PID from state file."""
    state = load_state()
    if state is None:
        print("geo-fix не запущен (нет файла состояния)")
        sys.exit(0)

    pid = state.pid
    try:
        os.kill(pid, signal.SIGTERM)
        print(f"Отправлен сигнал остановки процессу {pid}")
        # Wait a bit for cleanup
        time.sleep(2)
        if load_state() is not None:
            # Process didn't clean up — do it ourselves
            cleanup(state)
            print("Настройки восстановлены")
    except ProcessLookupError:
        print(f"Процесс {pid} не найден. Выполняю очистку...")
        cleanup(state)
        print("Настройки восстановлены")
    except PermissionError:
        print(f"Нет прав на остановку процесса {pid}")
        sys.exit(1)


def _handle_cleanup():
    """Clean up stale state without starting a new session."""
    state = load_state()
    if state is None:
        print("Нечего очищать — файла состояния нет")
    else:
        cleanup(state)
        print("Очистка завершена. Настройки восстановлены.")
    release_instance_lock()


def _do_cleanup():
    """Perform cleanup on exit. Called by atexit and signal handlers."""
    global _cleanup_done
    with _cleanup_lock:
        if _cleanup_done:
            return
        _cleanup_done = True

        logger.info("Performing cleanup...")
        try:
            # Signal watchdog to stop BEFORE cleanup deletes tmpdir
            _signal_watchdog_stop(_session_tmpdir, _stop_token)
            state = load_state()
            failures = []
            if state:
                failures = cleanup(state)
            _remove_onlogon_task()
            release_instance_lock()
            if failures:
                write_cleanup_pending(failures)
                msg = "Не удалось полностью очистить:\n" + "\n".join(f"  - {f}" for f in failures)
                msg += "\nОчистка будет выполнена автоматически при следующем запуске."
                print(msg, file=sys.stderr)
            else:
                delete_cleanup_pending()
        except Exception as e:
            logger.error("Cleanup error: %s", e)


def _get_memory_bytes_windows() -> float:
    """Read PrivateUsage via Windows GetProcessMemoryInfo. Returns bytes."""
    import ctypes
    import ctypes.wintypes

    class PROCESS_MEMORY_COUNTERS_EX(ctypes.Structure):
        _fields_ = [
            ("cb", ctypes.wintypes.DWORD),
            ("PageFaultCount", ctypes.wintypes.DWORD),
            ("PeakWorkingSetSize", ctypes.c_size_t),
            ("WorkingSetSize", ctypes.c_size_t),
            ("QuotaPeakPagedPoolUsage", ctypes.c_size_t),
            ("QuotaPagedPoolUsage", ctypes.c_size_t),
            ("QuotaPeakNonPagedPoolUsage", ctypes.c_size_t),
            ("QuotaNonPagedPoolUsage", ctypes.c_size_t),
            ("PagefileUsage", ctypes.c_size_t),
            ("PeakPagefileUsage", ctypes.c_size_t),
            ("PrivateUsage", ctypes.c_size_t),
        ]

    try:
        kernel32 = ctypes.windll.kernel32
        psapi = ctypes.windll.psapi
        handle = kernel32.GetCurrentProcess()
        counters = PROCESS_MEMORY_COUNTERS_EX()
        counters.cb = ctypes.sizeof(counters)
        if psapi.GetProcessMemoryInfo(handle, ctypes.byref(counters), counters.cb):
            return float(counters.PrivateUsage)
        return 0.0
    except OSError as e:
        logger.warning("GetProcessMemoryInfo failed: %s", e)
        return 0.0


def _get_memory_mb_linux() -> float:
    """Read VmRSS from /proc/self/status. Returns MB."""
    try:
        with open("/proc/self/status") as f:
            for line in f:
                if line.startswith("VmRSS:"):
                    # Format: "VmRSS:   153600 kB"
                    parts = line.split()
                    if len(parts) >= 2:
                        return float(parts[1]) / 1024.0  # kB to MB
        return 0.0
    except FileNotFoundError:
        return 0.0
    except (ValueError, OSError) as e:
        logger.warning("Failed to read /proc/self/status: %s", e)
        return 0.0


def _get_process_memory_mb() -> float:
    """Get process memory usage in MB. Windows: PrivateUsage, Linux: VmRSS."""
    if sys.platform == "win32":
        return _get_memory_bytes_windows() / (1024 * 1024)  # bytes to MB
    return _get_memory_mb_linux()  # already MB


def _should_restart(
    mem_mb: float,
    last_flow_time: float,
    last_restart_time: float,
    restart_timestamps: list[float],
    now: float,
) -> tuple[bool, str]:
    """Check all RAM restart guards. Returns (should_restart, reason).

    Reason is empty string if should restart, otherwise describes why blocked.
    """
    if mem_mb < _RAM_THRESHOLD_MB:
        return False, "below_threshold"

    # Idle guard
    idle_seconds = now - last_flow_time
    if idle_seconds < _IDLE_GUARD_SECONDS:
        return False, f"traffic_active ({idle_seconds:.1f}s ago)"

    # Cooldown
    if now - last_restart_time < _COOLDOWN_SECONDS:
        return False, f"cooldown ({now - last_restart_time:.0f}s / {_COOLDOWN_SECONDS}s)"

    # Rate limit
    recent = [t for t in restart_timestamps if now - t < _RATE_LIMIT_WINDOW]
    if len(recent) >= _RATE_LIMIT_MAX:
        return False, f"rate_limit ({len(recent)} restarts in last hour)"

    return True, ""


def _start_mitmproxy(addon: GeoFixAddon, confdir: str = None, port: int = PROXY_PORT) -> tuple[threading.Thread, "Master"]:
    """Start mitmproxy in a background thread.

    Returns (thread, master) tuple so callers can access the master instance.
    """
    from mitmproxy.options import Options
    from mitmproxy.master import Master
    from mitmproxy.addons.core import Core
    from mitmproxy.addons.proxyserver import Proxyserver
    from mitmproxy.addons.next_layer import NextLayer
    from mitmproxy.addons.tlsconfig import TlsConfig
    from mitmproxy.addons.keepserving import KeepServing
    from mitmproxy.addons.errorcheck import ErrorCheck

    master_ref = {}

    def run_proxy():
        kwargs = dict(listen_host=PROXY_HOST, listen_port=port)
        if confdir:
            kwargs["confdir"] = confdir
        opts = Options(**kwargs)
        master = Master(opts)
        master.addons.add(
            Core(), Proxyserver(), NextLayer(), TlsConfig(),
            KeepServing(), ErrorCheck(), addon, FlowCleanup()
        )
        master_ref["master"] = master

        logger.info("Starting mitmproxy on %s:%d", PROXY_HOST, port)

        try:
            master.run()
        except Exception as e:
            logger.error("mitmproxy error: %s", e)

    thread = threading.Thread(target=run_proxy, daemon=True, name="mitmproxy")
    thread.start()

    # Wait for proxy to start
    for _ in range(30):
        time.sleep(0.5)
        if check_proxy_running(PROXY_HOST, port):
            logger.info("mitmproxy is running on port %d", port)
            return thread, master_ref.get("master")

    logger.error("mitmproxy failed to start within 15 seconds")
    sys.exit(1)


def _restart_mitmproxy(
    old_master,
    addon: GeoFixAddon,
    confdir: str,
    port: int,
    state: "ProxyState",
) -> tuple[threading.Thread, "Master"]:
    """Restart mitmproxy thread: shutdown old master, regenerate CA, start new master.

    Returns (new_thread, new_master) or (None, None) on failure.
    """
    # 1. Shutdown old master
    try:
        old_master.shutdown()
    except Exception as e:
        logger.error("Failed to shutdown old master: %s", e)
        return None, None

    # 2. Uninstall old CA cert
    old_thumbprint = state.ca_thumbprint
    try:
        from src.system_config import uninstall_ca_cert as _uninstall
        _uninstall(thumbprint=old_thumbprint)
    except Exception as e:
        logger.warning("Failed to uninstall old CA (thumbprint=%s) — may remain in trust store: %s",
                       old_thumbprint, e)

    # 3. Start new master (generates new CA in confdir)
    # _start_mitmproxy re-adds the same GeoFixAddon instance + new FlowCleanup()
    try:
        new_thread, new_master = _start_mitmproxy(addon, confdir=confdir, port=port)
    except SystemExit:
        logger.error("_start_mitmproxy called sys.exit during restart — proxy failed to start")
        return None, None
    except Exception as e:
        logger.error("Failed to start new mitmproxy: %s", e)
        return None, None

    # 4. Install new CA cert
    new_thumbprint = install_ca_cert(confdir)
    if new_thumbprint is None:
        logger.error("CA cert install failed after restart — proxy running without trusted CA")
        # Delete CA key files even on failure — security hardening
        delete_ca_key_files(confdir)
        delete_ca_public_cert(confdir)
        # Shutdown the new master — no trusted CA means broken HTTPS
        try:
            new_master.shutdown()
        except Exception:
            pass
        return None, None

    # 5. Delete CA key files from disk (preserve security hardening)
    delete_ca_key_files(confdir)
    delete_ca_public_cert(confdir)

    # 6. Update state
    state.ca_thumbprint = new_thumbprint
    save_state(state)

    logger.info("mitmproxy restarted successfully (new CA thumbprint: %s)", new_thumbprint[:12])
    return new_thread, new_master


def _monitor_tick(last_vpn):
    """Single tick of VPN + watchdog monitoring. Returns updated last_vpn state."""
    global _watchdog_proc
    # Check VPN
    try:
        vpn = check_vpn_status()
        if vpn == VpnStatus.NOT_DETECTED and last_vpn != VpnStatus.NOT_DETECTED:
            logger.warning("VPN disconnected!")
            print("⚠ VPN отключён! Реальный IP может быть виден.", file=sys.stderr)
        elif vpn == VpnStatus.DETECTED and last_vpn == VpnStatus.NOT_DETECTED:
            logger.info("VPN reconnected")
            print("✓ VPN восстановлен.", file=sys.stderr)
        if vpn != VpnStatus.UNKNOWN:
            last_vpn = vpn
    except Exception as e:
        logger.warning("VPN check error: %s", e)
    # Check watchdog health (under lock to avoid race with _do_cleanup)
    with _cleanup_lock:
        if _watchdog_proc and _watchdog_proc.poll() is not None:
            logger.warning("Watchdog died (rc=%s), respawning...", _watchdog_proc.returncode)
            try:
                _watchdog_proc = _spawn_watchdog(
                    os.getpid(), str(STATE_FILE), _session_tmpdir, _session_id, _stop_token
                )
            except Exception as e:
                logger.error("Failed to respawn watchdog: %s", e)
    return last_vpn


def main():
    _setup_logging()
    args = _parse_args()

    # Handle --stop
    if args.stop:
        _handle_stop()
        return

    # Handle --cleanup
    if args.cleanup:
        _handle_cleanup()
        return

    # Handle --setup
    if args.setup:
        run_setup_wizard(force=True)
        return

    # Validate country code
    if not args.country:
        valid = ", ".join(sorted(PRESETS.keys()))
        print(f"Использование: geo-fix [COUNTRY_CODE]\nДоступные страны: {valid}")
        print("Другие команды: --stop, --cleanup, --setup")
        sys.exit(1)

    country_code = _validate_country(args.country)
    preset = get_preset(country_code)

    # Check for stale state and clean up
    stale = load_state()
    if stale:
        logger.info("Found stale state from PID %d, cleaning up...", stale.pid)
        cleanup(stale)

    # Single-instance guard
    if not acquire_instance_lock():
        print("geo-fix уже запущен. Используйте --stop для остановки.")
        sys.exit(1)

    # Clean up pending operations from previous failed cleanup (after lock)
    try:
        check_pending_cleanup()
    except Exception as e:
        logger.warning("check_pending_cleanup failed: %s — continuing", e)

    # Register cleanup handlers
    atexit.register(_do_cleanup)
    signal.signal(signal.SIGTERM, lambda sig, frame: (_do_cleanup(), sys.exit(0)))

    # VPN check
    vpn = check_vpn_status()
    if vpn == VpnStatus.NOT_DETECTED:
        print("⚠ VPN не обнаружен! Рекомендуется включить VPN перед использованием geo-fix.")
        print("  Продолжить без VPN? Реальный IP может быть виден.")
        # In GUI mode, this would be a dialog. For CLI, just warn and continue.

    # First-time setup
    if not is_setup_complete():
        logger.info("First run — launching setup wizard")
        run_setup_wizard()

    # Create per-session tmpdir for ephemeral CA
    global _session_tmpdir, _stop_token, _watchdog_proc, _session_id
    session_id = str(uuid.uuid4())
    session_tmpdir = create_session_tmpdir()
    _session_tmpdir = session_tmpdir
    _session_id = session_id
    stop_token = secrets.token_hex(32)
    _stop_token = stop_token

    # Select port (before any system modifications)
    port = _select_port(args.port)

    # Create proxy addon
    addon = GeoFixAddon(preset)

    # Start mitmproxy FIRST — no system changes until proxy is confirmed running
    proxy_thread, proxy_master = _start_mitmproxy(addon, confdir=session_tmpdir, port=port)  # noqa: F841 — proxy_master used by Task 3

    # Delete CA private key from disk — mitmproxy has loaded it into memory.
    # Key exists on disk only during mitmproxy startup (seconds, not hours).
    delete_ca_key_files(session_tmpdir)

    # Incremental state saving: save after EACH system change so watchdog
    # can recover from crash at any point during startup sequence.
    state = ProxyState(
        pid=os.getpid(),
        preset_code=country_code,
        timestamp=datetime.datetime.now().isoformat(),
        session_id=session_id,
        session_tmpdir=session_tmpdir,
        proxy_port=port,
    )

    # Install CA cert (public cert still on disk for certutil)
    ca_thumbprint = install_ca_cert(session_tmpdir)
    # Now delete the public cert too — no longer needed
    delete_ca_public_cert(session_tmpdir)
    state.ca_thumbprint = ca_thumbprint
    save_state(state)

    # Spawn watchdog BEFORE system modifications (so it can recover from crash)
    from src.system_config import STATE_FILE
    _watchdog_proc = _spawn_watchdog(
        os.getpid(), str(STATE_FILE), session_tmpdir, session_id, stop_token
    )

    # Each system change: modify → update state → save (crash-safe)
    original_proxy = set_wininet_proxy(port=port)
    state.original_proxy_enable = original_proxy.get("ProxyEnable")
    state.original_proxy_server = original_proxy.get("ProxyServer")
    state.original_proxy_override = original_proxy.get("ProxyOverride")
    save_state(state)

    firefox_backup = set_firefox_proxy(port=port)
    state.firefox_prefs_modified = firefox_backup is not None
    state.firefox_prefs_backup = firefox_backup
    save_state(state)

    # Remove legacy STUN firewall rules from previous versions (if any)
    # WebRTC protection now uses iceTransportPolicy='relay' instead
    remove_firewall_rules()

    # Disable Location Services and store original value for cleanup
    original_location_services = disable_location_services()
    state.original_location_services = original_location_services
    save_state(state)

    # Country switch callback for tray
    def on_switch_country(code: str):
        new_preset = get_preset(code)
        addon.switch_preset(new_preset)
        logger.info("Switched to %s (%s)", new_preset.name_ru, code)

    # Stop callback for tray
    stop_event = threading.Event()

    def on_stop():
        stop_event.set()

    # Start tray icon
    tray = GeoFixTray(preset, on_switch_country, on_stop)
    tray_thread = tray.start_threaded()

    # Mutable reference for proxy master (updated on restart)
    proxy_ref = {"master": proxy_master, "thread": proxy_thread}

    # Start monitoring loop (VPN status + watchdog health + RAM)
    def _monitor_loop():
        global _last_restart_time, _restart_timestamps
        last_vpn = None
        while not stop_event.is_set():
            stop_event.wait(timeout=60)
            if stop_event.is_set():
                break
            last_vpn = _monitor_tick(last_vpn)

            # RAM check
            try:
                mem_mb = _get_process_memory_mb()
                now = time.monotonic()
                should, reason = _should_restart(
                    mem_mb, addon._last_flow_time,
                    _last_restart_time, _restart_timestamps, now
                )
                if not should:
                    if mem_mb >= _RAM_THRESHOLD_MB:
                        logger.info("RAM restart blocked: %s (%.1f MB)", reason, mem_mb)
                    continue

                # All guards passed — restart
                logger.info("Initiating mitmproxy restart (RAM: %.1f MB)", mem_mb)
                # Prune old timestamps
                _restart_timestamps[:] = [t for t in _restart_timestamps if now - t < _RATE_LIMIT_WINDOW]
                new_thread, new_master = _restart_mitmproxy(
                    proxy_ref["master"], addon, session_tmpdir, port, state
                )
                if new_thread is not None and new_master is not None:
                    proxy_ref["master"] = new_master
                    proxy_ref["thread"] = new_thread
                    _last_restart_time = time.monotonic()
                    _restart_timestamps.append(_last_restart_time)
                    logger.info("mitmproxy restart complete")
                else:
                    # Set cooldown to prevent immediate re-trigger on dead master
                    _last_restart_time = time.monotonic()
                    logger.error("mitmproxy restart failed — cooldown activated, will retry after %d sec",
                                 _COOLDOWN_SECONDS)
            except Exception as e:
                logger.error("RAM monitor error: %s", e)

    monitor_thread = threading.Thread(target=_monitor_loop, daemon=True, name="monitor")
    monitor_thread.start()

    print(f"✓ geo-fix запущен: {preset.name_ru} ({country_code})")
    print("  Иконка в трее → правая кнопка мыши для управления")

    # Wait for stop signal
    try:
        stop_event.wait()
    except KeyboardInterrupt:
        pass

    # Cleanup
    logger.info("Shutting down...")
    tray.stop()
    _do_cleanup()
    print("✓ geo-fix остановлен. Настройки восстановлены.")


if __name__ == "__main__":
    main()
