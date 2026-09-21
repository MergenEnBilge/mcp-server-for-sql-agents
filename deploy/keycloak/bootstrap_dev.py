"""Development setup for the identity provider: sample users and a test client.

The shipped realm file (sql-data-layer-realm.json) holds only structure: roles, the GUI client
and the audience scopes. Users and passwords are deliberately NOT in it. This script creates them
from environment variables, so no credentials live in the repository.

    python deploy/keycloak/bootstrap_dev.py                    # structure, sample users, test app
    python deploy/keycloak/bootstrap_dev.py --structure-only   # just what every deployment needs

Reads from the environment (or the repo .env):
    KEYCLOAK_URL              default http://localhost:8080/auth
    KEYCLOAK_ADMIN_USER       default admin
    KEYCLOAK_ADMIN_PASSWORD   required
    KEYCLOAK_DEV_USER_PASSWORD  required (unless --structure-only): password for the sample users

It creates:
    alice (admin), bob (analyst), carol (viewer)   sample users
    e2e-test, e2e-new-agent                        clients that allow the password grant, used ONLY
                                                   by automated tests to obtain tokens. The realm
                                                   file doesn't include them, so they can't exist in
                                                   a production realm by accident. (The seed script
                                                   approves e2e-test as an agent; e2e-new-agent is
                                                   left unapproved, to test the approval pop-up.)
It first makes sure the `mcp-audience` scope exists and is a default for every client, so tokens
issued to MCP clients (including ones that register themselves) are marked as meant for the MCP
server.

It also sets up what online chatbots need in order to connect to an MCP server without anyone
creating a client for them by hand (this part runs for every deployment, not only the demo):

    * Dynamic client registration (RFC 7591) is limited to the hosts chatbots use. A client may
      only register redirect addresses on those hosts, so an unknown site can't create a client
      that sends people's sign-ins somewhere else. KEYCLOAK_DCR_TRUSTED_HOSTS lists them.
    * PKCE with S256 is required of every client (the MCP specification requires it).
    * Refresh tokens are rotated, as the specification asks of public clients.
    * The app roles (admin, analyst, viewer, ...) are attached to the `mcp-audience` scope, so a
      self-registered client's tokens carry the roles the server's permissions are built on
      without switching on "full scope" for it.
    * The addresses that depend on where the server lives (the console's redirect addresses and
      the audiences in tokens) are set from PUBLIC_HOST, so moving from localhost to a real host
      name is a matter of changing one setting and running this again. (The realm file only knows
      localhost, and it is read only the first time the realm is created.)

Also reads KEYCLOAK_DCR_MAX_CLIENTS (default 1000): every connected chatbot adds a client.

Safe to run repeatedly.
"""

import os
import sys
import time
from pathlib import Path

import httpx

REALM = "sql-data-layer"

POLICY_TYPE = "org.keycloak.services.clientregistration.policy.ClientRegistrationPolicy"

# Hosts a chatbot's redirect address may be on, when it registers itself. Only the host is
# compared. localhost and 127.0.0.1 are for tools that run on the person's own machine.
DEFAULT_DCR_TRUSTED_HOSTS = (
    "claude.ai,claude.com,chatgpt.com,platform.openai.com,"
    "vscode.dev,insiders.vscode.dev,localhost,127.0.0.1"
)
BUILT_IN_ROLES = ("offline_access", "uma_authorization")
USERS = [
    ("alice", "Alice Admin", "admin"),
    ("bob", "Bob Analyst", "analyst"),
    ("carol", "Carol Viewer", "viewer"),
]


def load_env() -> None:
    env_file = Path(__file__).resolve().parents[2] / ".env"
    if env_file.exists():
        for line in env_file.read_text(encoding="utf-8").splitlines():
            if "=" in line and not line.lstrip().startswith("#"):
                key, value = line.split("=", 1)
                os.environ.setdefault(key.strip(), value.strip())


def public_origin() -> str:
    host = os.environ.get("PUBLIC_HOST", "localhost")
    return f"https://{host}"


def set_audience(mappers: list[dict], name: str, audience: str) -> bool:
    """Point the named audience mapper at `audience`. True if something changed."""
    changed = False
    for mapper in mappers:
        if mapper["name"] == name and mapper["config"].get("included.custom.audience") != audience:
            mapper["config"]["included.custom.audience"] = audience
            changed = True
    return changed


