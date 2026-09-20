"""Settings for the admin GUI backend, read from GUI_-prefixed environment variables."""

from functools import lru_cache

from pydantic import Field, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="GUI_",
        env_file=(".env", "../.env"),
        extra="ignore",
    )

    # app_meta database, connected as the gui_app role (manage config, read the audit log).
    app_meta_url: SecretStr

    # The same Fernet keys the MCP server uses: this service encrypts connection secrets
    # and the MCP server decrypts them.
    connection_secret_keys: SecretStr

    # Optional. Without Redis the GUI still works, but the MCP server's caches only expire
    # on their TTL instead of being invalidated the moment an admin changes something.
    redis_url: SecretStr | None = None

    # Sign-in: the same identity provider as the MCP server, so a person's roles are the same
    # in both places. Tokens must be issued for this API (`oauth_audience`).
    oauth_issuer: str
    oauth_audience: str
    oauth_jwks_url: str | None = None
    oauth_roles_claim: str = "realm_access.roles"

    # Holders of this role (or a scope of the same name) may use the admin endpoints.
    admin_role: str = "admin"

    # Browser origins allowed to call the API cross-site. Only needed in development, where the
    # UI runs on its own port; in production the UI and API share one origin.
    cors_origins: str = ""

    max_page_size: int = Field(default=200, ge=1)
    schema_cache_ttl_s: int = Field(default=300, ge=1)
    connection_test_timeout_s: float = Field(default=8.0, gt=0, le=60)


@lru_cache
def get_settings() -> Settings:
    return Settings()  # values come from the environment
