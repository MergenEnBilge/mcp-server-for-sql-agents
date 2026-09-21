"""Authorization, secrets at rest, and the database-role boundaries, against real Postgres."""

from uuid import uuid4

import pytest
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import create_async_engine

from mcp_sql_server.errors import ConnectionNotFound, TableNotFound, ToolNotPermitted
from mcp_sql_server.models import Caller


async def grant(stack, sql: str, **params) -> None:
    async with stack.admin.begin() as db:
        await db.execute(text(sql), params)


async def connection_id(stack, name: str):
    async with stack.admin.connect() as db:
        return (
            await db.execute(text("SELECT id FROM connections WHERE name = :n"), {"n": name})
        ).scalar_one()


# --- the three permission layers ------------------------------------------------------------


async def test_tool_permissions_decide_which_tools_a_role_may_call(stack):
    role = f"viewer-{uuid4().hex[:6]}"
    caller = Caller(sub=f"u-{uuid4().hex[:6]}", roles=frozenset({role}))
    pg = await connection_id(stack, "shop-pg")
    await grant(stack, "INSERT INTO connection_access VALUES (:c, 'role', :r)", c=pg, r=role)
    await grant(
        stack,
        "INSERT INTO table_permissions (connection_id, subject_type, subject_id, table_name) VALUES (:c, 'role', :r, 'orders')",
        c=pg,
        r=role,
    )
    await grant(
        stack,
        "INSERT INTO tool_permissions (subject_type, subject_id, tool_name) VALUES ('role', :r, 'list_tables')",
        r=role,
    )

    assert [t.name for t in await stack.schema.list_tables(caller, "shop-pg")] == ["orders"]
    with pytest.raises(ToolNotPermitted, match="run_query"):
        await stack.query.run_query(caller, "shop-pg", "SELECT id FROM orders")


async def test_connection_access_is_granted_one_connection_at_a_time(stack):
    role = f"pg-only-{uuid4().hex[:6]}"
    caller = Caller(sub=f"u-{uuid4().hex[:6]}", roles=frozenset({role}))
    pg = await connection_id(stack, "shop-pg")
    await grant(stack, "INSERT INTO connection_access VALUES (:c, 'role', :r)", c=pg, r=role)
    for tool in ("list_connections", "list_tables"):
        await grant(
            stack,
            "INSERT INTO tool_permissions (subject_type, subject_id, tool_name) VALUES ('role', :r, :t)",
            r=role,
            t=tool,
        )

    assert [c.name for c in await stack.schema.list_connections(caller)] == ["shop-pg"]
    with pytest.raises(ConnectionNotFound):
        await stack.schema.list_tables(caller, "shop-sqlite")  # exists, but not theirs


async def test_a_grant_to_one_user_adds_to_what_their_role_already_allows(stack):
    # The analyst role cannot see payments. Grant it to just this user.
    pg = await connection_id(stack, "shop-pg")
    other = Caller(sub=f"u-{uuid4().hex[:6]}", roles=frozenset({"analyst"}))
    with pytest.raises(TableNotFound):
        await stack.query.run_query(stack.caller, "shop-pg", "SELECT count(*) FROM payments")

    await grant(
        stack,
        "INSERT INTO table_permissions (connection_id, subject_type, subject_id, table_name) "
        "VALUES (:c, 'user', :u, 'payments')",
        c=pg,
        u=stack.caller.sub,
    )

    result = await stack.query.run_query(stack.caller, "shop-pg", "SELECT count(*) FROM payments")
    assert result.rows == [[64]]
    with pytest.raises(TableNotFound):  # someone else with the same role still can't
        await stack.query.run_query(other, "shop-pg", "SELECT count(*) FROM payments")


async def test_a_deactivated_connection_disappears_for_everyone(stack, sqlite_file):
    name = f"temp-{uuid4().hex[:8]}"
    await grant(
        stack,
        "INSERT INTO connections (name, engine, details) VALUES (:n, 'sqlite', CAST(:d AS jsonb))",
        n=name,
        d=f'{{"path": "{sqlite_file.as_posix()}"}}',
    )
    cid = await connection_id(stack, name)
    await grant(stack, "INSERT INTO connection_access VALUES (:c, 'role', 'analyst')", c=cid)
    assert name in [c.name for c in await stack.schema.list_connections(stack.caller)]

    await grant(stack, "UPDATE connections SET is_active = false WHERE id = :c", c=cid)
    assert name not in [c.name for c in await stack.schema.list_connections(stack.caller)]
    with pytest.raises(ConnectionNotFound):
        await stack.schema.list_tables(stack.caller, name)


# --- secrets ---------------------------------------------------------------------------------


