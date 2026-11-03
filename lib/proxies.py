"""Parse proxy endpoints separately from their credentials."""

from dataclasses import dataclass, field
from urllib.parse import unquote, urlsplit


@dataclass(frozen=True)
class Proxy:
    server: str
    auth: tuple[str, str] | None = field(default=None, repr=False)


def parse_proxy(value, default_auth=None):
    """Accept host:port or [scheme://][username:password@]host:port.

    Error messages intentionally exclude the supplied value, which may be secret.
    """
    value = value.strip()
    if not value or any(char.isspace() for char in value):
        raise ValueError("Expected [username:password@]host:port")
    try:
        parts = urlsplit(value if "://" in value else f"http://{value}")
        if parts.scheme not in {"http", "https", "socks4", "socks5"}:
            raise ValueError("Unsupported proxy protocol")
        if not parts.hostname or not parts.port or parts.path not in {"", "/"} or parts.query or parts.fragment:
            raise ValueError("Invalid endpoint")
        auth = default_auth
        if parts.username is not None:
            if not parts.username or parts.password is None:
                raise ValueError("Username and password must both be supplied")
            auth = (unquote(parts.username), unquote(parts.password))
        if auth and parts.scheme.startswith("socks"):
            raise ValueError("Use HTTP/HTTPS for authenticated proxies")
        host = parts.hostname
        if ":" in host:
            host = f"[{host}]"
        return Proxy(f"{parts.scheme}://{host}:{parts.port}", auth)
    except ValueError:
        raise ValueError(
            "Expected [http:// or https://][username:password@]host:port; "
            "SOCKS proxies must not require authentication"
        ) from None


def split_proxy_list(value):
    """Accept comma-separated entries or one entry per line; skip comment lines."""
    return [entry.strip() for line in value.splitlines()
            if not line.lstrip().startswith("#")
            for entry in line.split(",") if entry.strip()]
