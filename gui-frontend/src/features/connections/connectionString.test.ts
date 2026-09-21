import { describe, expect, it } from "vitest";

import { formatOptions, parseConnectionString, parseOptions } from "./connectionString";

describe("parseConnectionString", () => {
  it("splits a PostgreSQL URL into fields, decoding what was escaped", () => {
    expect(parseConnectionString("postgresql://read%40er:p%3Ass%2Fw@db.internal:5433/my%20shop?ssl=require&application_name=app")).toEqual({
      engine: "postgresql",
      host: "db.internal",
      port: "5433",
      database: "my shop",
      username: "read@er",
      password: "p:ss/w",
      path: "",
      options: "ssl=require\napplication_name=app",
    });
  });

  it.each([
    ["postgres://u:p@h/d", "postgresql"],
    ["postgresql+asyncpg://u:p@h/d", "postgresql"],
    ["mysql+aiomysql://u:p@h/d", "mysql"],
    ["mariadb://u:p@h/d", "mysql"],
    ["mssql+pyodbc://u:p@h/d", "mssql"],
    ["sqlserver://u:p@h/d", "mssql"],
  ])("recognises %s", (text, engine) => {
    expect(parseConnectionString(text)).toMatchObject({ engine });
  });

  it("leaves the port empty when there isn't one, so the engine's default is used", () => {
    expect(parseConnectionString("postgresql://u:p@h/d")).toMatchObject({ port: "" });
  });

  it("reads IPv6 hosts without their brackets", () => {
    expect(parseConnectionString("postgresql://u:p@[::1]:5432/d")).toMatchObject({ host: "::1", port: "5432" });
  });

  it("follows SQLAlchemy's slash rule for SQLite files", () => {
    expect(parseConnectionString("sqlite:///shop.db")).toMatchObject({ engine: "sqlite", path: "shop.db" });
    expect(parseConnectionString("sqlite:////data/shop.db")).toMatchObject({ path: "/data/shop.db" });
  });

  it("copes with no password and no user", () => {
    expect(parseConnectionString("postgresql://h/d")).toMatchObject({ username: "", password: "" });
  });

  it.each([
    ["", /doesn't look like a connection string/],
    ["host=h dbname=d", /doesn't look like a connection string/],
    ["oracle://u:p@h/d", /isn't an engine/],
    ["postgresql://", /has no host/],
  ])("explains why %j can't be used", (text, message) => {
    const result = parseConnectionString(text);
    expect("error" in result && result.error).toMatch(message);
  });
});

describe("driver options as text", () => {
  it("round-trips name=value lines and ignores blanks and lines without a name", () => {
    expect(parseOptions("ssl=require\n\n =nothing\nTrustServerCertificate = yes\r\nplain")).toEqual({ ssl: "require", TrustServerCertificate: "yes" });
    expect(formatOptions({ ssl: "require", n: 1 })).toBe("ssl=require\nn=1");
    expect(formatOptions(undefined)).toBe("");
  });
});
