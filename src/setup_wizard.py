"""First-run setup wizard for geo-fix.

Guides the user through: CA certificate installation, Firefox configuration,
optional firewall rules, and DNS setup instructions.
Uses tkinter for native Windows dialogs.
"""

import logging
import os
import subprocess
import sys
import webbrowser
from pathlib import Path
from typing import Optional

from .system_config import (
    MITMPROXY_CA_CERT,
    create_firewall_rules,
    install_ca_cert,
)

logger = logging.getLogger("geo-fix.wizard")

# Setup completion flag file
SETUP_COMPLETE_FILE = Path(os.path.dirname(os.path.abspath(sys.argv[0]))) / ".geo-fix-setup-done"


def is_setup_complete() -> bool:
    """Check if first-time setup has been completed."""
    return SETUP_COMPLETE_FILE.exists()


def mark_setup_complete() -> None:
    """Mark setup as complete."""
    SETUP_COMPLETE_FILE.write_text("done")


def run_setup_wizard(force: bool = False) -> bool:
    """Run the setup wizard. Returns True if setup completed successfully.

    Args:
        force: If True, run even if setup was already completed.
    """
    if not force and is_setup_complete():
        logger.info("Setup already completed, skipping wizard")
        return True

    # Check if CA cert exists (mitmproxy generates it on first run)
    if not MITMPROXY_CA_CERT.exists():
        logger.info("CA cert not found — will be generated on first proxy start")

    try:
        return _run_gui_wizard()
    except Exception as e:
        logger.error("GUI wizard failed: %s. Falling back to console.", e)
        return _run_console_wizard()


def _run_gui_wizard() -> bool:
    """Run the tkinter-based GUI wizard."""
    import tkinter as tk
    from tkinter import messagebox

    root = tk.Tk()
    root.title("geo-fix — Первоначальная настройка")
    root.geometry("500x400")
    root.resizable(False, False)

    # Center window
    root.update_idletasks()
    x = (root.winfo_screenwidth() - 500) // 2
    y = (root.winfo_screenheight() - 400) // 2
    root.geometry(f"+{x}+{y}")

    success = {"cert": False, "firewall": False, "all": False}
    current_step = {"value": 0}

    # Title
    tk.Label(root, text="Настройка geo-fix", font=("Arial", 16, "bold")).pack(pady=10)

    # Status frame
    status_frame = tk.Frame(root)
    status_frame.pack(fill="x", padx=20, pady=5)

    status_label = tk.Label(status_frame, text="", wraplength=450, justify="left")
    status_label.pack(fill="x")

    def update_status(text: str):
        status_label.config(text=text)
        root.update()

    # Step 1: Certificate
    def step_cert():
        update_status(
            "Шаг 1/3: Установка сертификата\n\n"
            "Для перехвата геосигналов в браузере нужно установить "
            "специальный сертификат. Это безопасно — сертификат работает "
            "только на вашем компьютере и используется только для подмены "
            "геолокации.\n\n"
            "Данные НЕ записываются и НЕ отправляются куда-либо."
        )
        if install_ca_cert():
            success["cert"] = True
            update_status("✓ Сертификат установлен для Chrome/Edge.\n\n"
                         "Firefox: сертификат будет подхвачен автоматически "
                         "через enterprise_roots.")
        else:
            update_status("⚠ Не удалось установить сертификат.\n"
                         "Возможно, сертификат ещё не сгенерирован.\n"
                         "Он будет создан при первом запуске прокси.")

    # Step 2: Firewall (optional)
    def step_firewall():
        result = messagebox.askyesno(
            "Шаг 2/3: Защита от WebRTC-утечек",
            "Хотите установить правила файрвола для максимальной "
            "защиты от утечки IP через WebRTC?\n\n"
            "Это потребует права администратора (появится запрос UAC).\n\n"
            "Без этого базовая защита всё равно работает, но менее надёжна.\n\n"
            "Установить правила файрвола?",
        )
        if result:
            if create_firewall_rules():
                success["firewall"] = True
                update_status("✓ Правила файрвола установлены.")
            else:
                update_status("⚠ Не удалось установить правила файрвола.\n"
                             "Базовая защита WebRTC всё равно работает.")
        else:
            update_status("Правила файрвола пропущены. Базовая защита WebRTC активна.")

    # Step 3: DNS
    def step_dns():
        update_status(
            "Шаг 3/3: Настройка DNS\n\n"
            "Для защиты от утечки DNS включите «Безопасный DNS» в настройках браузера.\n\n"
            "Chrome/Edge: Настройки → Конфиденциальность → Безопасность → Безопасный DNS → Включить\n\n"
            "Firefox: Настройки → Конфиденциальность → DNS через HTTPS → Включить"
        )

    def open_chrome_security():
        webbrowser.open("chrome://settings/security")

    def open_firefox_security():
        webbrowser.open("about:preferences#privacy")

    # Navigation buttons
    btn_frame = tk.Frame(root)
    btn_frame.pack(side="bottom", pady=10)

    def next_step():
        step = current_step["value"]
        if step == 0:
            step_cert()
        elif step == 1:
            step_firewall()
        elif step == 2:
            step_dns()

            # Add browser buttons
            browser_frame = tk.Frame(root)
            browser_frame.pack(pady=5)
            tk.Button(browser_frame, text="Открыть настройки Chrome",
                     command=open_chrome_security).pack(side="left", padx=5)
            tk.Button(browser_frame, text="Открыть настройки Firefox",
                     command=open_firefox_security).pack(side="left", padx=5)
        elif step == 3:
            mark_setup_complete()
            success["all"] = True
            root.destroy()
            return

        current_step["value"] += 1
        if current_step["value"] >= 4:
            next_btn.config(text="Готово")

    next_btn = tk.Button(btn_frame, text="Далее →", command=next_step, width=15)
    next_btn.pack(side="left", padx=5)

    skip_btn = tk.Button(btn_frame, text="Пропустить настройку",
                         command=lambda: (mark_setup_complete(), root.destroy()))
    skip_btn.pack(side="left", padx=5)

    # Start
    update_status(
        "Добро пожаловать в geo-fix!\n\n"
        "Эта программа подменяет сигналы геолокации в браузере, "
        "чтобы в связке с VPN обеспечить доступ к заблокированным "
        "сервисам Google (NotebookLM, Gemini).\n\n"
        "Нажмите «Далее» для начала настройки."
    )

    root.mainloop()
    return success.get("firewall", False)


def _run_console_wizard() -> bool:
    """Fallback console-based wizard for headless/no-display environments."""
    print("\n=== geo-fix: Первоначальная настройка ===\n")

    print("Шаг 1: Установка сертификата...")
    if install_ca_cert():
        print("  ✓ Сертификат установлен")
    else:
        print("  ⚠ Сертификат будет создан при первом запуске")

    print("\nШаг 2: Настройте «Безопасный DNS» в браузере:")
    print("  Chrome: chrome://settings/security → Безопасный DNS → Включить")
    print("  Firefox: about:preferences#privacy → DNS через HTTPS → Включить")

    mark_setup_complete()
    print("\n✓ Настройка завершена!\n")
    return True
