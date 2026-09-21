# SQL dialect notes

`list_connections` tells you the engine of each database. Write SQL for that engine. The server
doesn't translate anything.

## Rules that apply to every engine

- One statement, `SELECT` or `WITH ... SELECT`. No trailing second statement.
- Name tables as `list_tables` shows them: plain `orders` for the default schema, `sales.orders`
  for any other schema.
- No `INTO`, no `FOR UPDATE`, no `EXEC`, and no backslashes inside quoted text.
- Comments are fine (`-- note`, `/* note */`), except MySQL's `/*! ... */`, which is refused.

## PostgreSQL

- Limit rows with `LIMIT n`. Case-insensitive match with `ILIKE`.
- Cast with `CAST(x AS int)` or `x::int`. Dates: `date_trunc('month', ts)`, `now() - interval '7 days'`.
- Quote odd identifiers with double quotes: `"Order Date"`. Unquoted names fold to lower case.
- `explain_query` returns the plan with cost and estimated rows.

## MySQL / MariaDB

- `LIMIT n`. Quote identifiers with backticks: `` `order` ``.
- Dates: `DATE_FORMAT(ts, '%Y-%m')`, `NOW() - INTERVAL 7 DAY`.
- String comparison is usually case-insensitive already.
- `--` only starts a comment when a space follows it.
- `explain_query` returns the plan as a table.

## SQL Server

- Limit rows with `SELECT TOP n ...` or `ORDER BY ... OFFSET 0 ROWS FETCH NEXT n ROWS ONLY`.
  There is no `LIMIT`.
- Quote identifiers with square brackets: `[Order Date]`. Dates: `DATEADD`, `DATEDIFF`, `FORMAT`.
- `explain_query` is not available. Run the query with a small `row_limit` instead.

## SQLite

- `LIMIT n`. Dates are text: use `date()`, `datetime()`, `strftime('%Y-%m', ts)`.
- `LIKE` is case-insensitive for plain ASCII. There is no `ILIKE`.
- Column types are loose: a column declared `INTEGER` can still hold text. Check with `get_sample_rows`.
