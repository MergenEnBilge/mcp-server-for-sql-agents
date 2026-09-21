"""The saved-reports stub: CRUD, a read-only guarantee on the SQL, and admin-only access."""

import pytest
from sqlalchemy import text

SELECT = "SELECT country, count(*) AS customers FROM customers GROUP BY country"


async def connection_id(api, name="shop-pg") -> str:
    connections = (await api.client.get("/api/connections", headers=api.admin)).json()
    return next(c["id"] for c in connections if c["name"] == name)


async def create(api, cid, expect=201, **overrides):
    body = {
        "name": "Customers by country",
        "description": "Head count per country.",
        "connection_id": cid,
        "sql": SELECT,
        "chart_config": {"type": "bar", "x": "country", "y": "customers"},
        **overrides,
    }
    response = await api.client.post("/api/reports", json=body, headers=api.admin)
    assert response.status_code == expect, response.text
    return response.json()


@pytest.fixture(autouse=True)
async def no_leftover_reports(api):
    """Reports pin their connection in place, and the connection tests delete connections."""
    yield
    async with api.owner.begin() as db:
        await db.execute(text("DELETE FROM saved_reports"))


async def test_a_report_is_created_with_its_owner_and_connection_name(api):
    report = await create(api, await connection_id(api))
    assert report["connection_name"] == "shop-pg"
    assert report["owner_sub"] == "admin-1"
    assert report["chart_config"] == {"type": "bar", "x": "country", "y": "customers"}
    fetched = (await api.client.get(f"/api/reports/{report['id']}", headers=api.admin)).json()
    assert fetched == report


@pytest.mark.parametrize(
    "sql",
    [
        "DELETE FROM customers",
        "UPDATE customers SET country = 'XX'",
        "SELECT 1; DROP TABLE customers",
        "SELECT * INTO copy FROM customers",
        "not sql at all",
    ],
)
async def test_a_report_can_only_hold_a_read_only_query(api, sql):
    error = await create(api, await connection_id(api), expect=422, sql=sql)
    assert "read-only" in error["detail"]


async def test_a_report_can_target_either_engine(api):
    await create(api, await connection_id(api), sql="SELECT 1")
    await create(api, await connection_id(api, "shop-sqlite"), sql="SELECT 1")


async def test_a_report_on_a_missing_connection_is_refused(api):
    await create(api, "00000000-0000-0000-0000-000000000000", expect=422)


async def test_reports_can_be_searched_and_filtered_by_connection(api):
    pg, lite = await connection_id(api), await connection_id(api, "shop-sqlite")
    await create(api, pg, name="Revenue by month", description="Finance")
    await create(api, lite, name="Top customers", description="Sales")

    async def names(**params):
        response = await api.client.get("/api/reports", params=params, headers=api.admin)
        return {r["name"] for r in response.json()}

    assert await names() == {"Revenue by month", "Top customers"}
    assert await names(q="finance") == {"Revenue by month"}
    assert await names(connection_id=lite) == {"Top customers"}
    assert await names(q="100%") == set()  # % is text, not a wildcard


async def test_updating_a_report_records_what_changed_and_nothing_else(api):
    report = await create(api, await connection_id(api))
    body = {
        "name": "Customers by country (2026)",
        "description": report["description"],
        "connection_id": report["connection_id"],
        "sql": report["sql"],
        "chart_config": report["chart_config"],
    }
    updated = await api.client.put(f"/api/reports/{report['id']}", json=body, headers=api.admin)
    assert updated.status_code == 200 and updated.json()["name"] == body["name"]

    log = (
        await api.client.get(
            "/api/admin-log", params={"action": "report.update"}, headers=api.admin
        )
    ).json()
    entry = next(i for i in log["items"] if i["target"] == body["name"])
    assert entry["details"] == {"changed": ["name"]}


async def test_an_update_cannot_smuggle_in_a_write(api):
    report = await create(api, await connection_id(api))
    body = {**report, "sql": "DELETE FROM customers"}
    response = await api.client.put(f"/api/reports/{report['id']}", json=body, headers=api.admin)
    assert response.status_code == 422
    fetched = (await api.client.get(f"/api/reports/{report['id']}", headers=api.admin)).json()
    assert fetched["sql"] == SELECT


async def test_a_report_can_be_deleted_and_then_is_gone(api):
    report = await create(api, await connection_id(api))
    assert (
        await api.client.delete(f"/api/reports/{report['id']}", headers=api.admin)
    ).status_code == 204
    assert (
        await api.client.get(f"/api/reports/{report['id']}", headers=api.admin)
    ).status_code == 404
    log = (
        await api.client.get(
            "/api/admin-log", params={"action": "report.delete"}, headers=api.admin
        )
    ).json()
    assert any(i["target"] == "Customers by country" for i in log["items"])


async def test_a_connection_with_reports_cannot_be_deleted(api, postgres):
    connection = (
        await api.client.post(
            "/api/connections",
            json={
                "name": "with-reports",
                "engine": "postgresql",
                "details": {
                    "host": postgres.host,
                    "port": postgres.port,
                    "database": "org_data",
                    "username": "org_readonly",
                },
                "secret": "pw",
            },
            headers=api.admin,
        )
    ).json()
    report = await create(api, connection["id"])
    refused = await api.client.delete(f"/api/connections/{connection['id']}", headers=api.admin)
    assert refused.status_code == 409 and "1 saved report" in refused.json()["detail"]
    await api.client.delete(f"/api/reports/{report['id']}", headers=api.admin)
    assert (
        await api.client.delete(f"/api/connections/{connection['id']}", headers=api.admin)
    ).status_code == 204


async def test_only_administrators_can_touch_reports(api):
    cid = await connection_id(api)
    for method, path, kwargs in [
        ("get", "/api/reports", {}),
        ("post", "/api/reports", {"json": {"name": "x", "connection_id": cid, "sql": "SELECT 1"}}),
        ("delete", "/api/reports/00000000-0000-0000-0000-000000000000", {}),
    ]:
        assert (await getattr(api.client, method)(path, **kwargs)).status_code == 401, path
        member = await getattr(api.client, method)(path, headers=api.member, **kwargs)
        assert member.status_code == 403, path
