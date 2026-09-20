"""The audit-log viewer's API: filtering, sorting, paging, and the detail view."""

import json
from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest
from sqlalchemy import text

LONG_SQL = "SELECT " + ", ".join(f"col_{i}" for i in range(120)) + " FROM orders"


@pytest.fixture
async def seeded(api):
    """A batch of audit rows that belong to this test alone (marked by a unique connection
    name), with known callers, tools, tables, durations and outcomes."""
    tag = f"audit-{uuid4().hex[:8]}"
    now = datetime.now(UTC)
    rows = [
        # (minutes ago, caller, name, tool, tables, ok, ms, rows, sql, error)
        (
            50,
            "u-ana",
            "Ana Analyst",
            "run_query",
            ["orders"],
            True,
            40,
            10,
            "SELECT 1 FROM orders",
            None,
        ),
        (40, "u-ana", "Ana Analyst", "list_tables", [], True, 5, None, None, None),
        (
            30,
            "u-ben",
            "Ben Builder",
            "run_query",
            ["payments"],
            False,
            12,
            None,
            "SELECT * FROM payments",
            "Table 'payments' was not found or is not available to you.",
        ),
        (
            20,
            "u-ben",
            "Ben Builder",
            "run_query",
            ["orders", "customers"],
            True,
            900,
            500,
            "SELECT * FROM orders JOIN customers",
            None,
        ),
        (10, "u-cy", "Cy Analyst", "describe_table", ["orders"], True, 3, 5, None, None),
        (5, "u-cy", "Cy Analyst", "run_query", ["orders"], True, 75, 1, LONG_SQL, None),
    ]
    async with api.owner.begin() as db:
        for ago, sub, name, tool, tables, ok, ms, n, sql, err in rows:
            arguments = {"connection_name": tag, **({"sql": sql} if sql else {})}
            await db.execute(
                text(
                    "INSERT INTO audit_log (occurred_at, caller_sub, caller_name, tool_name, "
                    "connection_name, tables, arguments, success, error_message, row_count, "
                    "duration_ms, result_summary) VALUES (:at, :sub, :name, :tool, :conn, :tables, "
                    "CAST(:args AS jsonb), :ok, :err, :n, :ms, :summary)"
                ),
                {
                    "at": now - timedelta(minutes=ago),
                    "sub": sub,
                    "name": name,
                    "tool": tool,
                    "conn": tag,
                    "tables": tables,
                    "args": json.dumps(arguments),
                    "ok": ok,
                    "err": err,
                    "n": n,
                    "ms": ms,
                    "summary": f"{n} rows" if n else None,
                },
            )
    return tag


async def fetch(api, **params):
    response = await api.client.get("/api/audit", params=params, headers=api.admin)
    assert response.status_code == 200, response.text
    return response.json()


def ids_of(page):
    return [(i["caller_sub"], i["tool_name"]) for i in page["items"]]


async def test_newest_entries_come_first_by_default(api, seeded):
    page = await fetch(api, connection=seeded)
    assert page["total"] == 6
    assert ids_of(page)[0] == ("u-cy", "run_query")
    assert ids_of(page)[-1] == ("u-ana", "run_query")


async def test_pagination_reports_the_total_and_slices_correctly(api, seeded):
    first = await fetch(api, connection=seeded, page_size=4, page=1)
    second = await fetch(api, connection=seeded, page_size=4, page=2)
    assert (first["total"], len(first["items"]), len(second["items"])) == (6, 4, 2)
    assert not {i["id"] for i in first["items"]} & {i["id"] for i in second["items"]}


async def test_page_size_is_capped_by_the_server(api, seeded):
    page = await fetch(api, connection=seeded, page_size=100000)
    assert page["page_size"] == 200


@pytest.mark.parametrize(
    ("filters", "expected"),
    [
        ({"user": "ben"}, {("u-ben", "run_query")}),  # part of a display name, any case
        ({"user": "u-cy"}, {("u-cy", "describe_table"), ("u-cy", "run_query")}),
        ({"tool": "list_tables"}, {("u-ana", "list_tables")}),
        ({"table": "payments"}, {("u-ben", "run_query")}),
        ({"table": "customers"}, {("u-ben", "run_query")}),
        ({"success": "false"}, {("u-ben", "run_query")}),
        ({"q": "payments"}, {("u-ben", "run_query")}),  # in the SQL, or the error message
        ({"q": "JOIN customers"}, {("u-ben", "run_query")}),
    ],
)
async def test_filters(api, seeded, filters, expected):
    page = await fetch(api, connection=seeded, **filters)
    assert set(ids_of(page)) == expected
    assert page["total"] == len(page["items"])


