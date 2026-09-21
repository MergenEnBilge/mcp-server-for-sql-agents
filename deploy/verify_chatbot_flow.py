"""Connects to the MCP server the way an online chatbot does, and reports what happened.

Chatbots don't have a client set up for them in advance. They find the server's sign-in details
on their own, register themselves as an OAuth client, send the person to sign in (with PKCE),
and then talk MCP with the token they get back. This script walks through exactly that against a
running deployment, using a real sign-in, so it proves the whole chain (discovery documents,
self-registration, the sign-in and consent pages, token audience, the MCP endpoint) rather than
any one part of it.

    python deploy/verify_chatbot_flow.py https://your-host/mcp bob 'the-users-password'

It registers a throwaway client and removes it again. Run it with a test account. Nothing is
changed in the databases; the agent it registers shows up as waiting for approval, and stays that
way unless you approve it in the admin console.

Needs only `httpx`.
"""

import base64
import hashlib
import html
import json
import re
import secrets
import sys
from dataclasses import dataclass, field
from typing import Any
from urllib.parse import parse_qs, urlencode, urljoin, urlsplit

import httpx

# The address a well-known chatbot sends people back to. Nothing listens there: the script
# reads the sign-in result out of the redirect instead of following it.
DEFAULT_REDIRECT = "https://claude.ai/api/mcp/auth_callback"


class FlowError(Exception):
    """A step failed. The message says which one and what the server answered."""


@dataclass
class Outcome:
    mcp_url: str
    issuer: str
    metadata_found_at: str
    client_id: str
    consent_screen_shown: bool
    token_claims: dict[str, Any]
    server_instructions: str
    my_access: dict[str, Any]
    steps: list[str] = field(default_factory=list)
    access_token: str = ""
    _cleanup: Any = None

    def close(self) -> None:
        """Remove the throwaway client that was registered."""
        if self._cleanup:
            self._cleanup()
            self._cleanup = None


def decode_claims(token: str) -> dict[str, Any]:
    payload = token.split(".")[1]
    return json.loads(base64.urlsafe_b64decode(payload + "=" * (-len(payload) % 4)))


def pkce_pair() -> tuple[str, str]:
    verifier = secrets.token_urlsafe(48)
    digest = hashlib.sha256(verifier.encode()).digest()
    return verifier, base64.urlsafe_b64encode(digest).rstrip(b"=").decode()


# --- discovery ------------------------------------------------------------------------------------


def discover(http: httpx.Client, mcp_url: str) -> tuple[dict[str, Any], str, dict[str, Any], str]:
    """(protected resource metadata, issuer, authorization server metadata, where it was found),
    following the MCP authorization spec: the 401 challenge, then RFC 9728, then RFC 8414."""
    challenge = http.post(
        mcp_url,
        json={"jsonrpc": "2.0", "id": 1, "method": "tools/list"},
        headers={"Accept": "application/json, text/event-stream"},
    )
    if challenge.status_code != 401 or "resource_metadata=" not in challenge.headers.get(
        "www-authenticate", ""
    ):
        raise FlowError(
            "An unauthenticated request should get 401 with a WWW-Authenticate header "
            "pointing at the resource metadata; got "
            f"{challenge.status_code}: {challenge.headers.get('www-authenticate')!r}"
        )
    match = re.search(r'resource_metadata="([^"]+)"', challenge.headers["www-authenticate"])
    resource_doc = http.get(match.group(1) if match else "")
    if resource_doc.status_code != 200:
        raise FlowError(
            f"The protected resource metadata at {match and match.group(1)} "
            f"answered {resource_doc.status_code}"
        )
    resource = resource_doc.json()
    if resource.get("resource") != mcp_url:
        raise FlowError(
            f"The metadata says the resource is {resource.get('resource')!r}, not {mcp_url!r}"
        )
    issuer = resource["authorization_servers"][0]

    parts = urlsplit(issuer)
    root, path = f"{parts.scheme}://{parts.netloc}", parts.path.rstrip("/")
    candidates = [
        f"{root}/.well-known/oauth-authorization-server{path}",  # RFC 8414
        f"{root}/.well-known/openid-configuration{path}",  # OpenID Connect, path inserted
        f"{issuer.rstrip('/')}/.well-known/openid-configuration",  # OpenID Connect, path appended
    ]
    for url in candidates:
        answer = http.get(url)
        if answer.status_code == 200:
            return resource, issuer, answer.json(), url
    raise FlowError("No authorization server metadata at any of: " + ", ".join(candidates))


