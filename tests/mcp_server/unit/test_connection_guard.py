"""Where a registered database may point: which files, which network addresses."""

import os

import pytest

from mcp_sql_server.services.connection_guard import check_host, resolve_sqlite_path


def test_sqlite_is_refused_when_no_folder_is_configured(tmp_path):
    with pytest.raises(ValueError, match="switched off"):
        resolve_sqlite_path(str(tmp_path / "shop.sqlite"), None)


def test_a_file_inside_the_folder_is_accepted(tmp_path):
    assert resolve_sqlite_path("shop.sqlite", str(tmp_path)) == (tmp_path / "shop.sqlite").resolve()
    assert resolve_sqlite_path(str(tmp_path / "sub" / "a.db"), str(tmp_path)).name == "a.db"


@pytest.mark.parametrize(
    "path",
    ["../outside.sqlite", "sub/../../outside.sqlite", "/etc/passwd", "C:/Windows/system.ini"],
)
def test_a_file_outside_the_folder_is_refused(tmp_path, path):
    with pytest.raises(ValueError, match="must be inside"):
        resolve_sqlite_path(path, str(tmp_path))


@pytest.mark.parametrize(
    "path", ["file:/data/x.sqlite", "FILE:x.sqlite?mode=rwc", "shop.sqlite?mode=rwc", "a\x00b"]
)
def test_uris_and_query_strings_are_refused(tmp_path, path):
    with pytest.raises(ValueError, match="plain file path"):
        resolve_sqlite_path(path, str(tmp_path))


@pytest.mark.skipif(os.name == "nt", reason="creating symlinks needs elevated rights on Windows")
def test_a_symlink_that_leads_out_of_the_folder_is_refused(tmp_path):
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "secret.sqlite").write_bytes(b"")
    root = tmp_path / "root"
    root.mkdir()
    (root / "link.sqlite").symlink_to(outside / "secret.sqlite")
    with pytest.raises(ValueError, match="must be inside"):
        resolve_sqlite_path("link.sqlite", str(root))


@pytest.mark.parametrize(
    "host",
    ["169.254.169.254", "169.254.0.1", "fe80::1", "0.0.0.0", "::", "224.0.0.1", "[fe80::1]"]
    + ["metadata.google.internal", "METADATA", "instance-data."],
)
def test_addresses_that_are_never_a_database_are_refused(host):
    with pytest.raises(ValueError):
        check_host(host)


@pytest.mark.parametrize(
    "host", ["db.internal", "localhost", "127.0.0.1", "10.0.3.7", "192.168.1.20", "postgres", "::1"]
)
def test_ordinary_database_hosts_are_accepted(host):
    check_host(host)
