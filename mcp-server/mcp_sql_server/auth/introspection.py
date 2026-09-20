"""Token verification by asking the identity provider (RFC 7662 introspection).

Use this instead of local JWT checking when the provider issues opaque tokens, or when you want
revocation to take effect at the provider's own pace rather than at token expiry.

Every check would otherwise cost a round trip to the provider, so answers are cached in Redis
for a short time. Two details matter for safety:

  * The cache key is a hash of the token. The token itself is never written to Redis.
  * A cached "active" answer is never trusted past the token's own expiry, and a failed
    lookup (provider down, garbage response) is never cached and never treated as valid.
"""

import hashlib
import json
import logging
import time
from typing import Any

import httpx
from mcp.server.auth.provider import AccessToken

from mcp_sql_server.auth.token_verifier import access_token_from_claims
from mcp_sql_server.cache.base import Cache

logger = logging.getLogger(__name__)

# Inactive tokens are remembered briefly so a client hammering us with a dead token doesn't
# turn into a hammering of the identity provider.
NEGATIVE_TTL_S = 5


class IntrospectionTokenVerifier:
    def __init__(
        self,
        *,
        url: str,
        client_id: str,
        client_secret: str,
        audience: str,
        cache: Cache,
        issuer: str | None = None,
        cache_ttl_s: int = 60,
        http_client: httpx.AsyncClient | None = None,
    ) -> None:
        self._url = url
        self._auth = (client_id, client_secret)
        self._audience = audience
        self._issuer = issuer
        self._cache = cache
        self._cache_ttl_s = cache_ttl_s
        self._http = http_client or httpx.AsyncClient(timeout=5.0)

    async def verify_token(self, token: str) -> AccessToken | None:
        key = "token:" + hashlib.sha256(token.encode()).hexdigest()
        claims = await self._from_cache(key)
        if claims is None:
            claims = await self._introspect(token, key)
        if claims is None or not self._acceptable(claims):
            return None
        return access_token_from_claims(token, claims, self._audience)

    async def aclose(self) -> None:
        await self._http.aclose()

    async def _from_cache(self, key: str) -> dict[str, Any] | None:
        hit = await self._cache.get(key)
        if hit is None:
            return None
        try:
            claims = json.loads(hit)
        except ValueError:
            return None
        return claims if isinstance(claims, dict) else None

    async def _introspect(self, token: str, key: str) -> dict[str, Any] | None:
        try:
            response = await self._http.post(
                self._url, data={"token": token, "token_type_hint": "access_token"}, auth=self._auth
            )
            response.raise_for_status()
            claims = response.json()
        except (httpx.HTTPError, ValueError) as exc:
            logger.warning("token introspection failed: %s", exc)
            return None
        if not isinstance(claims, dict):
            return None

        if not claims.get("active"):
            await self._cache.set(key, json.dumps({"active": False}), NEGATIVE_TTL_S)
            return claims
        ttl = self._cache_ttl_s
        if isinstance(claims.get("exp"), int):
            ttl = min(ttl, claims["exp"] - int(time.time()))
        if ttl > 0:
            await self._cache.set(key, json.dumps(claims), ttl)
        return claims

    def _acceptable(self, claims: dict[str, Any]) -> bool:
        if claims.get("active") is not True or "sub" not in claims:
            return False
        exp = claims.get("exp")
        if not isinstance(exp, int) or exp <= int(time.time()):
            return False
        audiences = claims.get("aud", [])
        if isinstance(audiences, str):
            audiences = [audiences]
        if self._audience not in audiences:
            return False
        return self._issuer is None or claims.get("iss") == self._issuer
