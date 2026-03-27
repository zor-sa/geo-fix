"""System tray icon for geo-fix.

Shows active country, provides context menu for country switching and shutdown.
Thread-safe country switching synchronized with proxy addon.
"""

import logging
import threading
from typing import Callable, Optional

from PIL import Image, ImageDraw, ImageFont

from .presets import PRESETS, CountryPreset

logger = logging.getLogger("geo-fix.tray")

# Icon size
ICON_SIZE = 64


def _create_icon_image(text: str, bg_color: str = "#2196F3", text_color: str = "white") -> Image.Image:
    """Create a simple icon image with country code text."""
    img = Image.new("RGB", (ICON_SIZE, ICON_SIZE), bg_color)
    draw = ImageDraw.Draw(img)

    # Use default font, scaled to fit
    try:
        font = ImageFont.truetype("arial.ttf", 28)
    except (OSError, IOError):
        font = ImageFont.load_default()

    # Center text
    bbox = draw.textbbox((0, 0), text, font=font)
    text_w = bbox[2] - bbox[0]
    text_h = bbox[3] - bbox[1]
    x = (ICON_SIZE - text_w) // 2
    y = (ICON_SIZE - text_h) // 2
    draw.text((x, y), text, fill=text_color, font=font)

    return img


class GeoFixTray:
    """System tray icon manager."""

    def __init__(
        self,
        initial_preset: CountryPreset,
        on_switch_country: Callable[[str], None],
        on_stop: Callable[[], None],
    ):
        self._preset = initial_preset
        self._on_switch_country = on_switch_country
        self._on_stop = on_stop
        self._icon = None
        self._lock = threading.Lock()

    def _build_menu(self):
        """Build the context menu."""
        # Import here to avoid issues on systems without display
        import pystray

        items = []

        # Status line (disabled)
        with self._lock:
            status_text = f"Активна: {self._preset.name_ru} ({self._preset.code})"
        items.append(pystray.MenuItem(status_text, None, enabled=False))
        items.append(pystray.Menu.SEPARATOR)

        # Country submenu
        country_items = []
        for code, preset in sorted(PRESETS.items()):
            # Create a closure that captures the code
            def make_callback(c):
                return lambda: self._handle_switch(c)

            is_current = code == self._preset.code
            label = f"{'● ' if is_current else '  '}{preset.name_ru} ({code})"
            country_items.append(pystray.MenuItem(label, make_callback(code)))

        items.append(pystray.MenuItem("Переключить страну", pystray.Menu(*country_items)))
        items.append(pystray.Menu.SEPARATOR)

        # Stop
        items.append(pystray.MenuItem("Выключить", self._handle_stop))

        return pystray.Menu(*items)

    def _handle_switch(self, country_code: str) -> None:
        """Handle country switch from menu."""
        logger.info("Switching to %s via tray menu", country_code)
        with self._lock:
            self._preset = PRESETS[country_code]
        self._on_switch_country(country_code)
        self._update_icon()

    def _handle_stop(self) -> None:
        """Handle stop from menu."""
        logger.info("Stop requested via tray menu")
        self._on_stop()
        self.stop()

    def _update_icon(self) -> None:
        """Update the tray icon image and menu."""
        if self._icon is None:
            return
        with self._lock:
            code = self._preset.code
        self._icon.icon = _create_icon_image(code)
        self._icon.menu = self._build_menu()

    def start(self) -> None:
        """Start the tray icon. Blocks until stop() is called."""
        import pystray

        with self._lock:
            code = self._preset.code
            name = self._preset.name_ru

        self._icon = pystray.Icon(
            name="geo-fix",
            icon=_create_icon_image(code),
            title=f"geo-fix: {name} ({code})",
            menu=self._build_menu(),
        )
        self._icon.run()

    def start_threaded(self) -> threading.Thread:
        """Start the tray icon in a background thread."""
        thread = threading.Thread(target=self.start, daemon=True, name="tray-icon")
        thread.start()
        return thread

    def stop(self) -> None:
        """Stop and remove the tray icon."""
        if self._icon is not None:
            try:
                self._icon.stop()
            except Exception as e:
                logger.warning("Error stopping tray icon: %s", e)
            self._icon = None

    @property
    def current_preset(self) -> CountryPreset:
        with self._lock:
            return self._preset
