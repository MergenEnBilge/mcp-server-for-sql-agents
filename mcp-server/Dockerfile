# Build from the repository root:  docker build -f mcp-server/Dockerfile -t mcp-sql-server .
#
# EXTRAS picks optional target-database drivers. MySQL/MariaDB (aiomysql) is pure Python and
# included by default. SQL Server needs more, so it is opt-in:
#   docker build --build-arg EXTRAS=mysql,mssql -f mcp-server/Dockerfile .
# which also installs Microsoft's ODBC driver into the image.
FROM python:3.12-slim-bookworm AS build
WORKDIR /src
COPY mcp-server/pyproject.toml ./
COPY mcp-server/mcp_sql_server ./mcp_sql_server
ARG EXTRAS=mysql
RUN pip install --no-cache-dir --prefix=/install ".[${EXTRAS}]"

FROM python:3.12-slim-bookworm
ARG EXTRAS=mysql
# SQL Server needs Microsoft's ODBC driver in the image. It is only installed when EXTRAS names
# mssql, so the default image stays small.
RUN case ",${EXTRAS}," in *,mssql,*) \
      apt-get update \
      && apt-get install -y --no-install-recommends curl gnupg ca-certificates unixodbc \
      && curl -fsSL https://packages.microsoft.com/keys/microsoft.asc \
         | gpg --dearmor -o /usr/share/keyrings/microsoft.gpg \
      && echo "deb [signed-by=/usr/share/keyrings/microsoft.gpg] https://packages.microsoft.com/debian/12/prod bookworm main" \
         > /etc/apt/sources.list.d/mssql.list \
      && apt-get update \
      && ACCEPT_EULA=Y apt-get install -y --no-install-recommends msodbcsql18 \
      && apt-get purge -y curl gnupg && apt-get autoremove -y \
      && rm -rf /var/lib/apt/lists/* ;; \
    esac
RUN useradd --system --uid 10001 --no-create-home app
COPY --from=build /install /usr/local
USER app
ENV PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1 MCP_HTTP_HOST=0.0.0.0 MCP_HTTP_PORT=8000
EXPOSE 8000
HEALTHCHECK --interval=15s --timeout=4s --start-period=20s --retries=5 \
  CMD ["python", "-c", "import urllib.request as u; u.urlopen('http://127.0.0.1:8000/healthz', timeout=3)"]
CMD ["python", "-m", "mcp_sql_server", "--transport", "http"]
