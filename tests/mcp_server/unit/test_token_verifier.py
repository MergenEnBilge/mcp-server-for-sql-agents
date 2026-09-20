"""Access-token checks: what gets in, and, more importantly, what doesn't."""

import base64
import hashlib
import hmac
import json
import time

import jwt
import pytest
from cryptography.hazmat.primitives import serialization
from fake_idp import AUDIENCE, ISSUER, FakeIdP

from mcp_sql_server.auth import token_verifier as tv
from mcp_sql_server.auth.caller import caller_from_claims, claim_at_path
from mcp_sql_server.auth.token_verifier import JwtTokenVerifier


@pytest.fixture
def idp() -> FakeIdP:
    return FakeIdP()


@pytest.fixture
async def verifier(idp):
    v = JwtTokenVerifier(issuer=ISSUER, audience=AUDIENCE, http_client=idp.http_client())
    yield v
    await v.aclose()


# --- accepted -----------------------------------------------------------------------------


async def test_a_valid_token_is_accepted_and_its_claims_are_available(idp, verifier):
    access = await verifier.verify_token(idp.token(sub="u-42", scope="openid read"))
    assert access is not None
    assert access.subject == "u-42"
    assert access.scopes == ["openid", "read"]
    assert access.client_id == "test-client"
    assert access.resource == AUDIENCE
    assert access.claims["realm_access"]["roles"] == ["analyst"]


async def test_an_audience_list_is_fine_if_it_includes_us(idp, verifier):
    assert await verifier.verify_token(idp.token(aud=["account", AUDIENCE])) is not None


async def test_signing_keys_are_found_by_discovery_when_no_url_is_configured(idp, verifier):
    await verifier.verify_token(idp.token())
    assert idp.discovery_requests == 1
    assert idp.jwks_requests == 1


async def test_keys_are_fetched_once_and_reused(idp, verifier):
    for _ in range(5):
        assert await verifier.verify_token(idp.token()) is not None
    assert idp.jwks_requests == 1


# --- rejected ----------------------------------------------------------------------------------


async def test_an_expired_token_is_rejected(idp, verifier):
    assert await verifier.verify_token(idp.token(expires_in=-3600)) is None


async def test_a_token_minted_for_a_different_service_cannot_be_replayed_here(idp, verifier):
    """The whole point of checking the audience."""
    assert await verifier.verify_token(idp.token(aud="https://some-other-api.test")) is None
    assert await verifier.verify_token(idp.token(aud=["account", "billing"])) is None


async def test_a_token_with_no_audience_is_rejected(idp, verifier):
    assert await verifier.verify_token(idp.token(omit=("aud",))) is None


async def test_a_token_from_another_issuer_is_rejected(idp, verifier):
    assert await verifier.verify_token(idp.token(iss="https://evil.test/realms/shop")) is None


@pytest.mark.parametrize("claim", ["exp", "sub", "iss"])
async def test_tokens_missing_required_claims_are_rejected(idp, verifier, claim):
    assert await verifier.verify_token(idp.token(omit=(claim,))) is None


async def test_a_token_signed_with_someone_elses_key_is_rejected(idp, verifier):
    attacker = FakeIdP()  # a different key pair, same key id
    forged = attacker.token(kid="key-1")
    assert await verifier.verify_token(forged) is None


async def test_a_tampered_token_is_rejected(idp, verifier):
    header, payload, signature = idp.token(sub="user-1").split(".")
    claims = json.loads(base64.urlsafe_b64decode(payload + "=="))
    claims["sub"] = "admin"
    forged_payload = base64.urlsafe_b64encode(json.dumps(claims).encode()).rstrip(b"=").decode()
    assert await verifier.verify_token(f"{header}.{forged_payload}.{signature}") is None


async def test_unsigned_tokens_are_rejected(idp, verifier):
    unsigned = jwt.encode(
        {"sub": "u", "iss": ISSUER, "aud": AUDIENCE, "exp": int(time.time()) + 60},
        key=None,
        algorithm="none",
        headers={"kid": "key-1"},
    )
    assert await verifier.verify_token(unsigned) is None