async def test_filters_combine(api, seeded):
    page = await fetch(api, connection=seeded, user="ben", tool="run_query", success="true")
    assert page["total"] == 1 and page["items"][0]["row_count"] == 500


async def test_a_date_range_is_half_open(api, seeded):
    now = datetime.now(UTC)
    page = await fetch(
        api,
        connection=seeded,
        date_from=(now - timedelta(minutes=45)).isoformat(),
        date_to=(now - timedelta(minutes=15)).isoformat(),
    )
    assert set(ids_of(page)) == {("u-ana", "list_tables"), ("u-ben", "run_query")}


async def test_a_filter_that_matches_nothing_gives_an_empty_page_not_an_error(api, seeded):
    page = await fetch(api, connection=seeded, user="nobody-by-that-name")
    assert page == {"items": [], "total": 0, "page": 1, "page_size": 50}


@pytest.mark.parametrize(
    ("sort", "order", "first_ms"),
    [("duration", "desc", 900), ("duration", "asc", 3), ("occurred_at", "asc", 40)],
)
async def test_sorting(api, seeded, sort, order, first_ms):
    page = await fetch(api, connection=seeded, sort=sort, order=order)
    assert page["items"][0]["duration_ms"] == first_ms


async def test_sorting_by_a_column_that_does_not_exist_is_refused(api, seeded):
    response = await api.client.get(
        "/api/audit", params={"sort": "id; DROP TABLE audit_log"}, headers=api.admin
    )
    assert response.status_code == 422


async def test_a_search_containing_sql_or_wildcards_is_just_text(api, seeded):
    for hostile in ("'; DROP TABLE audit_log; --", "%", "_", "\\"):
        response = await api.client.get(
            "/api/audit", params={"connection": seeded, "q": hostile}, headers=api.admin
        )
        assert response.status_code == 200
    assert (await fetch(api, connection=seeded))["total"] == 6  # and nothing was harmed
    assert (await fetch(api, connection=seeded, q="%"))["total"] == 0  # '%' isn't a wildcard here


async def test_long_sql_is_previewed_in_the_list_and_complete_in_the_detail_view(api, seeded):
    page = await fetch(api, connection=seeded, user="cy", tool="run_query")
    item = page["items"][0]
    assert item["sql_truncated"] is True
    assert len(item["sql_preview"]) == 240 and LONG_SQL.startswith(item["sql_preview"])

    detail = (await api.client.get(f"/api/audit/{item['id']}", headers=api.admin)).json()
    assert detail["sql"] == LONG_SQL
    assert detail["arguments"]["connection_name"] == seeded
    assert detail["result_summary"] == "1 rows"


async def test_short_sql_is_not_marked_truncated(api, seeded):
    item = (await fetch(api, connection=seeded, tool="run_query", user="u-ana"))["items"][0]
    assert item["sql_truncated"] is False and item["sql_preview"] == "SELECT 1 FROM orders"


async def test_failed_calls_carry_their_error(api, seeded):
    item = (await fetch(api, connection=seeded, success="false"))["items"][0]
    assert item["success"] is False
    assert "not available to you" in item["error_preview"]
    detail = (await api.client.get(f"/api/audit/{item['id']}", headers=api.admin)).json()
    assert detail["error_message"].startswith("Table 'payments'")


async def test_an_unknown_entry_is_a_404(api):
    response = await api.client.get("/api/audit/999999999", headers=api.admin)
    assert response.status_code == 404


async def test_facets_list_what_can_be_filtered_on(api, seeded):
    facets = (await api.client.get("/api/audit/facets", headers=api.admin)).json()
    assert {"u-ana", "u-ben", "u-cy"} <= {c["sub"] for c in facets["callers"]}
    assert {"run_query", "list_tables", "describe_table"} <= set(facets["tools"])
    assert seeded in facets["connections"]
    assert {"orders", "payments", "customers"} <= set(facets["tables"])


async def test_the_audit_log_cannot_be_changed_through_the_api(api, seeded):
    for method in ("PUT", "POST", "DELETE", "PATCH"):
        response = await api.client.request(method, "/api/audit/1", headers=api.admin)
        assert response.status_code in (404, 405)
