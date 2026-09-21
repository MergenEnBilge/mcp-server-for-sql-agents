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
