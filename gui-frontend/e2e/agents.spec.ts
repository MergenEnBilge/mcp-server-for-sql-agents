import { expect, test, type APIRequestContext } from "@playwright/test";

import { signIn } from "./helpers";

/**
 * The first-connection flow with a real identity provider: a brand-new OAuth client signs in as
 * a person and says hello to the MCP server; an administrator watching the console sees a pop-up,
 * allows it with limits, and the agent starts working within those limits.
 */

const keycloak = "/auth";
const userPassword = process.env.KEYCLOAK_DEV_USER_PASSWORD ?? "";
// A client the demo setup creates and never approves, so every run starts as a brand-new agent.
const NEW_AGENT = "e2e-new-agent";

const MCP_HEADERS = {
  "Content-Type": "application/json",
  Accept: "application/json, text/event-stream",
  "Mcp-Protocol-Version": "2025-06-18",
};

async function tokenFor(request: APIRequestContext, clientId: string) {
  const response = await request.post(`${keycloak}/realms/sql-data-layer/protocol/openid-connect/token`, {
    form: { grant_type: "password", client_id: clientId, username: "bob", password: userPassword },
  });
  expect(response.ok(), await response.text()).toBeTruthy();
  return ((await response.json()) as { access_token: string }).access_token;
}

let rpcId = 0;
async function rpc(request: APIRequestContext, token: string, method: string, params: object) {
  const response = await request.post("/mcp", {
    headers: { ...MCP_HEADERS, Authorization: `Bearer ${token}` },
    data: { jsonrpc: "2.0", id: ++rpcId, method, params },
  });
  expect(response.ok(), await response.text()).toBeTruthy();
  return (await response.json()) as { result?: { isError?: boolean; content: { text: string }[] } };
}

const callTool = (request: APIRequestContext, token: string, name: string, args: object = {}) => rpc(request, token, "tools/call", { name, arguments: args });

test("a new agent appears as a pop-up, is allowed with limits, and works within them", async ({ page, request }) => {
  test.skip(!userPassword, "needs KEYCLOAK_DEV_USER_PASSWORD");
  const clientId = NEW_AGENT;

  // The administrator is looking at the console. Start clean if an earlier run left the agent behind.
  await signIn(page, "alice", "/agents");
  const leftover = page.getByRole("table", { name: "Agents" }).getByRole("button", { name: clientId });
  if (await leftover.count()) {
    await leftover.click();
    await page.getByRole("button", { name: "Remove…" }).click();
    await page.getByRole("button", { name: "Remove agent" }).click();
    await expect(page.getByText(/Removed/)).toBeVisible();
  }
  await page.goto("/audit");

  // The agent connects as Bob. Saying hello is enough: it can't do anything yet.
  const token = await tokenFor(request, clientId);
  await rpc(request, token, "initialize", {
    protocolVersion: "2025-06-18",
    capabilities: {},
    clientInfo: { name: "Playwright Chat", version: "1.2" },
  });
  const refused = await callTool(request, token, "list_connections");
  expect(refused.result?.isError).toBe(true);
  expect(refused.result?.content[0]?.text).toContain("has not been approved yet");

  // The pop-up appears by itself, on the screen the administrator was already looking at.
  const dialog = page.getByRole("dialog", { name: "A new agent wants to connect" });
  await expect(dialog).toBeVisible({ timeout: 20_000 });
  await expect(dialog).toContainText("Playwright Chat 1.2");
  await expect(dialog).toContainText(clientId);
  await expect(dialog).toContainText("Bob Analyst");
  await expect(page.getByRole("link", { name: /Agents/ })).toContainText("1");
  await page.screenshot({ path: "test-results/agents-1-popup.png" });

  // Schema only is the default: it may learn what exists, but not read data.
  await dialog.getByLabel(/^Name/).fill("Playwright chatbot");
  await dialog.getByRole("button", { name: "Allow" }).click();
  await expect(dialog).toBeHidden();

  await expect
    .poll(async () => (await callTool(request, token, "list_connections")).result?.isError, { timeout: 15_000 })
    .toBe(false);
  const query = await callTool(request, token, "run_query", { connection_name: "shop-sqlite", sql: "SELECT 1" });
  expect(query.result?.isError).toBe(true);
  expect(query.result?.content[0]?.text).toContain("This agent is not permitted");

  // It is listed on the Agents screen, with what it was allowed.
  await page.getByRole("link", { name: /Agents/ }).click();
  const row = page.getByRole("table", { name: "Agents" }).locator("tr", { hasText: "Playwright chatbot" });
  await expect(row).toContainText("Approved");
  await expect(row).toContainText("5 tools");
  await page.screenshot({ path: "test-results/agents-2-list.png" });

  // Blocking is immediate.
  await page.getByRole("button", { name: "Playwright chatbot" }).click();
  await page.getByRole("button", { name: "Block…" }).click();
  await page.getByRole("button", { name: "Block agent" }).click();
  await expect(page.getByText(/Blocked Playwright chatbot/)).toBeVisible();
  const blocked = await callTool(request, token, "list_connections");
  expect(blocked.result?.content[0]?.text).toContain("blocked this agent");

  // Remove it, so the suite can be run again.
  await page.getByRole("button", { name: "Remove…" }).click();
  await page.getByRole("button", { name: "Remove agent" }).click();
  await expect(page.getByText(/Removed Playwright chatbot/)).toBeVisible();
});