def point_at_public_host(kc: httpx.Client, scope_id: str) -> None:
    """The MCP audience scope and the console's client follow PUBLIC_HOST."""
    origin = public_origin()

    mappers = kc.get(f"/client-scopes/{scope_id}/protocol-mappers/models").json()
    if set_audience(mappers, "mcp-audience", os.environ.get("MCP_AUDIENCE", f"{origin}/mcp")):
        for mapper in mappers:
            kc.put(
                f"/client-scopes/{scope_id}/protocol-mappers/models/{mapper['id']}", json=mapper
            ).raise_for_status()

    console = kc.get("/clients", params={"clientId": "gui"}).json()
    if not console:
        return
    client = console[0]
    redirects = {*client.get("redirectUris", []), f"{origin}/*"}
    attributes = client.get("attributes", {})
    logout = {u for u in attributes.get("post.logout.redirect.uris", "").split("##") if u}
    attributes["post.logout.redirect.uris"] = "##".join(sorted(logout | {f"{origin}/*"}))
    client["redirectUris"] = sorted(redirects)
    client["attributes"] = attributes
    set_audience(client.get("protocolMappers", []), "gui-audience", f"{origin}/api")
    kc.put(f"/clients/{client['id']}", json=client).raise_for_status()


def csv(name: str, default: str) -> list[str]:
    return [part.strip() for part in os.environ.get(name, default).split(",") if part.strip()]


def allow_chatbots_to_register(kc: httpx.Client) -> None:
    """Anonymous dynamic client registration, but only for redirect addresses on known hosts."""
    policies = kc.get("/components", params={"type": POLICY_TYPE}).json()

    def anonymous(provider: str) -> dict:
        return next(
            p for p in policies if p["providerId"] == provider and p["subType"] == "anonymous"
        )

    trusted = anonymous("trusted-hosts")
    trusted["config"] = {
        "trusted-hosts": csv("KEYCLOAK_DCR_TRUSTED_HOSTS", DEFAULT_DCR_TRUSTED_HOSTS),
        # The request comes from the chatbot vendor's servers, whose addresses we can't know, so
        # what is checked instead is where the client says people should be sent back to.
        "host-sending-registration-request-must-match": ["false"],
        "client-uris-must-match": ["true"],
    }
    kc.put(f"/components/{trusted['id']}", json=trusted).raise_for_status()

    limit = anonymous("max-clients")
    limit["config"] = {"max-clients": [os.environ.get("KEYCLOAK_DCR_MAX_CLIENTS", "1000")]}
    kc.put(f"/components/{limit['id']}", json=limit).raise_for_status()
    # "Consent Required" (which makes a person approve a newly registered client the first time
    # they sign in to it) is on by default for anonymous registrations, and stays on.


def require_pkce(kc: httpx.Client) -> None:
    """Every client must use PKCE with S256. Adds one profile and one policy, leaving any others
    the operator has defined alone."""
    profile = {
        "name": "mcp-require-pkce",
        "description": "Every client must use PKCE (S256), as the MCP authorization spec requires.",
        "executors": [{"executor": "pkce-enforcer", "configuration": {"auto-configure": True}}],
    }
    policy = {
        "name": "mcp-require-pkce",
        "description": "Applies mcp-require-pkce to every client.",
        "enabled": True,
        "conditions": [{"condition": "any-client", "configuration": {}}],
        "profiles": ["mcp-require-pkce"],
    }
    profiles = kc.get("/client-policies/profiles").json().get("profiles") or []
    kc.put(
        "/client-policies/profiles",
        json={"profiles": [p for p in profiles if p["name"] != profile["name"]] + [profile]},
    ).raise_for_status()
    policies = kc.get("/client-policies/policies").json().get("policies") or []
    kc.put(
        "/client-policies/policies",
        json={"policies": [p for p in policies if p["name"] != policy["name"]] + [policy]},
    ).raise_for_status()


def put_app_roles_in_the_audience_scope(kc: httpx.Client, scope_id: str) -> None:
    """Clients that register themselves don't get every role in their tokens (their "full scope"
    is off). Attaching the app roles to the scope every MCP client has puts exactly those in."""
    roles = [
        r
        for r in kc.get("/roles").json()
        if r["name"] not in BUILT_IN_ROLES and not r["name"].startswith("default-roles-")
    ]
    if roles:
        kc.post(f"/client-scopes/{scope_id}/scope-mappings/realm", json=roles).raise_for_status()


def wait_for(base: str) -> None:
    for _ in range(90):
        try:
            if httpx.get(f"{base}/realms/master", timeout=3).status_code == 200:
                return
        except httpx.HTTPError:
            pass
        time.sleep(2)
    sys.exit(f"Keycloak did not come up at {base}")


