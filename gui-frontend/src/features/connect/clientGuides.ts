export type ClientId = "claude" | "chatgpt" | "claude-code" | "editors" | "other";

export interface ClientGuide {
  id: ClientId;
  title: string;
  steps: string[];
  snippet?: { label: string; text: string };
  note?: string;
}

/**
 * Setup steps per kind of client, with the server's address filled in. Menu names in other
 * people's products change; the steps say what to look for rather than promising an exact path.
 */
export function clientGuides(url: string): ClientGuide[] {
  return [
    {
      id: "claude",
      title: "Claude",
      steps: [
        "Open Settings, then Connectors, and choose Add custom connector.",
        "Give it a name, and paste the server address above as its URL.",
        "Leave the client ID and secret blank: the client registers itself.",
        "Choose Connect. You are sent to the sign-in page; sign in with your account.",
        "Turn the connector on in a conversation. The first time, a pop-up appears in this console.",
      ],
      note: "On team or enterprise plans, an owner adds the connector under the organisation's connector settings first, and members then connect to it.",
    },
    {
      id: "chatgpt",
      title: "ChatGPT",
      steps: [
        "Turn on Developer mode (Settings, then Connectors, then Advanced). It is only offered on some plans.",
        "Create a connector, choose the MCP server type, and paste the server address as its URL.",
        "Choose OAuth for authentication, and leave any client ID and secret blank.",
        "Create it, and sign in with your account when asked.",
        "Use it in a chat. The first time, a pop-up appears in this console.",
      ],
      note: "Where these options sit changes from time to time. Look for custom connectors or MCP servers.",
    },
    {
      id: "claude-code",
      title: "Claude Code",
      steps: ["Run this in a terminal.", "Inside Claude Code, run /mcp and choose to authenticate. Your browser opens for sign-in."],
      snippet: { label: "command", text: `claude mcp add --transport http sql-data-layer ${url}` },
    },
    {
      id: "editors",
      title: "VS Code / Cursor",
      steps: [
        "VS Code: put this in .vscode/mcp.json (or run MCP: Add Server). Cursor: put the same server in ~/.cursor/mcp.json under mcpServers.",
        "Start the server from the editor. It opens a browser to sign in.",
      ],
      snippet: {
        label: "mcp.json",
        text: JSON.stringify({ servers: { "sql-data-layer": { type: "http", url } } }, null, 2),
      },
    },
    {
      id: "other",
      title: "Anything else",
      steps: [
        "Any MCP client that supports Streamable HTTP with OAuth 2.1 can connect. Give it the server address.",
        "It finds the sign-in details itself, from the address below, and registers itself as a client.",
        "For a client that only speaks stdio, run it through a bridge such as mcp-remote.",
      ],
      snippet: { label: "discovery address", text: url.replace(/\/mcp\/?$/, "") + "/.well-known/oauth-protected-resource/mcp" },
    },
  ];
}