# --- registering, signing in ----------------------------------------------------------------------


def register(
    http: httpx.Client, metadata: dict[str, Any], redirect_uris: list[str], name: str
) -> httpx.Response:
    endpoint = metadata.get("registration_endpoint")
    if not endpoint:
        raise FlowError(
            "The authorization server publishes no registration_endpoint, "
            "so chatbots can't register themselves."
        )
    return http.post(
        endpoint,
        json={
            "client_name": name,
            "redirect_uris": redirect_uris,
            "grant_types": ["authorization_code", "refresh_token"],
            "response_types": ["code"],
            "token_endpoint_auth_method": "none",
        },
    )


def _form_action(page: str, form_id_hint: str) -> str | None:
    for tag in re.findall(r"<form\b[^>]*>", page):
        if form_id_hint in tag:
            action = re.search(r'action="([^"]+)"', tag)
            if action:
                return html.unescape(action.group(1))
    return None


def sign_in(
    http: httpx.Client,
    authorization_url: str,
    username: str,
    password: str,
    redirect_uri: str,
) -> tuple[dict[str, list[str]], bool]:
    """Sign in on the identity provider's own pages. Returns the redirect's query and whether a
    consent screen had to be accepted on the way."""
    consent = False
    page = http.get(authorization_url)
    for _ in range(6):
        if page.status_code in (301, 302, 303, 307):
            location = urljoin(str(page.url), page.headers["location"])
            if location.startswith(redirect_uri):
                return parse_qs(urlsplit(location).query), consent
            page = http.get(location)
            continue
        text = page.text
        if page.status_code != 200:
            raise FlowError(f"The sign-in page answered {page.status_code}: {text[:200]!r}")
        if login := _form_action(text, "kc-form-login"):
            page = http.post(
                urljoin(str(page.url), login),
                data={"username": username, "password": password, "credentialId": ""},
            )
            if "Invalid username or password" in page.text:
                raise FlowError("The identity provider says the username or password is wrong.")
        elif consent_action := _form_action(text, "login-actions/consent"):
            consent = True
            hidden = dict(
                re.findall(r'<input[^>]+type="hidden"[^>]+name="([^"]+)"[^>]+value="([^"]*)"', text)
            )
            page = http.post(
                urljoin(str(page.url), consent_action), data={**hidden, "accept": "Yes"}
            )
        else:
            plain = re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", text)).strip()
            raise FlowError(f"Don't know what this sign-in page wants: {plain[-300:]!r}")
    raise FlowError("Signing in went round in circles.")


def authorization_url(
    metadata: dict[str, Any], client_id: str, redirect_uri: str, mcp_url: str, extra: dict[str, str]
) -> str:
    query = {
        "response_type": "code",
        "client_id": client_id,
        "redirect_uri": redirect_uri,
        "state": secrets.token_urlsafe(12),
        "scope": "openid",
        "resource": mcp_url,  # RFC 8707: say which server the token is for
        **extra,
    }
    return f"{metadata['authorization_endpoint']}?{urlencode(query)}"


# --- talking MCP ---------------------------------------------------------------------------


def mcp(
    http: httpx.Client, mcp_url: str, token: str, method: str, params: dict[str, Any], id: int
) -> dict[str, Any]:
    response = http.post(
        mcp_url,
        json={"jsonrpc": "2.0", "id": id, "method": method, "params": params},
        headers={
            "Authorization": f"Bearer {token}",
            "Accept": "application/json, text/event-stream",
            "Mcp-Protocol-Version": "2025-06-18",
        },
    )
    if response.status_code != 200:
        raise FlowError(f"{method} answered {response.status_code}: {response.text[:300]}")
    return response.json()


def call_tool(
    http: httpx.Client, mcp_url: str, token: str, name: str, arguments: dict[str, Any] | None = None
) -> dict[str, Any]:
    answer = mcp(
        http, mcp_url, token, "tools/call", {"name": name, "arguments": arguments or {}}, id=3
    )
    return answer["result"]


