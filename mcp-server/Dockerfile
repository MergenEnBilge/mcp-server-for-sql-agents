# Build from the repository root:  docker build -f mcp-server/Dockerfile -t mcp-sql-server .
#
# EXTRAS picks optional target-database drivers. MySQL/MariaDB (aiomysql) is pure Python and
# included by default. SQL Server (EXTRAS=mssql) also needs Microsoft's ODBC driver installed in
# the image, which this file doesn't do; see mcp-server/README.md.
FROM python:3.12-slim AS build
WORKDIR /src
COPY mcp-server/pyproject.toml ./
COPY mcp-server/mcp_sql_server ./mcp_sql_server
ARG EXTRAS=mysql
RUN pip install --no-cache-dir --prefix=/install ".[${EXTRAS}]"

FROM python:3.12-slim
RUN useradd --system --uid 10001 --no-create-home app
COPY --from=build /install /usr/local
USER app
ENV PYTHONUNBUFFERED=1 MCP_HTTP_HOST=0.0.0.0 MCP_HTTP_PORT=8000
EXPOSE 8000
HEALTHCHECK --interval=15s --timeout=4s --start-period=20s --retries=5 \
  CMD ["python", "-c", "import urllib.request as u; u.urlopen('http://127.0.0.1:8000/healthz', timeout=3)"]
CMD ["python", "-m", "mcp_sql_server", "--transport", "http"]
