"""Tests for CA key deletion after mitmproxy loads it (K-1)."""

import os
from pathlib import Path

import pytest

from src.system_config import delete_ca_key_files, delete_ca_public_cert


class TestDeleteCaKeyFiles:
    def test_deletes_private_key(self, tmp_path):
        (tmp_path / "mitmproxy-ca.pem").write_text("PRIVATE KEY")
        delete_ca_key_files(str(tmp_path))
        assert not (tmp_path / "mitmproxy-ca.pem").exists()

    def test_deletes_pkcs12(self, tmp_path):
        (tmp_path / "mitmproxy-ca.p12").write_bytes(b"\x00\x01\x02")
        delete_ca_key_files(str(tmp_path))
        assert not (tmp_path / "mitmproxy-ca.p12").exists()

    def test_deletes_der_cert(self, tmp_path):
        (tmp_path / "mitmproxy-ca-cert.cer").write_bytes(b"\x30\x82")
        delete_ca_key_files(str(tmp_path))
        assert not (tmp_path / "mitmproxy-ca-cert.cer").exists()

    def test_keeps_public_pem_cert(self, tmp_path):
        """Public cert must survive — needed by install_ca_cert."""
        (tmp_path / "mitmproxy-ca-cert.pem").write_text("PUBLIC CERT")
        (tmp_path / "mitmproxy-ca.pem").write_text("PRIVATE KEY")
        delete_ca_key_files(str(tmp_path))
        assert (tmp_path / "mitmproxy-ca-cert.pem").exists()

    def test_keeps_dhparam(self, tmp_path):
        (tmp_path / "mitmproxy-dhparam.pem").write_text("DH PARAMS")
        (tmp_path / "mitmproxy-ca.pem").write_text("PRIVATE KEY")
        delete_ca_key_files(str(tmp_path))
        assert (tmp_path / "mitmproxy-dhparam.pem").exists()

    def test_noop_when_no_sensitive_files(self, tmp_path):
        (tmp_path / "mitmproxy-ca-cert.pem").write_text("PUBLIC CERT")
        delete_ca_key_files(str(tmp_path))  # Should not raise
        assert (tmp_path / "mitmproxy-ca-cert.pem").exists()


class TestDeleteCaPublicCert:
    def test_deletes_public_cert(self, tmp_path):
        (tmp_path / "mitmproxy-ca-cert.pem").write_text("PUBLIC CERT")
        delete_ca_public_cert(str(tmp_path))
        assert not (tmp_path / "mitmproxy-ca-cert.pem").exists()

    def test_noop_when_not_exists(self, tmp_path):
        delete_ca_public_cert(str(tmp_path))  # Should not raise
