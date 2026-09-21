"""The connection manager's API. The central promise: credentials go in, and never come out."""

import json
from uuid import uuid4

import pytest
from sqlalchemy import text

PASSWORD = "Sup3r-secret-pw!"


def pg_details(postgres, **overrides) -> dict:
    return {
        "host": postgres.host,
        "port": postgres.port,
        "database": "org_data",
        "username": "org_readonly",
        **overrides,
    }


def new_connection(postgres, **overrides) -> dict:
    return {
        "name": f"test-{uuid4().hex[:8]}",
        "engine": "postgresql",
        "description": "A test connection",
        "details": pg_details(postgres),
        "secret": PASSWORD,
        **overrides,
    }


async def create(api, body, expect=201):
    response = await api.client.post("/api/connections", json=body, headers=api.admin)
    assert response.status_code == expect, response.text
    return response.json()


async def stored_secret(api, connection_id) -> str | None:
    async with api.owner.connect() as db:
        return (
            await db.execute(
                text("SELECT secret_encrypted FROM connections WHERE id = :id"),
                {"id": connection_id},
            )
        ).scalar_one()


# --- credentials are write-only -------------------------------------------------------------------


async def test_a_created_connection_never_reveals_its_secret(api, postgres, fernet_key):
    created = await create(api, new_connection(postgres))
    assert created["has_secret"] is True
    assert "secret" not in created and "secret_encrypted" not in created

    everything = json.dumps(
        [
            (await api.client.get("/api/connections", headers=api.admin)).json(),
            (await api.client.get(f"/api/connections/{created['id']}", headers=api.admin)).json(),
            (
                await api.client.get(f"/api/connections/{created['id']}/access", headers=api.admin)
            ).json(),
        ]
    )
    assert PASSWORD not in everything


async def test_the_secret_is_encrypted_at_rest_and_the_mcp_servers_key_opens_it(
    api, postgres, secret_box
):
    created = await create(api, new_connection(postgres))
    stored = await stored_secret(api, created["id"])
    assert PASSWORD not in stored
    assert secret_box.decrypt(stored) == PASSWORD  # the MCP server can read what the GUI wrote


async def test_the_secret_never_reaches_the_admin_log(api, postgres):
    body = new_connection(postgres)
    created = await create(api, body)
    await api.client.put(
        f"/api/connections/{created['id']}",
        json={"details": body["details"], "description": "changed", "secret": "Another-pw-9"},
        headers=api.admin,
    )
    log = (
        await api.client.get(
            "/api/admin-log", params={"target_type": "connection"}, headers=api.admin
        )
    ).text
    assert PASSWORD not in log and "Another-pw-9" not in log


@pytest.mark.parametrize("key", ["password", "db_password", "secret", "api_token", "pwd"])
async def test_a_secret_cannot_be_smuggled_into_the_plain_text_settings(api, postgres, key):
    body = new_connection(postgres, details=pg_details(postgres))
    body["details"][key] = "hunter2"
    response = await api.client.post("/api/connections", json=body, headers=api.admin)
    assert response.status_code == 422
    assert (
        "secret field" in response.json()["detail"]
        or "Unknown setting" in response.json()["detail"]
    )


async def test_a_secret_cannot_hide_in_a_driver_option(api, postgres):
    body = new_connection(postgres)
    body["details"]["options"] = {"sslmode": "require", "application_password": "x"}
    response = await api.client.post("/api/connections", json=body, headers=api.admin)
    assert response.status_code == 422


# --- creating and validating ------------------------------------------------------------------------


async def test_engines_are_offered_for_the_dropdown(api):
    engines = (await api.client.get("/api/engines", headers=api.admin)).json()
    assert {e["engine"] for e in engines} == {"postgresql", "mysql", "mssql", "sqlite"}
    assert next(e for e in engines if e["engine"] == "postgresql")["default_port"] == 5432


async def test_engines_say_whether_this_server_can_open_them(api):
    engines = {
        e["engine"]: e for e in (await api.client.get("/api/engines", headers=api.admin)).json()
    }
    assert engines["postgresql"]["available"] and engines["sqlite"]["available"]
    for engine in engines.values():
        assert (engine["unavailable_reason"] is None) == engine["available"]


async def test_a_new_connection_starts_with_no_access_and_no_check(api, postgres):
    created = await create(api, new_connection(postgres))
    assert (created["access_count"], created["last_checked_at"], created["last_check_ok"]) == (
        0,
        None,
        None,
    )
    assert created["details"]["database"] == "org_data"