def main() -> None:
    load_env()
    base = os.environ.get("KEYCLOAK_URL", "http://localhost:8080/auth").rstrip("/")
    admin_password = os.environ["KEYCLOAK_ADMIN_PASSWORD"]
    structure_only = "--structure-only" in sys.argv
    user_password = "" if structure_only else os.environ["KEYCLOAK_DEV_USER_PASSWORD"]
    wait_for(base)

    token = (
        httpx.post(
            f"{base}/realms/master/protocol/openid-connect/token",
            data={
                "grant_type": "password",
                "client_id": "admin-cli",
                "username": os.environ.get("KEYCLOAK_ADMIN_USER", "admin"),
                "password": admin_password,
            },
            timeout=15,
        )
        .raise_for_status()
        .json()["access_token"]
    )
    kc = httpx.Client(
        base_url=f"{base}/admin/realms/{REALM}",
        headers={"Authorization": f"Bearer {token}"},
        timeout=15,
    )

    # --- the audience scope for MCP clients is a realm default ------------------------------------
    # (Created here rather than in the realm file: a realm file that declares client scopes makes
    # Keycloak skip creating its standard ones such as `profile` and `roles`.)
    scopes = {s["name"]: s["id"] for s in kc.get("/client-scopes").json()}
    if "mcp-audience" not in scopes:
        kc.post(
            "/client-scopes",
            json={
                "name": "mcp-audience",
                "description": "Marks access tokens as issued for the MCP server (RFC 8707).",
                "protocol": "openid-connect",
                "attributes": {
                    "include.in.token.scope": "false",
                    "display.on.consent.screen": "false",
                },
                "protocolMappers": [
                    {
                        "name": "mcp-audience",
                        "protocol": "openid-connect",
                        "protocolMapper": "oidc-audience-mapper",
                        "consentRequired": False,
                        "config": {
                            "included.custom.audience": os.environ.get(
                                "MCP_AUDIENCE", "https://localhost/mcp"
                            ),
                            "access.token.claim": "true",
                            "id.token.claim": "false",
                        },
                    }
                ],
            },
        ).raise_for_status()
        scopes = {s["name"]: s["id"] for s in kc.get("/client-scopes").json()}
    default = kc.put(f"/default-default-client-scopes/{scopes['mcp-audience']}")
    if default.status_code != 409:  # 409 means it already is a default: nothing to do
        default.raise_for_status()

    put_app_roles_in_the_audience_scope(kc, scopes["mcp-audience"])
    point_at_public_host(kc, scopes["mcp-audience"])
    allow_chatbots_to_register(kc)
    require_pkce(kc)
    # Rotate refresh tokens: each use gives a new one and retires the old, so a stolen one is
    # only good until the real client next refreshes.
    kc.put("", json={"revokeRefreshToken": True, "refreshTokenMaxReuse": 0}).raise_for_status()

    if structure_only:
        print(
            f"Keycloak realm '{REALM}' structure ready: audience scope for MCP clients, "
            "self-registration limited to known chatbot hosts, PKCE required."
        )
        return

    # --- users -----------------------------------------------------------------------------
    for username, full_name, role in USERS:
        existing = kc.get("/users", params={"username": username, "exact": "true"}).json()
        if existing:
            user_id = existing[0]["id"]
        else:
            first, last = full_name.split(" ", 1)
            response = kc.post(
                "/users",
                json={
                    "username": username,
                    "email": f"{username}@example.test",
                    "firstName": first,
                    "lastName": last,
                    "enabled": True,
                    "emailVerified": True,
                },
            )
            response.raise_for_status()
            user_id = response.headers["Location"].rsplit("/", 1)[1]
        kc.put(
            f"/users/{user_id}/reset-password",
            json={"type": "password", "value": user_password, "temporary": False},
        ).raise_for_status()
        role_repr = kc.get(f"/roles/{role}").json()
        kc.post(f"/users/{user_id}/role-mappings/realm", json=[role_repr]).raise_for_status()

    # --- the clients automated tests use to get tokens ---------------------------------------
    for test_client in ("e2e-test", "e2e-new-agent"):
        create_test_client(kc, test_client)
    print(
        f"Keycloak realm '{REALM}' ready: users alice, bob, carol; clients e2e-test, e2e-new-agent."
    )


def create_test_client(kc: httpx.Client, client_id: str) -> None:
    if not kc.get("/clients", params={"clientId": client_id}).json():
        kc.post(
            "/clients",
            json={
                "clientId": client_id,
                "name": "Automated test client (dev only)",
                "enabled": True,
                "publicClient": True,
                "directAccessGrantsEnabled": True,
                "standardFlowEnabled": False,
                "defaultClientScopes": [
                    "mcp-audience",
                    "web-origins",
                    "acr",
                    "profile",
                    "roles",
                    "basic",
                    "email",
                ],
                "protocolMappers": [
                    {
                        "name": "gui-audience",
                        "protocol": "openid-connect",
                        "protocolMapper": "oidc-audience-mapper",
                        "consentRequired": False,
                        "config": {
                            "included.custom.audience": os.environ.get(
                                "GUI_AUDIENCE", "https://localhost/api"
                            ),
                            "access.token.claim": "true",
                            "id.token.claim": "false",
                        },
                    }
                ],
            },
        ).raise_for_status()


if __name__ == "__main__":
    main()
