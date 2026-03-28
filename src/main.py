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
    ProxyState,
    cleanup,
    create_firewall_rules,
    create_session_tmpdir,
    delete_state,
    install_ca_cert,
    load_state,
    save_state,
    set_firefox_proxy,
    set_wininet_proxy,
    unset_firefox_proxy,
    unset_wininet_proxy,
)
from src.setup_wizard import is_setup_complete, run_setup_wizard
from src.proxy_addon import GeoFixAddon
from src.tray import GeoFixTray

from src.watchdog import STOP_FLAG_NAME

logger = logging.getLogger("geo-fix")

# Global state for cleanup
_cleanup_done = False
_cleanup_lock = threading.Lock()
_watchdog_proc = None
_stop_token = None
_session_tmpdir = None


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
            if state:
                cleanup(state)
            _remove_onlogon_task()
            release_instance_lock()
        except Exception as e:
            logger.error("Cleanup error: %s", e)


def _start_mitmproxy(addon: GeoFixAddon, confdir: str = None, port: int = PROXY_PORT) -> threading.Thread:
    """Start mitmproxy in a background thread."""
    from mitmproxy.options import Options
    from mitmproxy.tools.dump import DumpMaster

    def run_proxy():
        kwargs = dict(listen_host=PROXY_HOST, listen_port=port)
        if confdir:
            kwargs["confdir"] = confdir
        opts = Options(**kwargs)
        master = DumpMaster(opts)
        master.addons.add(addon)

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
            return thread

    logger.error("mitmproxy failed to start within 15 seconds")
    sys.exit(1)


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
    global _session_tmpdir, _stop_token, _watchdog_proc
    session_id = str(uuid.uuid4())
    session_tmpdir = create_session_tmpdir()
    _session_tmpdir = session_tmpdir
    stop_token = secrets.token_hex(32)
    _stop_token = stop_token

    # Select port (before any system modifications)
    port = _select_port(args.port)

    # Create proxy addon
    addon = GeoFixAddon(preset)

    # Start mitmproxy FIRST — no system changes until proxy is confirmed running
    proxy_thread = _start_mitmproxy(addon, confdir=session_tmpdir, port=port)

    # Install CA cert after mitmproxy generates it, capture thumbprint
    ca_thumbprint = install_ca_cert(session_tmpdir)

    # Save partial state so watchdog can load it for crash recovery
    original_proxy = set_wininet_proxy(port=port)
    firefox_backup = set_firefox_proxy(port=port)

    # Create firewall rules (every session, not just wizard)
    create_firewall_rules()

    state = ProxyState(
        pid=os.getpid(),
        preset_code=country_code,
        timestamp=datetime.datetime.now().isoformat(),
        original_proxy_enable=original_proxy.get("ProxyEnable"),
        original_proxy_server=original_proxy.get("ProxyServer"),
        original_proxy_override=original_proxy.get("ProxyOverride"),
        firefox_prefs_modified=firefox_backup is not None,
        firefox_prefs_backup=firefox_backup,
        session_id=session_id,
        session_tmpdir=session_tmpdir,
        ca_thumbprint=ca_thumbprint,
        proxy_port=port,
    )
    save_state(state)

    # Spawn watchdog after state is saved (so it can load state on crash)
    from src.system_config import STATE_FILE
    _watchdog_proc = _spawn_watchdog(
        os.getpid(), str(STATE_FILE), session_tmpdir, session_id, stop_token
    )

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