@pytest.mark.parametrize(
    ("change", "message"),
    [
        ({"name": "Has Spaces"}, None),
        ({"name": "UPPER"}, None),
        ({"name": ""}, None),
        ({"engine": "oracle"}, None),
        ({"details": {"host": "h", "database": "d"}}, "Missing required setting: username"),
        (
            {"details": {"host": "h", "database": "d", "username": "u", "extra": 1}},
            "Unknown setting: extra",
        ),
        ({"details": {"host": "h/../x", "database": "d", "username": "u"}}, "plain host"),
        ({"details": {"host": "h", "database": "d", "username": "u", "port": 99999}}, "port"),
        ({"engine": "sqlite", "details": {"host": "h"}}, "Missing required setting: path"),
    ],
)
async def test_bad_settings_are_refused_with_a_reason(api, postgres, change, message):
    response = await api.client.post(
        "/api/connections", json=new_connection(postgres, **change), headers=api.admin
    )
    assert response.status_code == 422
    if message:
        assert message in response.text


async def test_names_must_be_unique(api, postgres):
    body = new_connection(postgres)
    await create(api, body)
    again = await create(
        api, {**body, "details": pg_details(postgres, database="other")}, expect=409
    )
    assert "already exists" in again["detail"]


async def test_a_sqlite_connection_needs_only_a_path(api, sqlite_file):
    body = {
        "name": f"lite-{uuid4().hex[:6]}",
        "engine": "sqlite",
        "details": {"path": str(sqlite_file)},
    }
    created = await create(api, body)
    assert created["has_secret"] is False


@pytest.mark.parametrize(
    "path", ["/etc/passwd", "../../elsewhere.sqlite", "file:/x.sqlite?mode=rwc", "C:/Windows/x.db"]
)
async def test_a_sqlite_file_outside_the_allowed_folder_is_refused(api, path):
    body = {"name": f"lite-{uuid4().hex[:6]}", "engine": "sqlite", "details": {"path": path}}
    for url in ("/api/connections", "/api/connections/test"):
        response = await api.client.post(url, json=body, headers=api.admin)
        assert response.status_code == 422, url
        assert "SQLite" in response.json()["detail"]


@pytest.mark.parametrize("host", ["169.254.169.254", "metadata.google.internal", "0.0.0.0"])
async def test_a_cloud_metadata_address_is_refused_as_a_database_host(api, postgres, host):
    body = new_connection(postgres, details=pg_details(postgres, host=host))
    response = await api.client.post("/api/connections", json=body, headers=api.admin)
    assert response.status_code == 422


# --- editing --------------------------------------------------------------------------------------------


async def test_editing_without_a_new_secret_keeps_the_old_one(api, postgres):
    created = await create(api, new_connection(postgres))
    before = await stored_secret(api, created["id"])
    response = await api.client.put(
        f"/api/connections/{created['id']}",
        json={"details": created["details"], "description": "renamed in the UI"},
        headers=api.admin,
    )
    assert response.json()["description"] == "renamed in the UI" and response.json()["has_secret"]
    assert await stored_secret(api, created["id"]) == before


async def test_a_new_secret_replaces_the_old_one_and_a_clear_removes_it(api, postgres, secret_box):
    created = await create(api, new_connection(postgres))
    put = lambda body: api.client.put(  # noqa: E731
        f"/api/connections/{created['id']}",
        json={"details": created["details"], **body},
        headers=api.admin,
    )
    await put({"secret": "Replacement-pw-1"})
    assert secret_box.decrypt(await stored_secret(api, created["id"])) == "Replacement-pw-1"
    cleared = (await put({"clear_secret": True})).json()
    assert cleared["has_secret"] is False and await stored_secret(api, created["id"]) is None


async def test_name_and_engine_cannot_be_changed_by_editing(api, postgres):
    created = await create(api, new_connection(postgres))
    updated = (
        await api.client.put(
            f"/api/connections/{created['id']}",
            json={"name": "renamed", "engine": "mysql", "details": created["details"]},
            headers=api.admin,
        )
    ).json()
    assert (updated["name"], updated["engine"]) == (created["name"], "postgresql")


