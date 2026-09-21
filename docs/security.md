# Security

What this server defends against, which standards it follows and how that was checked, what the last
review found, and what is still open. To report a problem, see [SECURITY.md](../SECURITY.md).

## What is trusted, and what isn't

| | Trusted? | Why |
|---|---|---|
| The identity provider (Keycloak) | Yes, for who a person is | It is a separate service; this server only checks its tokens. |
| An administrator | Yes, within limits | They can register databases and grant access, but can't make a database point at an arbitrary file or a cloud metadata address, and can't see a stored password. |
| A signed-in person | Only for what they were granted | Default deny at every level. |
| An AI client (agent) | **No** | It must be approved, and is capped by that approval and by the person it acts for. |
| The model's own SQL | **No** | It is parsed, restricted to one read-only `SELECT`, and the database refuses writes independently. |
| Text from a database | **No** | Descriptions, comments and cell values are data written by strangers. Text that reads like an instruction is withheld. |
| The network in front of the server | No | TLS ends at the reverse proxy; the `Host` and `Origin` of every request are checked. |

## The layers, and where each is enforced

| Layer | What it does | Tested by |
|---|---|---|
| Token check | Signature, expiry, issuer and **audience** must be right; asymmetric algorithms only; if the identity provider can't be reached, the answer is no | `test_token_verifier`, `test_http_oauth` |
| Agent approval | A new client is refused everything until an administrator approves it; an approval is a ceiling, intersected with the person's own permissions; the MCP server's database role can't approve | `test_agent_gate`, `test_agent_approval` (including that the role is refused), `test_agents`, the browser test of the pop-up |
| Permissions | Tool, connection and table grants, default deny, checked on every call; a missing and a forbidden thing look the same | `test_permissions_and_roles`, `test_schema_service`, `test_query_service` |
| SQL validation | One statement; `SELECT`/`WITH` only; found by keyword scan and by parsing; every table a query reads must be one the caller may see | `test_query_validator` (and again against real MySQL) |
| Database | Read-only transaction or session, a read-only role, a statement time limit, a row cap by streaming | `test_both_engines` (writes attempted with the service layer bypassed), `test_mysql` |
| Output | Instruction-like text withheld, invisible characters stripped, long text cut | `test_sanitizer` |
| Audit | Every call is recorded, refused ones included, with the agent that made it; if the row can't be written, the result is withheld | `test_audit`, `test_http_oauth` |
| Secrets | Connection passwords encrypted at rest, decrypted only to open a connection, never returned or logged | `test_crypto_and_serialization`, `test_connections` |
| Transport | `Host`/`Origin` checks, request size cap, per-person rate and concurrency limits, `no-store` responses | `test_http_security`, `test_ratelimit`, `test_http_oauth` |

## Standards followed

The MCP specification's authorization section builds on OAuth 2.1 and several RFCs. Each requirement, and
how it is met here:

