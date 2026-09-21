/** What a pasted connection string tells us, split into the console's fields. */
export interface ParsedConnection {
  engine: "postgresql" | "mysql" | "mssql" | "sqlite";
  host: string;
  port: string;
  database: string;
  username: string;
  /** Goes to the write-only password field, never into the plain settings. */
  password: string;
  path: string;
  /** Driver options as `key=value` lines. */
  options: string;
}

const ENGINES: Record<string, ParsedConnection["engine"]> = {
  postgres: "postgresql",
  postgresql: "postgresql",
  mysql: "mysql",
  mariadb: "mysql",
  mssql: "mssql",
  sqlserver: "mssql",
  sqlite: "sqlite",
};

const decode = (value: string) => {
  try {
    return decodeURIComponent(value);
  } catch {
    return value;
  }
};

/**
 * Understand `postgresql://user:pass@host:5432/db?ssl=require` (and the `+driver` spellings that
 * SQLAlchemy uses, such as `mysql+aiomysql://`). Returns a sentence to show if it can't.
 */
export function parseConnectionString(input: string): ParsedConnection | { error: string } {
  const text = input.trim();
  const scheme = /^([a-z][a-z0-9.+-]*):\/\//i.exec(text)?.[1]?.toLowerCase();
  if (!scheme) return { error: "That doesn't look like a connection string. It should start with something like postgresql://" };
  const engine = ENGINES[scheme.split("+")[0]!];
  if (!engine) return { error: `'${scheme}' isn't an engine this console knows. Use postgresql, mysql, mssql or sqlite.` };

  let url: URL;
  try {
    url = new URL(text);
  } catch {
    return { error: "That connection string couldn't be read. Check it for typos." };
  }

  const options = [...url.searchParams.entries()].map(([key, value]) => `${key}=${value}`).join("\n");
  if (engine === "sqlite") {
    // sqlite:///relative.db has three slashes; sqlite:////absolute/path.db has four.
    return { engine, host: "", port: "", database: "", username: "", password: "", path: decode(url.pathname.slice(1)), options: "" };
  }
  if (!url.hostname) return { error: "The connection string has no host. It should look like postgresql://user:password@host/database" };
  return {
    engine,
    host: url.hostname.replace(/^\[|\]$/g, ""),
    port: url.port,
    database: decode(url.pathname.replace(/^\//, "")),
    username: decode(url.username),
    password: decode(url.password),
    path: "",
    options,
  };
}

/** `key=value` lines to the object the API stores. Blank lines are ignored. */
export function parseOptions(text: string): Record<string, string> {
  const result: Record<string, string> = {};
  for (const line of text.split(/\r?\n/)) {
    const at = line.indexOf("=");
    const key = at > 0 ? line.slice(0, at).trim() : "";
    if (key) result[key] = line.slice(at + 1).trim();
  }
  return result;
}

/** The other direction, to show what is stored. */
export function formatOptions(options: unknown): string {
  if (!options || typeof options !== "object") return "";
  return Object.entries(options as Record<string, unknown>)
    .map(([key, value]) => `${key}=${String(value)}`)
    .join("\n");
}