async def test_a_symmetric_token_signed_with_the_public_key_is_rejected(idp, verifier):
    """The classic algorithm-confusion attack: sign with HS256 using the (public) RSA key."""
    public_pem = (
        idp.keys["key-1"]
        .public_key()
        .public_bytes(serialization.Encoding.PEM, serialization.PublicFormat.SubjectPublicKeyInfo)
    )

    # Built by hand, because pyjwt itself refuses to sign HS256 with a PEM key.
    def b64(data: bytes) -> str:
        return base64.urlsafe_b64encode(data).rstrip(b"=").decode()

    head = b64(json.dumps({"alg": "HS256", "typ": "JWT", "kid": "key-1"}).encode())
    body = b64(
        json.dumps(
            {"sub": "admin", "iss": ISSUER, "aud": AUDIENCE, "exp": int(time.time()) + 60}
        ).encode()
    )
    sig = b64(hmac.new(public_pem, f"{head}.{body}".encode(), hashlib.sha256).digest())
    assert await verifier.verify_token(f"{head}.{body}.{sig}") is None


@pytest.mark.parametrize("garbage", ["", "not-a-token", "a.b.c", "Bearer xyz", "x" * 5000])
async def test_garbage_is_rejected_without_raising(verifier, garbage):
    assert await verifier.verify_token(garbage) is None


async def test_a_token_without_a_key_id_is_rejected(idp, verifier):
    pem = idp.keys["key-1"].private_bytes(
        serialization.Encoding.PEM,
        serialization.PrivateFormat.PKCS8,
        serialization.NoEncryption(),
    )
    token = jwt.encode(
        {"sub": "u", "iss": ISSUER, "aud": AUDIENCE, "exp": int(time.time()) + 60},
        pem,
        algorithm="RS256",
    )
    assert await verifier.verify_token(token) is None


# --- signing-key handling --------------------------------------------------------------------


async def test_a_rotated_key_is_picked_up(idp, verifier, monkeypatch):
    await verifier.verify_token(idp.token())  # learns key-1
    idp.add_key("key-2")
    monkeypatch.setattr(tv, "MIN_JWKS_REFETCH_INTERVAL_S", 0)  # don't wait out the rate limit
    assert await verifier.verify_token(idp.token(kid="key-2")) is not None


async def test_a_flood_of_unknown_key_ids_does_not_flood_the_identity_provider(idp, verifier):
    await verifier.verify_token(idp.token())
    fetches_before = idp.jwks_requests
    for i in range(20):
        forged = idp.token(kid="key-1")  # valid shape...
        header = jwt.get_unverified_header(forged)
        header["kid"] = f"made-up-{i}"
        parts = forged.split(".")
        new_header = base64.urlsafe_b64encode(json.dumps(header).encode()).rstrip(b"=").decode()
        assert await verifier.verify_token(".".join([new_header, *parts[1:]])) is None
    assert idp.jwks_requests == fetches_before  # rate limited: no extra fetches


async def test_if_the_identity_provider_is_down_nothing_new_gets_in(idp, verifier):
    idp.down = True
    assert await verifier.verify_token(idp.token()) is None  # fails closed, doesn't raise


async def test_tokens_keep_working_from_cached_keys_during_an_outage(idp, verifier):
    assert await verifier.verify_token(idp.token()) is not None
    idp.down = True
    assert await verifier.verify_token(idp.token()) is not None


# --- turning claims into a caller -------------------------------------------------------------


def test_the_caller_is_built_from_the_subject_name_and_roles():
    caller = caller_from_claims(
        {"sub": "u-1", "name": "Ana", "realm_access": {"roles": ["analyst", "viewer"]}},
        "realm_access.roles",
    )
    assert (caller.sub, caller.name, caller.roles) == ("u-1", "Ana", {"analyst", "viewer"})


def test_the_display_name_falls_back_to_username_then_email():
    assert caller_from_claims({"sub": "u", "preferred_username": "ana"}, "roles").name == "ana"
    assert caller_from_claims({"sub": "u", "email": "a@x.test"}, "roles").name == "a@x.test"


def test_roles_may_come_from_any_claim_and_as_a_list_or_a_string():
    assert caller_from_claims({"sub": "u", "groups": ["a", "b"]}, "groups").roles == {"a", "b"}
    assert caller_from_claims({"sub": "u", "roles": "a, b c"}, "roles").roles == {"a", "b", "c"}
    assert caller_from_claims({"sub": "u"}, "realm_access.roles").roles == frozenset()


def test_claim_paths_are_followed_safely():
    claims = {"a": {"b": {"c": 1}}, "flat": 2}
    assert claim_at_path(claims, "a.b.c") == 1
    assert claim_at_path(claims, "flat") == 2
    assert claim_at_path(claims, "a.x.c") is None
    assert claim_at_path(claims, "flat.deeper") is None