| Requirement | Status | Where / how it was checked |
|---|---|---|
| The MCP server is an OAuth 2.1 **resource server** and validates tokens itself | Met | `auth/token_verifier.py`; expired, wrong-audience, wrong-issuer, unknown-key and garbage tokens all get 401 (`test_http_oauth`) |
| A request without a valid token gets `401` with `WWW-Authenticate` naming the resource metadata | Met | Integration test, and by the chatbot simulation against the real stack |
| **Protected Resource Metadata** (RFC 9728) at `/.well-known/oauth-protected-resource/mcp`, naming the authorization server | Met | Integration test; served with CORS by the SDK |
| **Authorization Server Metadata** discoverable (RFC 8414, or OpenID Connect discovery), including when the issuer has a path | Met | Keycloak publishes it; the reverse proxy serves the "path inserted" form at the address chatbots try first. `verify_chatbot_flow.py` finds it there |
| **Dynamic client registration** (RFC 7591), so clients without prior setup can connect | Met, restricted | Keycloak's endpoint, limited to redirect addresses on trusted hosts; refusal tested end to end |
| Tokens are bound to this server (**resource indicators**, RFC 8707): the audience must name it | Met | The server rejects tokens whose `aud` doesn't include its address. The audience is put in tokens by a scope every MCP client has, and clients also send `resource`, which Keycloak accepts |
| **PKCE** with `S256`, and refuse when it's missing | Met | Enforced by a Keycloak client policy for every client; a request with no PKCE or with `plain` is refused (`tests/e2e`) |
| Refresh tokens for public clients are rotated | Met | Realm setting applied by the setup job |
| **No token passthrough**: the server doesn't forward the token to anything | Met | The token only identifies the caller; databases are reached with their own stored credentials |
| **Origin validation** on the Streamable HTTP endpoint (DNS rebinding) | Met (was not) | See finding 1 below |
| HTTPS for all authorization endpoints | Met | TLS at the reverse proxy, HSTS on, plain HTTP redirected (308) |
| Streamable HTTP: `POST` for requests, `GET` answers with an event stream or 405, `DELETE` 405 without sessions | Met | Checked against the running server. It is stateless, which the specification allows |
| JSON-RPC errors are well formed: parse error `-32700`, unknown method `-32601`, bad parameters `-32602`; a notification gets `202` | Met | Checked against the running server |
| Wrong `Content-Type` refused; unsupported protocol version refused | Met | Checked against the running server |
| Tool results carry structured output and read-only annotations | Met | `test_mcp_tools` |
| Both the `initialize` handshake and the newer handshake-less protocol revision work | Met | The client and server in the tests negotiate the newer one; raw `initialize` is used by the browser test |

One deviation, in the SDK: a request with **no** token is answered with `error="invalid_token"` in the
`WWW-Authenticate` header, where RFC 6750 says to leave the error out when no credentials were sent. It is
harmless (clients start the sign-in flow from the metadata address, which is what the chatbot simulation does)
and is the SDK's behaviour, not something this repository sets.

## Findings from the last review

The review covered the protocol layer, the authorization model, the SQL path, the deployment files and the
dependencies. Severity is what it would have meant on a server open to the internet.

| # | Severity | Finding | Now |
|---|---|---|---|
| 1 | High | `Host`/`Origin` validation was **off** whenever the server listened on anything but loopback (the SDK only turns it on for loopback), which is how it runs in a container. A malicious web page could have used a visitor's browser to reach a server on their network. | Always on. Allowed values come from the server's own public address plus `MCP_ALLOWED_HOSTS` / `MCP_ALLOWED_ORIGINS`. Tested over HTTP. |
| 2 | High | Any OAuth client could use the server once a person signed in. Nothing said *which application* was acting. | Agent approval (above): default deny per agent, capped by the person's permissions. |
| 3 | Medium | Self-registration would have been refused for real chatbots (the trusted-host list only had `localhost`), and clients that did register would not have carried the person's **roles** in their tokens, so role-based permissions would have silently not applied. | Trusted hosts set, roles attached to the audience scope. The realm's policies showed the first; the second was confirmed by running the real chatbot flow without the fix (the token had no roles at all) and with it. Both are now covered by `tests/e2e`. |
| 4 | Medium | PKCE was not required, and `plain` was accepted. | Required and S256-only, for every client. |
| 5 | Medium | Refresh tokens were not rotated. | Rotated. |
| 6 | Medium | Keycloak's admin console and `master` realm were reachable on the public address. | Blocked at the proxy unless `KEYCLOAK_EXPOSE_ADMIN=true`. |
| 7 | Medium | A SQLite connection accepted any path, so an administrator (or someone who took over an admin session) could point the server at any SQLite file on its disk. | Files must be inside `MCP_SQLITE_ROOT`; URIs and query strings refused; symlinks that leave the folder refused. Checked when saving and again when opening. |
| 8 | Medium | No limit on how much one person could ask for. | Calls per minute (shared via Redis) and calls at once, per person. |
| 9 | Medium | The console's redirect addresses and token audiences were fixed to `localhost` in a realm file that is only read on first start, so a real deployment could not sign in without editing Keycloak by hand. | The setup job derives them from `PUBLIC_HOST` on every run. |
| 10 | Low | A connection could be pointed at link-local or cloud-metadata addresses. | Refused as hosts. Only literal addresses are checked (see below). |
| 11 | Low | Request bodies could be 4 MB. | 256 KB. |
| 12 | Low | API responses could be cached by a browser or proxy. | `Cache-Control: no-store` on every response. |
| 13 | Low | Application containers had a writable filesystem and all default capabilities. | Read-only filesystem, no capabilities, no privilege escalation. |
| 14 | Low | Advisories in `react-router` (an open redirect through a backslash in a link target) and `vitest` (a development-only file read). Neither was reachable in this app, which never builds links from user input. | Upgraded; `npm audit` reports none. |
| 15 | Low | MySQL's "access denied to database" was shown to administrators as a failed login. | Fixed; found while testing MySQL for real. |

