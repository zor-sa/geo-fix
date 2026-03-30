"""Tests for main.py startup sequence — Location Services integration."""

from unittest.mock import patch, MagicMock
import pytest


class TestMainStartup:
    def test_disable_location_services_importable_from_main(self):
        """disable_location_services must be in main module's namespace."""
        import src.main as main_module
        from src.system_config import disable_location_services
        assert main_module.disable_location_services is disable_location_services

    def test_restore_location_services_importable_from_main(self):
        """restore_location_services must be in main module's namespace."""
        import src.main as main_module
        from src.system_config import restore_location_services
        assert main_module.restore_location_services is restore_location_services


class TestLocationServicesStartupWiring:
    """Verify that main() calls disable_location_services and stores result.

    main() is too complex to invoke directly in unit tests (requires mitmproxy,
    tray, signal handlers). Instead, we inspect the source code to verify the
    call chain is wired correctly. This is a structural test.
    """

    def test_main_calls_disable_and_stores_result(self):
        """Source of main() contains disable_location_services call and state assignment."""
        import inspect
        import src.main as main_module
        source = inspect.getsource(main_module.main)
        # Verify the call exists
        assert "disable_location_services()" in source, (
            "main() must call disable_location_services()"
        )
        # Verify the result is stored in state
        assert "state.original_location_services" in source, (
            "main() must store result in state.original_location_services"
        )
        # Verify save_state is called after the assignment
        # Find positions to verify ordering
        disable_pos = source.index("disable_location_services()")
        store_pos = source.index("state.original_location_services")
        save_pos = source.index("save_state(state)", store_pos)
        assert disable_pos < store_pos < save_pos, (
            "main() must call disable → store in state → save_state in order"
        )
