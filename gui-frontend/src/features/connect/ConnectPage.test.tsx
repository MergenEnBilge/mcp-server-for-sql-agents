import { screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";

import type { ServerInfo } from "../../api/types";
import { fakeApi, renderScreen } from "../../test/render";
import { clientGuides } from "./clientGuides";
import { ConnectPage } from "./ConnectPage";

const info = (extra: Partial<ServerInfo> = {}): ServerInfo => ({
  mcp_url: "https://mcp.example.com/mcp",
  guide_url: "https://mcp.example.com/agent-guide",
  issuer: "https://mcp.example.com/auth/realms/sql-data-layer",
  secure: true,
  ...extra,
});

describe("connect screen", () => {
  it("shows the server's address and what happens on first connection", async () => {
    const { api } = fakeApi({ "GET /server-info": info() });
    renderScreen(<ConnectPage />, api);
    expect(await screen.findByLabelText("server address")).toHaveTextContent("https://mcp.example.com/mcp");
    expect(screen.getByText(/A pop-up appears here/)).toBeInTheDocument();
    expect(screen.getByText(/An approval is a ceiling/)).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "https://mcp.example.com/agent-guide" })).toBeInTheDocument();
  });

  it("copies the address", async () => {
    const write = vi.fn().mockResolvedValue(undefined);
    Object.defineProperty(navigator, "clipboard", { configurable: true, value: { writeText: write } });
    const { api } = fakeApi({ "GET /server-info": info() });
    renderScreen(<ConnectPage />, api);
    await userEvent.click(await screen.findByRole("button", { name: "Copy server address" }));
    await waitFor(() => expect(write).toHaveBeenCalledWith("https://mcp.example.com/mcp"));
    expect(await screen.findByRole("button", { name: "Copy server address" })).toHaveTextContent("Copied");
  });

  it("puts the address into the setup for each client", async () => {
    const { api } = fakeApi({ "GET /server-info": info() });
    renderScreen(<ConnectPage />, api);
    await screen.findByLabelText("server address");

    await userEvent.click(screen.getByRole("button", { name: "Claude Code" }));
    expect(screen.getByLabelText("command")).toHaveTextContent("claude mcp add --transport http sql-data-layer https://mcp.example.com/mcp");

    await userEvent.click(screen.getByRole("button", { name: "VS Code / Cursor" }));
    expect(JSON.parse(screen.getByLabelText("mcp.json").textContent!)).toEqual({ servers: { "sql-data-layer": { type: "http", url: "https://mcp.example.com/mcp" } } });

    await userEvent.click(screen.getByRole("button", { name: "Anything else" }));
    expect(screen.getByLabelText("discovery address")).toHaveTextContent("https://mcp.example.com/.well-known/oauth-protected-resource/mcp");
  });

  it("warns that a cloud chatbot can't use a plain http address", async () => {
    const { api } = fakeApi({ "GET /server-info": info({ mcp_url: "http://localhost:8000/mcp", secure: false }) });
    renderScreen(<ConnectPage />, api);
    expect(await screen.findByText(/not HTTPS/)).toBeInTheDocument();
  });

  it("says what to configure when the public address isn't set", async () => {
    const { api } = fakeApi({ "GET /server-info": info({ mcp_url: null, guide_url: null, secure: false }) });
    renderScreen(<ConnectPage />, api);
    expect(await screen.findByText(/public address isn.t set/)).toBeInTheDocument();
    expect(screen.getByText(/GUI_MCP_PUBLIC_URL/)).toBeInTheDocument();
  });
});

describe("client guides", () => {
  it("cover the clients people use and never leave the address out", () => {
    const guides = clientGuides("https://h/mcp");
    expect(guides.map((g) => g.id)).toEqual(["claude", "chatgpt", "claude-code", "editors", "other"]);
    for (const g of guides.filter((g) => g.snippet)) expect(g.snippet!.text).toContain("h/");
    for (const g of guides) expect(g.steps.length).toBeGreaterThan(1);
  });
});
