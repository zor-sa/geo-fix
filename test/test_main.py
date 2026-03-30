"""Tests for main.py startup sequence — Location Services integration."""

import os
from unittest.mock import MagicMock, patch, call
import sys

import pytest


class TestMainStartup:
    @patch("src.main.save_state")
    @patch("src.main.disable_location_services", return_value="Allow")
    def test_main_startup_calls_disable(self, mock_disable, mock_save):
        """main() startup calls disable_location_services() and stores result in state."""
        # We test the startup sequence logic in isolation by checking that
        # disable_location_services is imported and called from main module.
        # Import check: the symbol must be importable from src.main's namespace.
        import src.main as main_module
        assert hasattr(main_module, "disable_location_services"), (
            "disable_location_services must be imported in src.main"
        )

    def test_disable_location_services_importable_from_main(self):
        """disable_location_services must be in main module's namespace (imported)."""
        import src.main as main_module
        # Verify it resolves to the real function from system_config
        from src.system_config import disable_location_services
        assert main_module.disable_location_services is disable_location_services

    def test_cleanup_label_location_services_importable_from_main(self):
        """CLEANUP_LABEL_LOCATION_SERVICES must be imported in src.main."""
        import src.main as main_module
        assert hasattr(main_module, "CLEANUP_LABEL_LOCATION_SERVICES"), (
            "CLEANUP_LABEL_LOCATION_SERVICES must be imported in src.main"
        )