async def test_editing_a_connection_updates_its_edit_time_but_a_health_check_does_not(
    api, postgres
):
    """`updated_at` is how the MCP server knows to rebuild its connection pool."""
    created = await create(api, new_connection(postgres))
    await api.client.post(f"/api/connections/{created['id']}/test", headers=api.admin)
    after_check = (
        await api.client.get(f"/api/connections/{created['id']}", headers=api.admin)
    ).json()
    assert after_check["updated_at"] == created["updated_at"]
    assert after_check["last_checked_at"] is not None

    edited = (
        await api.client.put(
            f"/api/connections/{created['id']}",
            json={"details": created["details"], "description": "new"},
            headers=api.admin,
        )
    ).json()
    assert edited["updated_at"] > created["updated_at"]


# --- testing a connection -------------------------------------------------------------------------------


async def test_a_working_connection_says_so_and_records_the_result(api, postgres):
    created = await create(api, new_connection(postgres, secret=postgres.passwords["ORG_READONLY"]))
    result = (
        await api.client.post(f"/api/connections/{created['id']}/test", headers=api.admin)
    ).json()
    assert result["ok"] is True and result["table_count"] == 7 and "Connected" in result["message"]

    saved = (await api.client.get(f"/api/connections/{created['id']}", headers=api.admin)).json()
    assert saved["last_check_ok"] is True and saved["last_check_error"] is None


@pytest.mark.parametrize(
    ("change", "expect"),
    [
        ({"secret": "wrong-password"}, "authentication failed for user 'org_readonly'"),
        ({"details": {"port": 1}}, "could not reach host"),
        ({"details": {"database": "no_such_db"}}, "'no_such_db' does not exist"),
    ],
    ids=["bad-password", "wrong-port", "no-such-database"],
)
async def test_failures_say_what_to_fix_and_never_echo_the_password(api, postgres, change, expect):
    good_pw = postgres.passwords["ORG_READONLY"]
    body = {
        "engine": "postgresql",
        "details": pg_details(postgres, **change.get("details", {})),
        "secret": change.get("secret", good_pw),
    }
    result = (await api.client.post("/api/connections/test", json=body, headers=api.admin)).json()
    assert result["ok"] is False
    assert expect in result["message"] and result["message"].startswith("Connection failed:")
    assert body["secret"] not in result["message"] and good_pw not in result["message"]


async def test_a_failed_check_of_a_saved_connection_is_remembered(api, postgres):
    created = await create(api, new_connection(postgres, secret="wrong-password"))
    result = (
        await api.client.post(f"/api/connections/{created['id']}/test", headers=api.admin)
    ).json()
    saved = (await api.client.get(f"/api/connections/{created['id']}", headers=api.admin)).json()
    assert saved["last_check_ok"] is False and saved["last_check_error"] == result["message"]


async def test_unsaved_settings_can_be_tested_reusing_a_stored_secret(api, postgres):
    created = await create(api, new_connection(postgres, secret=postgres.passwords["ORG_READONLY"]))
    body = {"engine": "postgresql", "details": created["details"], "connection_id": created["id"]}
    assert (await api.client.post("/api/connections/test", json=body, headers=api.admin)).json()[
        "ok"
    ]


async def test_sqlite_files_are_tested_too(api, sqlite_file):
    ok = await api.client.post(
        "/api/connections/test",
        json={"engine": "sqlite", "details": {"path": str(sqlite_file)}},
        headers=api.admin,
    )
    assert ok.json()["ok"] and ok.json()["table_count"] == 7
    missing = await api.client.post(
        "/api/connections/test",
        json={"engine": "sqlite", "details": {"path": str(sqlite_file) + ".nope"}},
        headers=api.admin,
    )
    assert missing.json()["ok"] is False and "could not open" in missing.json()["message"]


# --- who may use it --------------------------------------------------------------------------------------


