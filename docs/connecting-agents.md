# Connecting agents

How to let an AI client use this server, and how to stay in control of what it may do. If you only
want to try it, the admin console's **Connect** screen has the same steps with your server's address
filled in.

## The idea

An agent is any AI client: a chatbot, an editor, a script. Three things stand between it and your data:

1. **It signs a person in.** Every request carries an access token for a real person. There is no shared
   account.
2. **An administrator has to approve the agent.** The first time an agent connects it is *pending* and can
   do nothing. The console shows a pop-up; an administrator allows it (schema only, or full read access,
   for some databases, for a set time) or blocks it.
3. **The person's own permissions still apply.** An approval is a ceiling, not a grant. The agent can do
   what the approval and the person's permissions *both* allow, never more than either.

## Which way to connect

| You want to use it from | Use |
|---|---|
| A chatbot on the web or in an app (Claude, ChatGPT, ...) | The HTTP transport, at a public HTTPS address ([below](#online-chatbots)) |
| A tool on your own machine (Claude Code, VS Code, Cursor) | The HTTP transport; `https://localhost/mcp` works |
| A desktop app that only speaks stdio | [stdio](../mcp-server/README.md#connecting-claude-desktop-stdio), configured with a fixed identity |

stdio has no sign-in, so it has no agent to approve: whoever writes its configuration decides who the
server acts as, and it should only ever be used on your own machine.

## Online chatbots

A chatbot that runs in the cloud connects from the provider's servers, and finds everything it needs
from the address you give it. There is nothing to create in advance.

### 1. Make the server reachable

The address must be **HTTPS** and reachable from the internet.

1. Point a DNS name at the machine that runs the stack, and open ports 80 and 443.
2. Set `PUBLIC_HOST=chat-data.example.com` in `.env` and start the stack (`docker compose --env-file .env up -d --build`).
   The bundled reverse proxy (Caddy) gets a public certificate for that name by itself.
3. Run the identity provider setup again so its addresses follow the new host (the compose file does this on
   every start: the `keycloak-setup` job).

The address to give chatbots is then `https://chat-data.example.com/mcp`. The console's **Connect** screen
shows it.

Trying it on a laptop that isn't reachable from outside? Use a tool on the same machine, or put a tunnel in
front of it; a tunnel needs to forward to Caddy over HTTPS, and this hasn't been tried with every tunnel
service.

### 2. Check it from the outside

```bash
python deploy/verify_chatbot_flow.py https://chat-data.example.com/mcp some-test-user 'their password'
```

This behaves like a chatbot: it finds the server's sign-in details, registers itself as a client, signs the
user in (with the consent screen), gets a token and says hello to the MCP server. It prints each step, and
removes the throwaway client afterwards. If it says "A chatbot can connect to this server", one will.

### 3. Add it to the chatbot

- **Claude:** Settings, then Connectors, then *Add custom connector*. Paste the address. Leave the client
  ID and secret blank. Choose *Connect* and sign in. On team and enterprise plans an owner adds the
  connector first, under the organisation's connector settings.
- **ChatGPT:** turn on Developer mode (Settings, Connectors, Advanced; only some plans have it), create a
  connector of the MCP server type, paste the address, choose OAuth, sign in.
- **Claude Code:** `claude mcp add --transport http sql-data-layer https://chat-data.example.com/mcp`, then
  `/mcp` inside it to sign in.
- **VS Code / Cursor:** add the server to `.vscode/mcp.json` (`{"servers": {"sql-data-layer": {"type": "http", "url": "..."}}}`)
  or to Cursor's `mcp.json`, and start it.

Menu names in other people's products change. Look for "custom connector" or "MCP server".

### 4. What happens when it connects

1. The person is sent to the sign-in page (Keycloak) and signs in with their own account.
2. Because the chatbot registered itself just now, Keycloak shows a **consent screen** the first time.
3. The chatbot connects. If nobody has approved it, a **pop-up appears in the admin console** on whatever
   screen an administrator has open: who is asking, what it calls itself (not verified), which person it
   acts for.
4. The administrator picks what it may do and clicks *Allow*. Until then the chatbot is told, in words it
   can pass on, that an administrator has to approve it.

## Approving agents

The **Agents** screen lists every client that has connected: what it is allowed, when it was last seen and
how many calls it made today.

| Choice | Meaning |
|---|---|
| Schema only | It can learn which databases, tables and columns exist, and read the descriptions. It never sees a row of data. |
| Full read access | Everything above, plus running read-only queries and looking at sample rows. |
| Choose tools | Any combination of the eight tools. |
| Which databases | Everything the person may use, or only the ones you tick. |
| How long | An hour to 30 days, or no end date. When it ends the agent is refused until you renew it. |

You can change an approval at any time (it takes effect on the agent's next request), **block** an agent, or
**remove** it (if it connects again it is a new request). **Approve in advance** lets you allow a client id
before it has ever connected, for example a scheduled job.

Every decision is written to the admin log, and every call an agent makes is in the audit log with the
agent's client id.

Requests nobody has seen for a week stop popping up but stay in the list. A client that registered itself
and is never used again leaves a row in the list until you remove it.

## Who may register a client

Chatbots register themselves through the identity provider's dynamic client registration. Left open,
that would let any site create a client. So it is limited:

- A client may only register redirect addresses on **trusted hosts**. The default covers the well-known
  chatbots and tools on your own machine: `claude.ai`, `claude.com`, `chatgpt.com`, `platform.openai.com`,
  `vscode.dev`, `insiders.vscode.dev`, `localhost` and `127.0.0.1`. To allow another, set
  `KEYCLOAK_DCR_TRUSTED_HOSTS` in `.env` (a comma-separated list, replacing the default) and run the setup job
  again (`docker compose --env-file .env up -d keycloak-setup`).
- One address that isn't trusted is enough to refuse the whole registration.
- The person has to accept a consent screen for a newly registered client.
- **PKCE with S256** is required of every client; `plain` and no PKCE are refused.
- Refresh tokens are rotated: each use retires the old one.
- Registration stops at `KEYCLOAK_DCR_MAX_CLIENTS` (default 1000). Every chatbot that connects adds a
  client, so if you see it approaching, remove the ones you don't use in Keycloak.

## People and roles

The people who sign in live in Keycloak, and their roles decide what they may do (see the **Permissions**
screen). To add a person or a role, use Keycloak's admin console. It is not served on the public address; to
reach it, set `KEYCLOAK_EXPOSE_ADMIN=true` in `.env`, restart the proxy, do what you need at
`https://your-host/auth/admin/`, and set it back. A role you add needs to reach tokens: run the setup job
again after creating it (it attaches the app roles to the audience scope).

## When something doesn't work

| What you see | Likely cause |
|---|---|
| The chatbot says it can't connect or the address is invalid | The address isn't HTTPS, isn't reachable from the internet, or the certificate isn't valid yet. Run `verify_chatbot_flow.py` from another network. |
| Registration is refused (`Trusted Hosts` in the error) | The chatbot's redirect address isn't on the trusted list. Add its host to `KEYCLOAK_DCR_TRUSTED_HOSTS`. |
| Signing in works but the chatbot says every tool is refused | It is waiting for approval. Look for the pop-up, or open **Agents**. |
| It was approved but sees no databases | The person has no grant. Give their role access on the **Permissions** screen. The approval only ever narrows it. |
| The console won't sign you in after changing `PUBLIC_HOST` | Run the setup job again: the console's redirect address is derived from `PUBLIC_HOST`. |
| "Too many requests" | The person is over the fair-use limit (`MCP_RATE_LIMIT_PER_MINUTE`). |

## What agents are told

You don't have to write instructions for the agent. The server sends its own when a client connects, has a
`get_my_access` tool for an agent that is unsure what it may do, and offers a guide, SQL notes per engine and
an error reference as MCP resources, plus a few prompts. The same guide is public at
`https://your-host/agent-guide`. See [mcp-server/README.md](../mcp-server/README.md#what-agents-are-told).

The descriptions you write for tables and columns on the **Schema** screen are the most useful thing you can
add: they are what agents read to decide which table holds what.
