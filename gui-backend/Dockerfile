# Build from the repository root:  docker build -f gui-backend/Dockerfile -t mcp-sql-gui-backend .
# (the API reuses the MCP server's crypto, cache and database adapters, so both packages go in.)
# EXTRAS must match the MCP server's: the console tests connections with the same drivers.
FROM python:3.12-slim-bookworm AS build
WORKDIR /src
COPY mcp-server/pyproject.toml ./mcp-server/
COPY mcp-server/mcp_sql_server ./mcp-server/mcp_sql_server
COPY gui-backend/pyproject.toml ./gui-backend/
COPY gui-backend/gui_backend ./gui-backend/gui_backend
ARG EXTRAS=mysql
RUN pip install --no-cache-dir --prefix=/install "./mcp-server[${EXTRAS}]" ./gui-backend

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
ENV PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1
EXPOSE 8100
HEALTHCHECK --interval=15s --timeout=4s --start-period=20s --retries=5 \
  CMD ["python", "-c", "import urllib.request as u; u.urlopen('http://127.0.0.1:8100/readyz', timeout=3)"]
CMD ["uvicorn", "--factory", "gui_backend.main:app_factory", "--host", "0.0.0.0", "--port", "8100"]
