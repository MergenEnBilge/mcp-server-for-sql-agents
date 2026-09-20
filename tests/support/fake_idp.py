"""A stand-in identity provider for tests: it holds signing keys, publishes them as JWKS,
and mints tokens (good ones and deliberately bad ones). No network involved; the JWKS and
discovery endpoints are served through httpx's mock transport."""

import json
import time
from typing import Any

import httpx
import jwt
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from jwt.algorithms import RSAAlgorithm

ISSUER = "https://idp.test/realms/shop"
AUDIENCE = "https://mcp.test/mcp"


class FakeIdP:
    def __init__(self) -> None:
        self.keys: dict[str, rsa.RSAPrivateKey] = {}
        self.jwks_requests = 0
        self.discovery_requests = 0
        self.down = False  # simulate an outage
        self.add_key("key-1")

    def add_key(self, kid: str) -> rsa.RSAPrivateKey:
        key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
        self.keys[kid] = key
        return key

    def remove_key(self, kid: str) -> None:
        del self.keys[kid]

    def jwks(self) -> dict[str, Any]:
        keys = []
        for kid, private in self.keys.items():
            jwk = json.loads(RSAAlgorithm.to_jwk(private.public_key()))
            keys.append({**jwk, "kid": kid, "use": "sig", "alg": "RS256"})
        return {"keys": keys}

    def token(
        self,
        *,
        sub: str = "user-1",
        name: str | None = "Ana Analyst",
        roles: tuple[str, ...] = ("analyst",),
        aud: str | list[str] | None = AUDIENCE,
        iss: str = ISSUER,
        expires_in: int = 3600,
        kid: str = "key-1",
        key: rsa.RSAPrivateKey | None = None,
        scope: str = "openid profile",
        extra: dict[str, Any] | None = None,
        omit: tuple[str, ...] = (),
    ) -> str:
        now = int(time.time())
        claims: dict[str, Any] = {
            "sub": sub,
            "name": name,
            "iss": iss,
            "aud": aud,
            "iat": now,
            "exp": now + expires_in,
            "scope": scope,
            "azp": "test-client",
            "realm_access": {"roles": list(roles)},
            **(extra or {}),
        }
        for claim in omit:
            claims.pop(claim, None)
        signing_key = key or self.keys[kid]
        pem = signing_key.private_bytes(
            serialization.Encoding.PEM,
            serialization.PrivateFormat.PKCS8,
            serialization.NoEncryption(),
        )
        return jwt.encode(claims, pem, algorithm="RS256", headers={"kid": kid})

    def transport(self) -> httpx.MockTransport:
        def handle(request: httpx.Request) -> httpx.Response:
            if self.down:
                raise httpx.ConnectError("identity provider is down", request=request)
            if request.url.path.endswith("/.well-known/openid-configuration"):
                self.discovery_requests += 1
                return httpx.Response(
                    200, json={"issuer": ISSUER, "jwks_uri": "https://idp.test/jwks"}
                )
            if request.url.path == "/jwks":
                self.jwks_requests += 1
                return httpx.Response(200, json=self.jwks())
            return httpx.Response(404)

        return httpx.MockTransport(handle)

    def http_client(self) -> httpx.AsyncClient:
        return httpx.AsyncClient(transport=self.transport())
