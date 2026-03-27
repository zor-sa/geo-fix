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
import signal
import sys
import threading
import time

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
    delete_state,
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

logger = logging.getLogger("geo-fix")

# Global state for cleanup
_cleanup_done = False
_cleanup_lock = threading.Lock()


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
        state = load_state()
        if state:
            cleanup(state)
        release_instance_lock()
    except Exception as e:
        logger.error("Cleanup error: %s", e)


def _start_mitmproxy(addon: GeoFixAddon) -> threading.Thread:
    """Start mitmproxy in a background thread."""
    from mitmproxy.options import Options
    from mitmproxy.tools.dump import DumpMaster

    def run_proxy():
        opts = Options(listen_host=PROXY_HOST, listen_port=PROXY_PORT)
        master = DumpMaster(opts)
        master.addons.add(addon)

        # Verify binding
        logger.info("Starting mitmproxy on %s:%d", PROXY_HOST, PROXY_PORT)

        try:
            master.run()
        except Exception as e:
            logger.error("mitmproxy error: %s", e)

    thread = threading.Thread(target=run_proxy, daemon=True, name="mitmproxy")
    thread.start()

    # Wait for proxy to start
    for _ in range(30):
        time.sleep(0.5)
        if check_proxy_running(PROXY_HOST, PROXY_PORT):
            logger.info("mitmproxy is running")
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
        firewall_created = run_setup_wizard()
    else:
        firewall_created = False

    # Save original proxy settings
    original_proxy = set_wininet_proxy()
    firefox_backup = set_firefox_proxy()

    # Save state for crash recovery
    state = ProxyState(
        pid=os.getpid(),
        preset_code=country_code,
        timestamp=datetime.datetime.now().isoformat(),
        original_proxy_enable=original_proxy.get("ProxyEnable"),
        original_proxy_server=original_proxy.get("ProxyServer"),
        original_proxy_override=original_proxy.get("ProxyOverride"),
        firefox_prefs_modified=firefox_backup is not None,
        firefox_prefs_backup=firefox_backup,
        firewall_rules_created=firewall_created,
    )
    save_state(state)

    # Create proxy addon
    addon = GeoFixAddon(preset)

    # Start mitmproxy
    proxy_thread = _start_mitmproxy(addon)

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
