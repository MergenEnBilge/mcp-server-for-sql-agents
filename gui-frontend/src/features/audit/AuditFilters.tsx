import { useEffect, useRef, useState } from "react";

import type { AuditFacets } from "../../api/types";
import { hasFilters, type AuditParams } from "./params";

/**
 * One line of filters that reads left to right like a query: "user, tool, database, outcome,
 * dates, text". Free-text boxes wait for a pause in typing before they change the results.
 */
export function AuditFilters({
  params,
  facets,
  onChange,
  onClear,
  searchRef,
}: {
  params: AuditParams;
  facets: AuditFacets | undefined;
  onChange: (change: Partial<AuditParams>) => void;
  onClear: () => void;
  searchRef: React.RefObject<HTMLInputElement>;
}) {
  const select = (label: string, key: "tool" | "connection" | "table", options: string[], any: string) => (
    <label>
      <span className="visually-hidden">{label}</span>
      <select className="select" aria-label={label} value={params[key]} onChange={(e) => onChange({ [key]: e.target.value })}>
        <option value="">{any}</option>
        {options.map((o) => (
          <option key={o} value={o}>
            {o}
          </option>
        ))}
      </select>
    </label>
  );

  return (
    <form className="filters" role="search" aria-label="Filter the audit log" onSubmit={(e) => e.preventDefault()}>
      <DebouncedInput className="input" label="User" placeholder="User name or id" value={params.user} onCommit={(user) => onChange({ user })} />
      {select("Tool", "tool", facets?.tools ?? [], "Any tool")}
      {select("Database", "connection", facets?.connections ?? [], "Any database")}
      {select("Table", "table", facets?.tables ?? [], "Any table")}
      <label>
        <span className="visually-hidden">Outcome</span>
        <select className="select" aria-label="Outcome" value={params.outcome} onChange={(e) => onChange({ outcome: e.target.value as AuditParams["outcome"] })}>
          <option value="">Any outcome</option>
          <option value="ok">Succeeded</option>
          <option value="failed">Failed</option>
        </select>
      </label>
      <label>
        <span className="muted">From</span>
        <input className="input" type="date" aria-label="From date" value={params.from} max={params.to || undefined} onChange={(e) => onChange({ from: e.target.value })} />
      </label>
      <label>
        <span className="muted">to</span>
        <input className="input" type="date" aria-label="To date" value={params.to} min={params.from || undefined} onChange={(e) => onChange({ to: e.target.value })} />
      </label>
      <div className="grow">
        <DebouncedInput
          inputRef={searchRef}
          className="input"
          style={{ width: "100%" }}
          label="Search"
          placeholder="Search SQL and errors   ( / )"
          value={params.q}
          onCommit={(q) => onChange({ q })}
        />
      </div>
      {hasFilters(params) && (
        <button type="button" className="btn btn-quiet" onClick={onClear}>
          Clear filters
        </button>
      )}
    </form>
  );
}

function DebouncedInput({
  value,
  onCommit,
  label,
  placeholder,
  className,
  style,
  inputRef,
}: {
  value: string;
  onCommit: (value: string) => void;
  label: string;
  placeholder: string;
  className: string;
  style?: React.CSSProperties;
  inputRef?: React.RefObject<HTMLInputElement>;
}) {
  const [draft, setDraft] = useState(value);
  const timer = useRef<ReturnType<typeof setTimeout>>();

  // Follow the address bar when it changes from elsewhere (e.g. "Clear filters").
  useEffect(() => setDraft(value), [value]);
  useEffect(() => () => clearTimeout(timer.current), []);

  return (
    <input
      ref={inputRef}
      className={className}
      style={style}
      type="search"
      aria-label={label}
      placeholder={placeholder}
      value={draft}
      onChange={(e) => {
        setDraft(e.target.value);
        clearTimeout(timer.current);
        timer.current = setTimeout(() => onCommit(e.target.value), 300);
      }}
      onKeyDown={(e) => {
        if (e.key === "Enter") {
          clearTimeout(timer.current);
          onCommit(draft);
        }
      }}
    />
  );
}