Automated scans, run on the code as it stands:

- **bandit** (static analysis for Python): no high-severity findings. 14 medium findings about SQL built
  from strings, all reviewed: the parts that are interpolated are fixed constants (column lists, sort
  columns from a fixed set, clause fragments) and every value is a bound parameter. 11 low findings (`assert`
  used for type narrowing; the word "password" or "token" in names). No change was needed.
- **pip-audit**: no known vulnerabilities in the Python dependencies. (Its only finding is for `pip`, the
  installer in the development environment, which is not part of the application.)
- **npm audit**: none.

## What is still open

Things this server does not do, so nobody is surprised:

- **Host names are not resolved when a database is registered.** Only literal addresses are checked, so a name
  that points at an internal address is accepted. Registering and testing connections is an administrator's
  job, and "connect to this host" is what the feature is; but an administrator's account is worth protecting.
- **Rate limits are per process without Redis.** With Redis they are shared across replicas.
- **Registered clients pile up.** Every chatbot that registers itself adds an OAuth client, until
  `KEYCLOAK_DCR_MAX_CLIENTS`. They are only removed by hand.
- **Prompt-injection defence is best effort.** The sanitiser withholds text that looks like an instruction to
  an AI, and the server tells agents that database text is data. Neither can promise a model never follows
  something clever. What limits the damage is that the tools are read-only, capped, and scoped to what the
  person may see.
- **The audit log is protected by database grants, not by cryptography.** Neither service can edit or delete
  it, but someone with the database owner's password can.
- **Traffic between containers is not encrypted.** It stays on the compose network. Put the services on
  separate hosts and it needs a private network or TLS.
- **The encryption key for stored passwords is one value in the environment.** Rotation is supported (several
  keys, the first encrypts, all decrypt) but manual.
- **SQL Server is untested against a live server**, and it has no server-side read-only session, so it relies
  on the database role being read-only. Give it one.
- **No web application firewall, and no per-address rate limit for unauthenticated requests.** Token checks
  are local and cheap; put the server behind your usual edge protection if it is exposed to the internet.

## Running it for real: a checklist

- [ ] `.env` holds generated secrets (nothing left as `change-me`), and is not in version control.
- [ ] `PUBLIC_HOST` is your real host name, the certificate was issued, and `verify_chatbot_flow.py` says a
      chatbot can connect.
- [ ] The **demo profile is off** (no sample users, no test clients, no sample databases).
- [ ] `KEYCLOAK_EXPOSE_ADMIN` is `false` (its default) except while you are using it.
- [ ] People have real accounts and roles in Keycloak, and the admin role is held by as few as possible.
      Turn on multi-factor sign-in for them in Keycloak.
- [ ] Every registered database uses a **read-only database user**.
- [ ] `MCP_SQLITE_ROOT` is set only if you use SQLite, and holds only those files.
- [ ] Redis is on (permission changes and rate limits work across replicas), and has a password.
- [ ] Postgres is backed up, and so is the encryption key, kept somewhere other than the database backup.
- [ ] Someone watches the **Agents** screen, and the audit log is looked at.
- [ ] `KEYCLOAK_DCR_TRUSTED_HOSTS` lists only the chatbots and tools you actually want.