async def test_the_database_password_is_encrypted_at_rest_and_not_in_the_details(
    stack, postgres, secret_box
):
    async with stack.admin.connect() as db:
        row = (
            await db.execute(
                text(
                    "SELECT details::text AS details, secret_encrypted FROM connections WHERE name = 'shop-pg'"
                )
            )
        ).one()

    password = postgres.passwords["ORG_READONLY"]
    assert password not in row.details
    assert password not in row.secret_encrypted
    assert secret_box.decrypt(row.secret_encrypted) == password


# --- database role boundaries (set up by db/init and the migration) ---------------------------


@pytest.mark.parametrize(
    "statement",
    [
        "INSERT INTO connections (name, engine) VALUES ('sneaky', 'postgresql')",
        "UPDATE connections SET is_active = false",
        "INSERT INTO tool_permissions (subject_type, subject_id, tool_name) VALUES ('user', 'me', 'run_query')",
        "SELECT count(*) FROM audit_log",  # the MCP server can append to the log but not read it
        "UPDATE audit_log SET success = true",
        "DELETE FROM audit_log",
    ],
)
async def test_the_mcp_servers_own_database_role_cannot_change_config_or_touch_the_audit_log(
    postgres, statement
):
    engine = create_async_engine(postgres.url("mcp_app", "app_meta"))
    try:
        async with engine.connect() as db:
            with pytest.raises(DBAPIError, match="permission denied"):
                await db.execute(text(statement))
    finally:
        await engine.dispose()


async def test_the_gui_role_can_read_the_audit_log_but_never_edit_it(stack):
    async with stack.admin.connect() as db:
        await db.execute(text("SELECT count(*) FROM audit_log"))
        for statement in ("UPDATE audit_log SET success = true", "DELETE FROM audit_log"):
            with pytest.raises(DBAPIError, match="permission denied"):
                await db.execute(text(statement))
            await db.rollback()


async def test_the_read_only_role_cannot_even_connect_to_app_meta(postgres):
    engine = create_async_engine(postgres.url("org_readonly", "app_meta"))
    try:
        with pytest.raises(Exception, match="permission denied|CONNECT"):
            async with engine.connect():
                pass
    finally:
        await engine.dispose()


async def test_the_read_only_role_has_no_write_privileges_even_with_its_read_only_default_switched_off(
    postgres,
):
    """Privileges, not just session settings, are what protect the target database."""
    engine = create_async_engine(postgres.url("org_readonly", "org_data"))
    try:
        async with engine.connect() as db:
            # Commit the setting so it applies to the transactions that follow.
            await db.execute(text("SET default_transaction_read_only = off"))
            await db.commit()
            assert (await db.execute(text("SHOW transaction_read_only"))).scalar_one() == "off"
            await db.rollback()

            for statement in (
                "DELETE FROM reviews",
                "INSERT INTO categories (id, name) VALUES (99, 'x')",
                "DROP TABLE orders",
                "CREATE TEMP TABLE t (a int)",
            ):
                with pytest.raises(DBAPIError, match="permission denied|must be owner"):
                    await db.execute(text(statement))
                await db.rollback()
    finally:
        await engine.dispose()


async def test_callers_and_their_roles_are_remembered_for_the_admin_grids(stack):
    role = f"seen-{uuid4().hex[:6]}"
    caller = Caller(sub=f"u-{uuid4().hex[:6]}", name="Seen Person", roles=frozenset({role}))
    with pytest.raises(
        ToolNotPermitted
    ):  # no grants at all, but the attempt still counts as "seen"
        await stack.schema.list_connections(caller)

    async with stack.admin.connect() as db:
        rows = (
            await db.execute(
                text(
                    "SELECT subject_type, subject_id, display_name FROM known_subjects "
                    "WHERE subject_id IN (:u, :r) ORDER BY subject_type DESC"
                ),
                {"u": caller.sub, "r": role},
            )
        ).all()
    assert [tuple(r) for r in rows] == [
        ("user", caller.sub, "Seen Person"),
        ("role", role, None),
    ]


async def test_the_identity_providers_built_in_roles_are_not_offered_as_grid_rows(stack):
    caller = Caller(
        sub=f"u-{uuid4().hex[:6]}",
        roles=frozenset({"default-roles-shop", "offline_access", "uma_authorization", "finance"}),
    )
    with pytest.raises(ToolNotPermitted):
        await stack.schema.list_connections(caller)

    async with stack.admin.connect() as db:
        roles = (
            await db.execute(
                text(
                    "SELECT subject_id FROM known_subjects WHERE subject_type = 'role' "
                    "AND subject_id IN ('default-roles-shop', 'offline_access', "
                    "'uma_authorization', 'finance')"
                )
            )
        ).scalars()
        assert set(roles) == {"finance"}
