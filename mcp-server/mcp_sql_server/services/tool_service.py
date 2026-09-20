"""Shared plumbing for the services behind the MCP tools."""

from mcp_sql_server.adapters.base import DBAdapter
from mcp_sql_server.models import Caller
from mcp_sql_server.services.audit_service import AuditService
from mcp_sql_server.services.connection_registry import AdapterProvider
from mcp_sql_server.services.permission_service import ConnectionAccess, PermissionService
from mcp_sql_server.services.sanitizer import OutputSanitizer


class ToolService:
    def __init__(
        self,
        permissions: PermissionService,
        adapters: AdapterProvider,
        audit: AuditService,
        sanitizer: OutputSanitizer,
    ) -> None:
        self._permissions = permissions
        self._adapters = adapters
        self._audit = audit
        self._sanitizer = sanitizer

    async def _open(
        self, caller: Caller, tool: str, connection_name: str
    ) -> tuple[ConnectionAccess, DBAdapter]:
        """Authorize the tool and connection; return the caller's access and a live adapter."""
        access = await self._permissions.open_connection(caller, tool, connection_name)
        adapter = await self._adapters.adapter_for(access.record)
        return access, adapter