async def test_access_lists_are_replaced_as_a_whole_and_logged(api, postgres):
    created = await create(api, new_connection(postgres))
    url = f"/api/connections/{created['id']}/access"
    both = {
        "subjects": [
            {"subject_type": "role", "subject_id": "gui-test-role"},
            {"subject_type": "user", "subject_id": "u-special"},
        ]
    }
    got = (await api.client.put(url, json=both, headers=api.admin)).json()
    assert {(s["subject_type"], s["subject_id"]) for s in got["subjects"]} == {
        ("role", "gui-test-role"),
        ("user", "u-special"),
    }
    assert (await api.client.get(f"/api/connections/{created['id']}", headers=api.admin)).json()[
        "access_count"
    ] == 2

    await api.client.put(url, json={"subjects": [both["subjects"][0]]}, headers=api.admin)
    assert len((await api.client.get(url, headers=api.admin)).json()["subjects"]) == 1

    log = (
        await api.client.get(
            "/api/admin-log", params={"action": "connection.access"}, headers=api.admin
        )
    ).json()
    mine = [i for i in log["items"] if i["target"] == created["name"]]
    assert mine[0]["details"]["revoked"] == ["user:u-special"]
    assert mine[1]["details"]["granted"] == ["role:gui-test-role", "user:u-special"]


async def test_the_mcp_server_sees_access_changes_immediately(api, postgres, secret_box):
    """The GUI writes, then announces the change so cached permissions are dropped at once."""
    from mcp_sql_server.cache.base import META

    created = await create(api, new_connection(postgres))
    before = await api.cache.version(META)
    await api.client.put(
        f"/api/connections/{created['id']}/access",
        json={"subjects": [{"subject_type": "role", "subject_id": "gui-test-role"}]},
        headers=api.admin,
    )
    assert await api.cache.version(META) == before + 1


# --- deleting -------------------------------------------------------------------------------------------


async def test_deleting_removes_the_connection_and_its_grants_and_says_so(api, postgres):
    created = await create(api, new_connection(postgres))
    await api.client.put(
        f"/api/connections/{created['id']}/access",
        json={"subjects": [{"subject_type": "role", "subject_id": "gui-test-role"}]},
        headers=api.admin,
    )
    assert (
        await api.client.delete(f"/api/connections/{created['id']}", headers=api.admin)
    ).status_code == 204
    assert (
        await api.client.get(f"/api/connections/{created['id']}", headers=api.admin)
    ).status_code == 404

    log = (
        await api.client.get(
            "/api/admin-log", params={"action": "connection.delete"}, headers=api.admin
        )
    ).json()
    entry = next(i for i in log["items"] if i["target"] == created["name"])
    assert entry["details"]["access_grants_removed"] == 1


async def test_a_connection_with_saved_reports_cannot_be_deleted(api, postgres):
    created = await create(api, new_connection(postgres))
    async with api.owner.begin() as db:
        await db.execute(
            text(
                "INSERT INTO saved_reports (name, connection_id, sql, owner_sub) "
                "VALUES ('r', :c, 'select 1', 'u')"
            ),
            {"c": created["id"]},
        )
    response = await api.client.delete(f"/api/connections/{created['id']}", headers=api.admin)
    assert response.status_code == 409
    assert "used by 1 saved report" in response.json()["detail"]


async def test_unknown_connections_are_404s(api):
    missing = "00000000-0000-0000-0000-000000000000"
    for method, path in [
        ("GET", ""),
        ("PUT", ""),
        ("DELETE", ""),
        ("POST", "/test"),
        ("GET", "/access"),
    ]:
        response = await api.client.request(
            method, f"/api/connections/{missing}{path}", headers=api.admin, json={"details": {}}
        )
        assert response.status_code == 404, (method, path)


# --- where agents connect --------------------------------------------------------------------------------


async def test_the_connect_screen_is_told_the_public_address_and_where_the_guide_is(api):
    api.ctx.settings.mcp_public_url = "https://mcp.example.com/mcp"
    info = (await api.client.get("/api/server-info", headers=api.admin)).json()
    assert info["mcp_url"] == "https://mcp.example.com/mcp"
    assert info["guide_url"] == "https://mcp.example.com/agent-guide"
    assert info["secure"] is True and info["issuer"]


async def test_without_a_configured_address_it_says_so_instead_of_guessing(api):
    api.ctx.settings.mcp_public_url = None
    info = (await api.client.get("/api/server-info", headers=api.admin)).json()
    assert (info["mcp_url"], info["guide_url"], info["secure"]) == (None, None, False)


async def test_plain_http_is_reported_as_not_secure(api):
    api.ctx.settings.mcp_public_url = "http://localhost:8000/mcp"
    assert (await api.client.get("/api/server-info", headers=api.admin)).json()["secure"] is False


async def test_only_administrators_see_it(api):
    assert (await api.client.get("/api/server-info")).status_code == 401
    assert (await api.client.get("/api/server-info", headers=api.member)).status_code == 403
