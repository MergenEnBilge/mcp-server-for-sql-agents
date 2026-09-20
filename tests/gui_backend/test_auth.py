"""Who gets in, and who gets in to the admin endpoints."""

import pytest
from conftest import GUI_AUDIENCE
from fake_idp import AUDIENCE as MCP_AUDIENCE

ADMIN_ENDPOINTS = [
    ("GET", "/api/audit"),
    ("GET", "/api/audit/facets"),
    ("GET", "/api/audit/1"),
    ("GET", "/api/admin-log"),
    ("GET", "/api/engines"),
    ("GET", "/api/connections"),
    ("POST", "/api/connections"),
    ("GET", "/api/permissions/subjects"),
    ("POST", "/api/permissions/subjects"),
    ("GET", "/api/permissions/tools"),
    ("PUT", "/api/permissions/tools"),
    ("PUT", "/api/permissions/tables"),
]


async def test_the_health_endpoints_are_public(api):
    assert (await api.client.get("/healthz")).json() == {"status": "ok"}
    assert (await api.client.get("/readyz")).json() == {"status": "ready"}


async def test_without_a_token_every_api_endpoint_says_to_sign_in(api):
    for method, path in [("GET", "/api/me"), *ADMIN_ENDPOINTS]:
        response = await api.client.request(method, path)
        assert response.status_code == 401, (method, path)
        assert response.headers["www-authenticate"].startswith("Bearer")


@pytest.mark.parametrize(
    "make_headers",
    [
        lambda api: {"Authorization": "Bearer not-a-token"},
        lambda api: {
            "Authorization": f"Bearer {api.idp.token(aud=GUI_AUDIENCE, expires_in=-3600)}"
        },
        lambda api: {
            "Authorization": f"Bearer {api.idp.token(aud=GUI_AUDIENCE, iss='https://evil.test')}"
        },
    ],
    ids=["garbage", "expired", "wrong-issuer"],
)
async def test_bad_tokens_are_refused(api, make_headers):
    response = await api.client.get("/api/me", headers=make_headers(api))
    assert response.status_code == 401
    assert "invalid_token" in response.headers["www-authenticate"]


async def test_a_token_meant_for_the_mcp_server_cannot_be_used_on_the_admin_api(api):
    """Same identity provider, different audience: the classic replay across services."""
    token = api.idp.token(aud=MCP_AUDIENCE, roles=("admin",))
    response = await api.client.get("/api/me", headers={"Authorization": f"Bearer {token}"})
    assert response.status_code == 401


async def test_signed_in_people_can_ask_who_they_are(api):
    admin = (await api.client.get("/api/me", headers=api.admin)).json()
    assert admin == {"sub": "admin-1", "name": "Ada Admin", "roles": ["admin"], "is_admin": True}
    member = (await api.client.get("/api/me", headers=api.member)).json()
    assert member["is_admin"] is False and member["roles"] == ["analyst"]


@pytest.mark.parametrize(("method", "path"), ADMIN_ENDPOINTS)
async def test_admin_endpoints_refuse_signed_in_non_administrators(api, method, path):
    response = await api.client.request(method, path, headers=api.member, json={})
    assert response.status_code == 403
    assert "administrator" in response.json()["detail"]


async def test_an_admin_scope_is_as_good_as_an_admin_role(api):
    headers = api.headers("analyst", sub="scoped-1", scope="openid admin")
    assert (await api.client.get("/api/engines", headers=headers)).status_code == 200


async def test_signing_in_makes_a_person_available_in_the_permission_grids(api):
    await api.client.get(
        "/api/me", headers=api.headers("analyst", sub="seen-me-1", name="Sam Seen")
    )
    subjects = (await api.client.get("/api/permissions/subjects", headers=api.admin)).json()
    assert any(
        s["subject_type"] == "user"
        and s["subject_id"] == "seen-me-1"
        and s["display_name"] == "Sam Seen"
        for s in subjects
    )
