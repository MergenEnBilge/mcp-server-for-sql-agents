"""Runtime settings, read from environment variables (or a .env file in development)."""

from dataclasses import dataclass
from functools import lru_cache
from typing import Literal

from pydantic import Field, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict


@dataclass(frozen=True, slots=True)
class QueryLimits:
    """The governance limits the service layer enforces on every query."""

    timeout_s: float = 5.0
    default_rows: int = 500
    max_rows: int = 5000
    max_sample_rows: int = 100
    max_sql_length: int = 20_000
    max_cell_chars: int = 2000


def split_list(value: str) -> list[str]:
    """'a, b,,c' -> ['a', 'b', 'c']"""
    return [part.strip() for part in value.split(",") if part.strip()]


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="MCP_",
        env_file=(".env", "../.env"),
        extra="ignore",
    )

    # app_meta database, connected as the mcp_app role (read config, append audit rows).
    app_meta_url: SecretStr

    # Comma-separated Fernet keys. The first encrypts; all of them can decrypt, which
    # is what makes key rotation possible: add a new key at the front, re-save secrets.
    connection_secret_keys: SecretStr

    # Optional. Without Redis everything still works, just without caching.
    redis_url: SecretStr | None = None

    # How long cached schema lookups live. Permission lookups are cached for less, and both
    # are dropped immediately when an admin changes something in the GUI.
    cache_ttl_s: int = Field(default=300, ge=1)
    permission_cache_ttl_s: int = Field(default=60, ge=1)

    # Query governance. The hard maximum is enforced no matter what the caller asks for.
    default_query_timeout_s: float = Field(default=5.0, gt=0, le=60)
    default_row_limit: int = Field(default=500, ge=1)
    max_row_limit: int = Field(default=5000, ge=1)
    max_sample_rows: int = Field(default=100, ge=1)
    max_sql_length: int = Field(default=20_000, ge=1)

    # Longest text cell handed back to the LLM before it is cut off.
    max_cell_chars: int = Field(default=2000, ge=50)

    # stdio transport (local development, e.g. Claude Desktop). stdio has no login, so the
    # identity is configured here. There's deliberately no default: without MCP_STDIO_SUB
    # the stdio transport refuses to start.
    stdio_sub: str | None = None
    stdio_name: str | None = None
    stdio_roles: str = ""  # comma-separated

    # Streamable HTTP transport and OAuth 2.1.
    http_host: str = "127.0.0.1"
    http_port: int = Field(default=8000, ge=1, le=65535)

    # The address clients use to reach this server, e.g. https://mcp.example.com/mcp.
    # It is this server's resource identifier (RFC 8707): tokens must be issued for it.
    public_url: str | None = None
    oauth_issuer: str | None = None
    oauth_jwks_url: str | None = None  # defaults to <issuer>/protocol/openid-connect/certs
    oauth_audience: str | None = None  # defaults to public_url
    oauth_roles_claim: str = "realm_access.roles"  # dotted path into the token's claims
    oauth_required_scopes: str = ""  # comma-separated

    # Browsers can be tricked into talking to a server on the caller's own network (DNS
    # rebinding), so the transport checks the Host and Origin headers of every request. The
    # server's own address (public_url) is always allowed; these add to it, comma-separated.
    # Origins are only needed for MCP clients that run inside a web page.
    allowed_hosts: str = ""
    allowed_origins: str = ""

    # Fair use, per signed-in caller: this many tool calls a minute, and this many running at
    # the same moment. 0 turns a limit off.
    rate_limit_per_minute: int = Field(default=120, ge=0)
    max_concurrent_calls: int = Field(default=4, ge=0)

    # SQLite connections may only point at files inside this folder. Without it, SQLite
    # connections are refused: a database path typed into the admin console must never be
    # able to reach an arbitrary file on the server.
    sqlite_root: str | None = None

    # "jwt" checks signed tokens locally. "introspection" asks the identity provider
    # (RFC 7662) and caches the answer in Redis, for providers that issue opaque tokens.
    token_verification: Literal["jwt", "introspection"] = "jwt"
    oauth_introspection_url: str | None = None
    oauth_introspection_client_id: str | None = None
    oauth_introspection_client_secret: SecretStr | None = None
    token_cache_ttl_s: int = Field(default=60, ge=1)

    def query_limits(self) -> QueryLimits:
        return QueryLimits(
            timeout_s=self.default_query_timeout_s,
            default_rows=self.default_row_limit,
            max_rows=self.max_row_limit,
            max_sample_rows=self.max_sample_rows,
            max_sql_length=self.max_sql_length,
            max_cell_chars=self.max_cell_chars,
        )


@lru_cache
def get_settings() -> Settings:
    return Settings()  # values come from the environment
