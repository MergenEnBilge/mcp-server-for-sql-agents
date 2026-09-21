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
    e2e-test                                       a client that allows the password grant, used
                                                   ONLY by automated tests to obtain tokens. The
                                                   realm file doesn't include it, so it can't
                                                   exist in a production realm by accident.
It first makes sure the `mcp-audience` scope exists and is a default for every client, so tokens
issued to MCP clients (including ones that register themselves) are marked as meant for the MCP
server.

Safe to run repeatedly.
"""

import os
import sys
import time
from pathlib import Path

import httpx

REALM = "sql-data-layer"
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

    if structure_only:
        print(f"Keycloak realm '{REALM}' structure ready (audience scope for MCP clients).")
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

    # --- the client automated tests use to get tokens ---------------------------------------------
    if not kc.get("/clients", params={"clientId": "e2e-test"}).json():
        kc.post(
            "/clients",
            json={
                "clientId": "e2e-test",
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
    print(f"Keycloak realm '{REALM}' ready: users alice, bob, carol; client e2e-test.")


if __name__ == "__main__":
    main()
