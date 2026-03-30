"""Unit tests for CA key/cert deletion functions (Task 1: security-hardening-r2)."""

from pathlib import Path

import pytest

from src.system_config import delete_ca_key_files, delete_ca_public_cert

# All CA files mitmproxy generates in a confdir
ALL_CA_FILES = [
    "mitmproxy-ca.pem",        # Private key (PEM)
    "mitmproxy-ca-cert.pem",   # Public cert (PEM)
    "mitmproxy-ca-cert.cer",   # Public cert (DER)
    "mitmproxy-ca.p12",        # PKCS12 bundle (private key + cert)
    "mitmproxy-dhparam.pem",   # DH parameters (not sensitive)
]

SENSITIVE_FILES = ["mitmproxy-ca.pem", "mitmproxy-ca-cert.cer", "mitmproxy-ca.p12"]


def _create_all_ca_files(confdir: Path) -> None:
    """Create dummy versions of all CA files in confdir."""
    for name in ALL_CA_FILES:
        (confdir / name).write_bytes(b"dummy-content")


class TestDeleteCaKeyFiles:
    """Tests for delete_ca_key_files(confdir)."""

    def test_delete_ca_key_files_removes_private_key(self, tmp_path):
        """All three sensitive files are deleted."""
        _create_all_ca_files(tmp_path)
        delete_ca_key_files(str(tmp_path))
        for name in SENSITIVE_FILES:
            assert not (tmp_path / name).exists(), f"{name} should be deleted"

    def test_delete_ca_key_files_keeps_public_cert(self, tmp_path):
        """Public cert mitmproxy-ca-cert.pem survives — needed by install_ca_cert."""
        _create_all_ca_files(tmp_path)
        delete_ca_key_files(str(tmp_path))
        assert (tmp_path / "mitmproxy-ca-cert.pem").exists()
        # dhparam also survives
        assert (tmp_path / "mitmproxy-dhparam.pem").exists()

    def test_delete_ca_key_files_idempotent(self, tmp_path):
        """Calling on an already-cleaned dir raises no exception."""
        # First call with files present
        _create_all_ca_files(tmp_path)
        delete_ca_key_files(str(tmp_path))
        # Second call — files already gone
        delete_ca_key_files(str(tmp_path))  # must not raise



class TestDeleteCaPublicCert:
    """Tests for delete_ca_public_cert(confdir)."""

    def test_delete_ca_public_cert_removes_cert(self, tmp_path):
        """Public cert is deleted."""
        (tmp_path / "mitmproxy-ca-cert.pem").write_bytes(b"cert")
        delete_ca_public_cert(str(tmp_path))
        assert not (tmp_path / "mitmproxy-ca-cert.pem").exists()

    def test_delete_ca_public_cert_idempotent(self, tmp_path):
        """Calling on empty dir raises no exception."""
        delete_ca_public_cert(str(tmp_path))  # must not raise
