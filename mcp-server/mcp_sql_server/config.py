"""Runtime settings, read from environment variables (or a .env file in development)."""

from dataclasses import dataclass
from functools import lru_cache

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

    # Query governance. The hard maximum is enforced no matter what the caller asks for.
    default_query_timeout_s: float = Field(default=5.0, gt=0, le=60)
    default_row_limit: int = Field(default=500, ge=1)
    max_row_limit: int = Field(default=5000, ge=1)
    max_sample_rows: int = Field(default=100, ge=1)
    max_sql_length: int = Field(default=20_000, ge=1)

    # Longest text cell handed back to the LLM before it is cut off.
    max_cell_chars: int = Field(default=2000, ge=50)

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
