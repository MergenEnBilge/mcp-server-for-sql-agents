"""Checks OAuth access tokens presented to the HTTP transport.

This server is an OAuth 2.1 *resource server*: it never issues tokens, it only decides
whether the one it was handed is acceptable. Issuing is the identity provider's job
(Keycloak in our compose file), which is a separate service by design.

A token is accepted only if all of these hold:
  * its signature verifies against a key the identity provider publishes (JWKS),
  * it hasn't expired,
  * it was issued by the configured issuer, and
  * it was issued FOR THIS SERVER: the audience (`aud`, the RFC 8707 resource indicator)
    names us. Without this check, a token minted for some other service that trusts the same
    identity provider could be replayed here.

Anything else is rejected, and so is any failure to find out (identity provider
unreachable, malformed token): when in doubt, no.
"""

import asyncio
import logging
import time
from typing import Any

import httpx
import jwt
from mcp.server.auth.provider import AccessToken, TokenVerifier

from mcp_sql_server.config import Settings

logger = logging.getLogger(__name__)

# Asymmetric algorithms only. Never HS* (an attacker could sign with the public key) or "none".
ALLOWED_ALGORITHMS = ("RS256", "RS384", "RS512", "PS256", "PS384", "PS512", "ES256", "ES384")

# Don't let a stream of tokens with made-up key ids turn into a stream of requests to the
# identity provider: refetch the key set at most this often.
MIN_JWKS_REFETCH_INTERVAL_S = 30.0
JWKS_MAX_AGE_S = 600.0


class JwtTokenVerifier:
    """Verifies signed JWT access tokens locally, with no per-request call to the provider."""

    def __init__(
        self,
        *,
        issuer: str,
        audience: str,
        jwks_url: str | None = None,
        http_client: httpx.AsyncClient | None = None,
        leeway_s: int = 30,
    ) -> None:
        self._issuer = issuer
        self._audience = audience
        self._jwks_url = jwks_url
        self._http = http_client or httpx.AsyncClient(timeout=5.0)
        self._leeway_s = leeway_s
        self._keys: dict[str, jwt.PyJWK] = {}
        self._fetched_at = 0.0
        self._lock = asyncio.Lock()

    async def verify_token(self, token: str) -> AccessToken | None:
        try:
            header = jwt.get_unverified_header(token)
            key = await self._key_for(header.get("kid"))
            if key is None:
                return None
            claims = jwt.decode(
                token,
                key=key,
                algorithms=list(ALLOWED_ALGORITHMS),
                audience=self._audience,
                issuer=self._issuer,
                leeway=self._leeway_s,
                options={"require": ["exp", "iss", "aud", "sub"]},
            )
        except jwt.PyJWTError as exc:
            logger.info("access token rejected: %s", exc)
            return None
        except (httpx.HTTPError, ValueError, KeyError) as exc:
            logger.warning("could not verify access token: %s", exc)
            return None
        return access_token_from_claims(token, claims, self._audience)

    async def aclose(self) -> None:
        await self._http.aclose()

    # --- signing keys -----------------------------------------------------------------------

    async def _key_for(self, kid: str | None) -> jwt.PyJWK | None:
        if kid is None:
            return None
        stale = time.monotonic() - self._fetched_at > JWKS_MAX_AGE_S
        if kid in self._keys and not stale:
            return self._keys[kid]
        async with self._lock:
            # Another request may have refreshed the keys while we waited for the lock.
            if kid in self._keys and time.monotonic() - self._fetched_at <= JWKS_MAX_AGE_S:
                return self._keys[kid]
            recently = time.monotonic() - self._fetched_at < MIN_JWKS_REFETCH_INTERVAL_S
            if not recently or not self._keys:
                await self._refresh_keys()
            return self._keys.get(kid)

    async def _refresh_keys(self) -> None:
        url = self._jwks_url or await self._discover_jwks_url()
        response = await self._http.get(url)
        response.raise_for_status()
        keys: dict[str, jwt.PyJWK] = {}
        for jwk in response.json().get("keys", []):
            if jwk.get("use", "sig") == "sig" and "kid" in jwk:
                try:
                    keys[jwk["kid"]] = jwt.PyJWK.from_dict(jwk)
                except jwt.PyJWTError:
                    logger.warning("ignoring unusable signing key %s", jwk.get("kid"))
        self._keys = keys
        self._fetched_at = time.monotonic()

    async def _discover_jwks_url(self) -> str:
        """OpenID Connect discovery: the issuer says where its keys are."""
        response = await self._http.get(
            f"{self._issuer.rstrip('/')}/.well-known/openid-configuration"
        )
        response.raise_for_status()
        url = response.json()["jwks_uri"]
        assert isinstance(url, str)
        self._jwks_url = url
        return url


def access_token_from_claims(token: str, claims: dict[str, Any], audience: str) -> AccessToken:
    scope = claims.get("scope", "")
    scopes = scope.split() if isinstance(scope, str) else list(scope)
    return AccessToken(
        token=token,
        client_id=str(claims.get("azp") or claims.get("client_id") or ""),
        scopes=scopes,
        expires_at=int(claims["exp"]),
        resource=audience,
        subject=str(claims["sub"]),
        claims=claims,
    )


def build_token_verifier(settings: Settings) -> TokenVerifier:
    if not settings.oauth_issuer or not settings.public_url:
        raise ValueError("MCP_OAUTH_ISSUER and MCP_PUBLIC_URL are required to verify tokens.")
    return JwtTokenVerifier(
        issuer=settings.oauth_issuer,
        audience=settings.oauth_audience or settings.public_url,
        jwks_url=settings.oauth_jwks_url,
    )
