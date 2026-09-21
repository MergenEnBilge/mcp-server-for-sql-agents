/** Shapes returned by the admin API. They mirror the backend's models. */

export interface Me {
  sub: string;
  name: string | null;
  roles: string[];
  is_admin: boolean;
}

export interface AuditItem {
  id: number;
  occurred_at: string;
  caller_sub: string;
  caller_name: string | null;
  client_id: string | null;
  tool_name: string;
  connection_name: string | null;
  tables: string[];
  sql_preview: string | null;
  sql_truncated: boolean;
  success: boolean;
  error_preview: string | null;
  row_count: number | null;
  duration_ms: number;
}

export interface AuditDetail extends AuditItem {
  arguments: Record<string, unknown>;
  sql: string | null;
  result_summary: string | null;
  error_message: string | null;
}

export interface Page<T> {
  items: T[];
  total: number;
  page: number;
  page_size: number;
}

export interface AuditFacets {
  callers: { sub: string; name: string | null }[];
  tools: string[];
  connections: string[];
  tables: string[];
}

export interface AdminLogItem {
  id: number;
  occurred_at: string;
  actor_sub: string;
  actor_name: string | null;
  action: string;
  target_type: string;
  target: string | null;
  details: Record<string, unknown>;
}

export interface Connection {
  id: string;
  name: string;
  engine: string;
  description: string;
  details: Record<string, unknown>;
  has_secret: boolean;
  is_active: boolean;
  created_at: string;
  updated_at: string;
  last_checked_at: string | null;
  last_check_ok: boolean | null;
  last_check_error: string | null;
  access_count: number;
}

export type SubjectType = "user" | "role";

export interface Subject {
  subject_type: SubjectType;
  subject_id: string;
  display_name: string | null;
  last_seen_at: string | null;
}

export interface TableGrid {
  connection_id: string;
  connection_name: string;
  reachable: boolean;
  error: string | null;
  tables: { name: string; kind: string; description: string }[];
  subjects: Subject[];
  with_connection_access: [SubjectType, string][];
  grants: { subject_type: SubjectType; subject_id: string; table: string }[];
}

export interface ToolGrid {
  tools: { name: string; description: string }[];
  subjects: Subject[];
  grants: { subject_type: SubjectType; subject_id: string; tool: string }[];
}

export interface EngineInfo {
  engine: string;
  label: string;
  default_port: number | null;
  required: string[];
  /** False when this server has no driver for the engine (see `unavailable_reason`). */
  available: boolean;
  unavailable_reason: string | null;
}

export interface ConnectionTestResult {
  ok: boolean;
  message: string;
  table_count: number | null;
  latency_ms: number | null;
}

export interface SchemaTable {
  name: string;
  kind: string;
  description: string;
  described_columns: number;
  missing: boolean;
}

export interface SchemaTableList {
  connection_id: string;
  connection_name: string;
  reachable: boolean;
  error: string | null;
  tables: SchemaTable[];
}

export interface SchemaColumn {
  name: string;
  type: string;
  nullable: boolean;
  primary_key: boolean;
  db_comment: string | null;
  description: string;
  updated_at: string | null;
  updated_by: string | null;
  withheld_rule: string | null;
}

export interface SchemaTableDetail {
  connection_id: string;
  name: string;
  kind: string;
  db_comment: string | null;
  description: string;
  updated_at: string | null;
  updated_by: string | null;
  withheld_rule: string | null;
  columns: SchemaColumn[];
  foreign_keys: { columns: string[]; to_table: string; to_columns: string[] }[];
}

export interface DescriptionSaved {
  table: string;
  column: string | null;
  description: string;
  updated_at: string;
  updated_by: string | null;
  withheld_rule: string | null;
}

export interface Check {
  state: "ok" | "down" | "not_configured";
  detail: string | null;
  latency_ms: number | null;
}

export interface Health {
  checked_at: string;
  database: Check;
  redis: Check;
  mcp_server: Check;
  pool: { size: number; in_use: number; idle: number; overflow: number } | null;
  window: { hours: number; calls: number; errors: number; error_rate: number | null; query_calls: number; p95_query_ms: number | null };
  series: { start: string; calls: number; errors: number; p95_query_ms: number | null }[];
  connections: {
    name: string;
    engine: string;
    is_active: boolean;
    last_checked_at: string | null;
    last_check_ok: boolean | null;
    last_check_error: string | null;
  }[];
}

export interface Report {
  id: string;
  name: string;
  description: string;
  connection_id: string;
  connection_name: string;
  sql: string;
  chart_config: Record<string, unknown>;
  owner_sub: string;
  owner_name: string | null;
  created_at: string;
  updated_at: string;
}

export type AgentState = "pending" | "approved" | "blocked" | "expired";

export interface Agent {
  id: string;
  client_id: string;
  label: string;
  /** What the agent called itself. Not verified: show it as a hint only. */
  reported_name: string | null;
  state: AgentState;
  allowed_tools: string[];
  all_connections: boolean;
  connections: string[];
  expires_at: string | null;
  first_seen_at: string;
  last_seen_at: string;
  last_user_sub: string | null;
  last_user_name: string | null;
  decided_by: string | null;
  decided_at: string | null;
  calls_24h: number;
}

export interface PendingAgents {
  count: number;
  agents: Agent[];
}

export interface AgentOptions {
  tools: { name: string; description: string }[];
  presets: Record<string, string[]>;
  connections: { id: string; name: string; engine: string }[];
}

/** What an administrator allows an agent. */
export interface AgentAccess {
  label: string;
  tools: string[];
  all_connections: boolean;
  connection_ids: string[];
  expires_in_hours: number | null;
}

export interface ServerInfo {
  /** Where agents connect. Null until an operator sets the public address. */
  mcp_url: string | null;
  guide_url: string | null;
  issuer: string;
  secure: boolean;
}
