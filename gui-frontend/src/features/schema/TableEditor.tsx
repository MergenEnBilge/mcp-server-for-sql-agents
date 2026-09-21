import { useApi } from "../../api/client";
import type { DescriptionSaved, SchemaTableDetail } from "../../api/types";
import { useAsync } from "../../api/useAsync";
import { formatTimestamp, plural } from "../../components/format";
import { ErrorNote } from "../../components/ui";
import { EditableText } from "./EditableText";

/** The selected table: its own description, then one row per column. */
export function TableEditor({
  connectionId,
  connectionName,
  table,
  onChanged,
}: {
  connectionId: string;
  connectionName: string;
  table: string;
  onChanged: () => void;
}) {
  const api = useApi();
  const detail = useAsync((signal) => api.get<SchemaTableDetail>("/schema/table", { connection_id: connectionId, table }, signal), [api, connectionId, table]);

  if (detail.error) return <ErrorNote onRetry={detail.reload}>{detail.error}</ErrorNote>;
  const d = detail.data;
  if (!d) return <p className="muted" role="status">Reading {table}…</p>;

  async function save(column: string | null, description: string): Promise<DescriptionSaved> {
    const saved = await api.put<DescriptionSaved>("/schema/description", { connection_id: connectionId, table: d?.name, column, description });
    detail.reload();
    onChanged();
    return saved;
  }

  const described = d.columns.filter((c) => c.description).length;

  return (
    <section aria-label={`Table ${d.name}`}>
      <h2 className="mono" style={{ fontSize: 15 }}>
        {d.name} <span className="chip">{d.kind}</span>
      </h2>
      <p className="muted" style={{ marginBottom: 10 }}>
        On {connectionName}. {plural(d.columns.length, "column")}, {described} described.
        {d.updated_at && ` Last edited ${formatTimestamp(d.updated_at)}${d.updated_by ? ` by ${d.updated_by}` : ""}.`}
      </p>

      <h3 className="section">What this table is</h3>
      <EditableText
        value={d.description}
        label={`Description of ${d.name}`}
        placeholder="Add a description of what this table holds"
        withheldRule={d.withheld_rule}
        onSave={(text) => save(null, text)}
      />
      {d.db_comment && (
        <p className="muted" style={{ marginTop: 6 }}>
          Comment in the database itself: {d.db_comment}
        </p>
      )}
      {d.foreign_keys.length > 0 && (
        <p className="muted" style={{ marginTop: 6 }}>
          Links to{" "}
          {d.foreign_keys.map((fk, i) => (
            <span key={`${fk.to_table}-${fk.columns.join()}`}>
              {i > 0 && ", "}
              <span className="mono">{fk.to_table}</span> (via <span className="mono">{fk.columns.join(", ")}</span>)
            </span>
          ))}
          .
        </p>
      )}

      <h3 className="section">Columns</h3>
      <div className="table-wrap">
        <table className="data schema-columns" aria-label={`Columns of ${d.name}`}>
          <thead>
            <tr>
              <th scope="col">Column</th>
              <th scope="col">Type</th>
              <th scope="col">Description</th>
            </tr>
          </thead>
          <tbody>
            {d.columns.map((c) => (
              <tr key={c.name}>
                <th scope="row" className="mono">
                  {c.name}
                  {c.primary_key && <span className="chip" style={{ marginLeft: 6 }}>key</span>}
                </th>
                <td className="mono muted">
                  {c.type}
                  {!c.nullable && " · required"}
                </td>
                <td>
                  <EditableText
                    value={c.description}
                    label={`Description of ${d.name}.${c.name}`}
                    withheldRule={c.withheld_rule}
                    onSave={(text) => save(c.name, text)}
                  />
                  {c.db_comment && <div className="muted">Comment in the database: {c.db_comment}</div>}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </section>
  );
}