# --- the whole thing -----------------------------------------------------------------------


def connect_like_a_chatbot(
    mcp_url: str,
    username: str,
    password: str,
    *,
    redirect_uri: str = DEFAULT_REDIRECT,
    client_name: str = "Chatbot simulator",
    verify_tls: bool = False,
) -> Outcome:
    """Do everything a chatbot does, and return what it saw. Call `.close()` when finished."""
    steps: list[str] = []
    # No redirects are followed automatically: the sign-in result is read out of the last one.
    http = httpx.Client(verify=verify_tls, follow_redirects=False, timeout=30)

    resource, issuer, metadata, found_at = discover(http, mcp_url)
    steps.append(f"found the sign-in details ({found_at})")
    if "S256" not in metadata.get("code_challenge_methods_supported", []):
        raise FlowError("The authorization server doesn't offer PKCE with S256.")

    registered = register(http, metadata, [redirect_uri], client_name)
    if registered.status_code not in (200, 201):
        raise FlowError(
            f"Registering as a client was refused ({registered.status_code}): "
            f"{registered.text[:300]}"
        )
    client = registered.json()
    client_id = client["client_id"]
    steps.append(f"registered itself as client {client_id}")

    def cleanup() -> None:
        if client.get("registration_client_uri") and client.get("registration_access_token"):
            http.delete(
                client["registration_client_uri"],
                headers={"Authorization": f"Bearer {client['registration_access_token']}"},
            )
        http.close()

    try:
        verifier, challenge = pkce_pair()
        url = authorization_url(
            metadata,
            client_id,
            redirect_uri,
            mcp_url,
            {"code_challenge": challenge, "code_challenge_method": "S256"},
        )
        query, consent = sign_in(http, url, username, password, redirect_uri)
        if "code" not in query:
            raise FlowError(f"Signing in didn't give an authorization code: {query}")
        steps.append("signed in" + (" and accepted the consent screen" if consent else ""))

        tokens = http.post(
            metadata["token_endpoint"],
            data={
                "grant_type": "authorization_code",
                "code": query["code"][0],
                "redirect_uri": redirect_uri,
                "client_id": client_id,
                "code_verifier": verifier,
                "resource": mcp_url,
            },
        )
        if tokens.status_code != 200:
            raise FlowError(
                f"The token request was refused ({tokens.status_code}): {tokens.text[:300]}"
            )
        access_token = tokens.json()["access_token"]
        claims = decode_claims(access_token)
        steps.append("got an access token")

        hello = mcp(
            http,
            mcp_url,
            access_token,
            "initialize",
            {
                "protocolVersion": "2025-06-18",
                "capabilities": {},
                "clientInfo": {"name": client_name, "version": "1.0"},
            },
            id=2,
        )
        instructions = hello["result"].get("instructions", "")
        steps.append("said hello to the MCP server")
        access = call_tool(http, mcp_url, access_token, "get_my_access")
        my_access = access.get("structuredContent") or json.loads(access["content"][0]["text"])
        steps.append(f"asked what it may do: {my_access['status']}")
    except BaseException:
        cleanup()
        raise

    outcome = Outcome(
        mcp_url=mcp_url,
        issuer=issuer,
        metadata_found_at=found_at,
        client_id=client_id,
        consent_screen_shown=consent,
        token_claims=claims,
        server_instructions=instructions,
        my_access=my_access,
        steps=steps,
        access_token=access_token,
    )
    outcome._cleanup = cleanup
    return outcome


def main(argv: list[str]) -> int:
    if len(argv) != 4:
        print(__doc__)
        return 2
    _, mcp_url, username, password = argv
    try:
        outcome = connect_like_a_chatbot(mcp_url, username, password)
    except FlowError as error:
        print(f"FAILED: {error}")
        return 1
    try:
        for number, step in enumerate(outcome.steps, 1):
            print(f"{number}. {step}")
        aud = outcome.token_claims.get("aud")
        print(f"\nThe token was issued for: {aud}")
        print(f"The server says: {outcome.my_access['message']}")
        print("\nA chatbot can connect to this server.")
        return 0
    finally:
        outcome.close()


if __name__ == "__main__":
    sys.exit(main(sys.argv))
