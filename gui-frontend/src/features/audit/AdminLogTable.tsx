import { Fragment, useState } from "react";

import type { AdminLogItem } from "../../api/types";
import { formatFull, formatTimestamp } from "../../components/format";

/** What administrators did in this console. Newest first, each row expands to its full details. */
export function AdminLogTable({ items }: { items: AdminLogItem[] }) {
  const [openId, setOpenId] = useState<number | null>(null);

  return (
    <div className="table-wrap">
      <table className="data" aria-label="Admin changes">
        <thead>
          <tr>
            <th scope="col">Time</th>
            <th scope="col">Administrator</th>
            <th scope="col">Change</th>
            <th scope="col">Applies to</th>
            <th scope="col">Summary</th>
          </tr>
        </thead>
        <tbody>
          {items.map((item) => {
            const open = openId === item.id;
            const toggle = () => setOpenId(open ? null : item.id);
            return (
              <Fragment key={item.id}>
                <tr
                  className={`row${open ? " open" : ""}`}
                  tabIndex={0}
                  aria-expanded={open}
                  onClick={toggle}
                  onKeyDown={(e) => {
                    if (e.key === "Enter" || e.key === " ") {
                      e.preventDefault();
                      toggle();
                    }
                  }}
                >
                  <td title={formatFull(item.occurred_at)}>{formatTimestamp(item.occurred_at)}</td>
                  <td title={item.actor_sub}>{item.actor_name ?? item.actor_sub}</td>
                  <td>{item.action}</td>
                  <td className="mono">{item.target ?? "–"}</td>
                  <td className="cell-clip">{summarise(item)}</td>
                </tr>
                {open && (
                  <tr className="detail">
                    <td colSpan={5}>
                      <div className="detail-body">
                        <dl>
                          <dt>When</dt>
                          <dd className="mono">{formatFull(item.occurred_at)}</dd>
                          <dt>Who</dt>
                          <dd>
                            {item.actor_name ?? "(no name)"} <span className="mono muted">{item.actor_sub}</span>
                          </dd>
                        </dl>
                        <pre className="sql">{JSON.stringify(item.details, null, 2)}</pre>
                      </div>
                    </td>
                  </tr>
                )}
              </Fragment>
            );
          })}
        </tbody>
      </table>
    </div>
  );
}

/** A one-line reading of what changed, so the list is useful without opening every row. */
export function summarise(item: AdminLogItem): string {
  const d = item.details as { granted?: string[]; revoked?: string[]; subject?: string; changed?: string[] };
  const parts: string[] = [];
  if (d.subject) parts.push(d.subject);
  if (d.granted?.length) parts.push(`granted ${d.granted.join(", ")}`);
  if (d.revoked?.length) parts.push(`revoked ${d.revoked.join(", ")}`);
  if (d.changed?.length) parts.push(`changed ${d.changed.join(", ")}`);
  return parts.join("; ") || "(no details)";
}
