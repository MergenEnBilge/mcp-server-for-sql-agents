# Build from the repository root:  docker build -f gui-backend/Dockerfile -t mcp-sql-gui-backend .
# (the API reuses the MCP server's crypto, cache and database adapters, so both packages go in.)
FROM python:3.12-slim AS build
WORKDIR /src
COPY mcp-server/pyproject.toml ./mcp-server/
COPY mcp-server/mcp_sql_server ./mcp-server/mcp_sql_server
COPY gui-backend/pyproject.toml ./gui-backend/
COPY gui-backend/gui_backend ./gui-backend/gui_backend
RUN pip install --no-cache-dir --prefix=/install ./mcp-server ./gui-backend

FROM python:3.12-slim
RUN useradd --system --uid 10001 --no-create-home app
COPY --from=build /install /usr/local
USER app
ENV PYTHONUNBUFFERED=1
EXPOSE 8100
HEALTHCHECK --interval=15s --timeout=4s --start-period=20s --retries=5 \
  CMD ["python", "-c", "import urllib.request as u; u.urlopen('http://127.0.0.1:8100/readyz', timeout=3)"]
CMD ["uvicorn", "--factory", "gui_backend.main:app_factory", "--host", "0.0.0.0", "--port", "8100"]
