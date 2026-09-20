import pytest
from cryptography.fernet import Fernet
from pydantic import ValidationError

from mcp_sql_server.config import QueryLimits, Settings


@pytest.fixture
def env(monkeypatch):
    monkeypatch.chdir("/")  # so a stray .env file in the working directory can't leak in
    monkeypatch.setenv("MCP_APP_META_URL", "postgresql+asyncpg://mcp_app:pw@localhost/app_meta")
    monkeypatch.setenv("MCP_CONNECTION_SECRET_KEYS", Fernet.generate_key().decode())


def test_the_documented_governance_defaults(env):
    limits = Settings().query_limits()
    assert limits == QueryLimits(
        timeout_s=5.0,
        default_rows=500,
        max_rows=5000,
        max_sample_rows=100,
        max_sql_length=20_000,
        max_cell_chars=2000,
    )


def test_limits_can_be_tuned_from_the_environment(env, monkeypatch):
    monkeypatch.setenv("MCP_DEFAULT_QUERY_TIMEOUT_S", "2.5")
    monkeypatch.setenv("MCP_MAX_ROW_LIMIT", "1000")
    limits = Settings().query_limits()
    assert (limits.timeout_s, limits.max_rows) == (2.5, 1000)


@pytest.mark.parametrize("bad_timeout", ["0", "-1", "61"])
def test_a_nonsensical_timeout_is_refused_at_startup(env, monkeypatch, bad_timeout):
    monkeypatch.setenv("MCP_DEFAULT_QUERY_TIMEOUT_S", bad_timeout)
    with pytest.raises(ValidationError):
        Settings()


def test_the_database_url_and_keys_are_required(monkeypatch):
    monkeypatch.chdir("/")
    monkeypatch.delenv("MCP_APP_META_URL", raising=False)
    monkeypatch.delenv("MCP_CONNECTION_SECRET_KEYS", raising=False)
    with pytest.raises(ValidationError):
        Settings()


def test_secrets_do_not_show_up_when_settings_are_printed_or_logged(env):
    rendered = repr(Settings()) + str(Settings())
    assert ":pw@" not in rendered
    assert "Fernet" not in rendered


def test_the_http_transport_refuses_to_start_without_oauth_settings(env):
    from mcp_sql_server.server import build_http_auth

    with pytest.raises(ValueError, match="MCP_PUBLIC_URL"):
        build_http_auth(Settings())  # no public URL or issuer configured


def test_oauth_scopes_are_a_comma_separated_list(env, monkeypatch):
    from mcp_sql_server.config import split_list

    assert split_list("mcp:query, mcp:admin,,") == ["mcp:query", "mcp:admin"]
    assert split_list("") == []
