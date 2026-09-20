"""Token verification by asking the identity provider, with answers cached."""

import hashlib
import json
import time

import httpx
import pytest
from fake_cache import BrokenCache, MemoryCache
from fake_idp import AUDIENCE, ISSUER

from mcp_sql_server.auth.introspection import NEGATIVE_TTL_S, IntrospectionTokenVerifier
from mcp_sql_server.auth.token_verifier import JwtTokenVerifier, build_token_verifier
from mcp_sql_server.config import Settings

TOKEN = "opaque-token-value-12345"


class Provider:
    """A fake introspection endpoint that counts how often it is asked."""

    def __init__(self) -> None:
        self.calls = 0
        self.seen_auth: list[str | None] = []
        self.response: dict = self.active()
        self.status = 200
        self.body_override: str | None = None

    def active(self, **overrides) -> dict:
        return {
            "active": True,
            "sub": "u-1",
            "name": "Ana",
            "aud": AUDIENCE,
            "iss": ISSUER,
            "exp": int(time.time()) + 3600,
            "scope": "openid read",
            "client_id": "web",
            "realm_access": {"roles": ["analyst"]},
            **overrides,
        }

    def client(self) -> httpx.AsyncClient:
        def handle(request: httpx.Request) -> httpx.Response:
            self.calls += 1
            self.seen_auth.append(request.headers.get("authorization"))
            if self.body_override is not None:
                return httpx.Response(self.status, text=self.body_override)
            return httpx.Response(self.status, json=self.response)

        return httpx.AsyncClient(transport=httpx.MockTransport(handle))


@pytest.fixture
def provider() -> Provider:
    return Provider()


@pytest.fixture
def cache() -> MemoryCache:
    return MemoryCache()


def verifier(provider, cache, **overrides) -> IntrospectionTokenVerifier:
    return IntrospectionTokenVerifier(
        url="https://idp.test/introspect",
        client_id="mcp-server",
        client_secret="s3cret",
        audience=AUDIENCE,
        issuer=ISSUER,
        cache=cache,
        http_client=provider.client(),
        **overrides,
    )


async def test_an_active_token_is_accepted_with_its_identity(provider, cache):
    access = await verifier(provider, cache).verify_token(TOKEN)
    assert access is not None
    assert (access.subject, access.scopes, access.client_id) == ("u-1", ["openid", "read"], "web")
    assert access.claims["realm_access"]["roles"] == ["analyst"]


async def test_the_server_authenticates_itself_to_the_identity_provider(provider, cache):
    await verifier(provider, cache).verify_token(TOKEN)
    assert provider.seen_auth[0].startswith("Basic ")


async def test_repeated_checks_of_one_token_cost_one_call_to_the_provider(provider, cache):
    v = verifier(provider, cache)
    for _ in range(5):
        assert await v.verify_token(TOKEN) is not None
    assert provider.calls == 1


async def test_the_token_itself_is_never_written_to_the_cache(provider, cache):
    await verifier(provider, cache).verify_token(TOKEN)
    assert cache.data, "something should have been cached"
    for key, (value, _) in cache.data.items():
        assert TOKEN not in key and TOKEN not in value
    assert any(hashlib.sha256(TOKEN.encode()).hexdigest() in key for key in cache.data)


async def test_a_cached_answer_never_outlives_the_token(provider, cache):
    provider.response = provider.active(exp=int(time.time()) + 10)
    await verifier(provider, cache, cache_ttl_s=300).verify_token(TOKEN)
    ((_, expires_at),) = cache.data.values()
    assert expires_at <= 10  # the cache clock starts at 0: ttl was capped at the token's 10s


async def test_a_token_that_expires_while_cached_is_refused(provider, cache):
    v = verifier(provider, cache)
    await v.verify_token(TOKEN)
    ((key, (value, _)),) = cache.data.items()
    stale = json.loads(value) | {"exp": int(time.time()) - 5}
    cache.data[key] = (json.dumps(stale), 1e9)  # cached entry says expired
    assert await v.verify_token(TOKEN) is None


async def test_an_inactive_token_is_refused_and_remembered_only_briefly(provider, cache):
    provider.response = {"active": False}
    v = verifier(provider, cache)
    assert await v.verify_token(TOKEN) is None
    assert await v.verify_token(TOKEN) is None
    assert provider.calls == 1  # the negative answer is cached...
    cache.now = NEGATIVE_TTL_S + 1
    provider.response = provider.active()  # ...but only for a moment, so a fixed token works soon
    assert await v.verify_token(TOKEN) is not None


@pytest.mark.parametrize(
    "overrides",
    [
        {"aud": "https://some-other-api.test"},
        {"aud": ["account"]},
        {"iss": "https://evil.test"},
        {"exp": 1},
        {"sub": None},
    ],
    ids=["other-audience", "audience-list", "other-issuer", "expired", "no-subject"],
)
async def test_active_but_unsuitable_tokens_are_refused(provider, cache, overrides):
    provider.response = {k: v for k, v in provider.active(**overrides).items() if v is not None}
    assert await verifier(provider, cache).verify_token(TOKEN) is None


async def test_a_list_of_audiences_is_fine_if_it_includes_us(provider, cache):
    provider.response = provider.active(aud=["account", AUDIENCE])
    assert await verifier(provider, cache).verify_token(TOKEN) is not None


@pytest.mark.parametrize(
    ("status", "body"),
    [(500, "oops"), (200, "not json"), (200, "[]"), (401, '{"error": "invalid_client"}')],
)
async def test_provider_trouble_means_no_and_is_not_cached(provider, cache, status, body):
    provider.status, provider.body_override = status, body
    v = verifier(provider, cache)
    assert await v.verify_token(TOKEN) is None
    assert await v.verify_token(TOKEN) is None
    assert provider.calls == 2 and not cache.data


async def test_it_still_works_with_no_cache_at_all(provider):
    v = verifier(provider, BrokenCache())
    assert await v.verify_token(TOKEN) is not None
    assert await v.verify_token(TOKEN) is not None
    assert provider.calls == 2


# --- choosing a verifier from settings -------------------------------------------------------------


def settings(**kw) -> Settings:
    return Settings(
        _env_file=None,
        app_meta_url="postgresql+asyncpg://x",
        connection_secret_keys="k",
        public_url="https://mcp.test/mcp",
        oauth_issuer=ISSUER,
        **kw,
    )


def test_jwt_verification_is_the_default():
    assert isinstance(build_token_verifier(settings()), JwtTokenVerifier)


def test_introspection_can_be_selected():
    chosen = build_token_verifier(
        settings(
            token_verification="introspection",
            oauth_introspection_url="https://idp.test/introspect",
            oauth_introspection_client_id="mcp",
            oauth_introspection_client_secret="s",
        ),
        MemoryCache(),
    )
    assert isinstance(chosen, IntrospectionTokenVerifier)


def test_introspection_without_credentials_fails_at_startup():
    with pytest.raises(ValueError, match="INTROSPECTION"):
        build_token_verifier(settings(token_verification="introspection"))
