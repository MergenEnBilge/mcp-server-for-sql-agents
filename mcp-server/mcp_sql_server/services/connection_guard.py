"""Limits on where a registered database may point.

Anyone who can edit connections in the admin console can make the server open a network
connection or a file of their choosing. That's the point of the feature, but it shouldn't reach
further than the databases it is for:

  * a SQLite connection must name a file inside one configured folder, so it can't be used to
    read some other application's database off the server's disk;
  * a network connection may not point at link-local or "any" addresses, which is where cloud
    metadata services (169.254.169.254) and similar internal endpoints live.

Both checks run when an administrator saves a connection (so they get a clear message) and again
when the server opens it (so a bad row put into the database some other way is still refused).
"""

import ipaddress
from pathlib import Path

_METADATA_NAMES = {"metadata", "metadata.google.internal", "instance-data"}


def resolve_sqlite_path(path: str, root: str | None) -> Path:
    """The real location of a SQLite file, or a ValueError saying why it isn't allowed."""
    if not root:
        raise ValueError(
            "SQLite connections are switched off: the server has no SQLite folder configured "
            "(MCP_SQLITE_ROOT)."
        )
    if path.lower().startswith("file:") or "?" in path or "\x00" in path:
        raise ValueError("Give the SQLite database as a plain file path.")
    base = Path(root).resolve()
    candidate = Path(path)
    resolved = (candidate if candidate.is_absolute() else base / candidate).resolve()
    # resolve() follows links, so a symlink that leads out of the folder is caught here too.
    if not resolved.is_relative_to(base):
        raise ValueError(f"The SQLite file must be inside {base}.")
    return resolved


def check_host(host: str) -> None:
    """Refuse addresses that are never a database: link-local, unspecified, metadata services."""
    name = host.strip().strip("[]").lower().rstrip(".")
    if name in _METADATA_NAMES:
        raise ValueError(f"'{host}' is a cloud metadata address, not a database.")
    try:
        address = ipaddress.ip_address(name)
    except ValueError:
        return  # a host name; only literal addresses are checked
    if address.is_link_local or address.is_unspecified or address.is_multicast:
        raise ValueError(f"'{host}' is not an address a database can be reached at.")
