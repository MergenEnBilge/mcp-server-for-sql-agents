import { act, renderHook, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";

import type { Agent, AgentOptions } from "../../api/types";
import { fakeApi, renderScreen } from "../../test/render";
import { AgentsPage } from "./AgentsPage";
import { PendingAgentPrompt } from "./PendingAgentPrompt";
import { POLL_MS, usePendingAgents } from "./usePendingAgents";

const options: AgentOptions = {
  tools: [
    { name: "list_tables", description: "List the tables" },
    { name: "run_query", description: "Run queries" },
  ],
  presets: { explorer: ["list_tables"], analyst: ["list_tables", "run_query"] },
  connections: [
    { id: "c-1", name: "shop-pg", engine: "postgresql" },
    { id: "c-2", name: "shop-sqlite", engine: "sqlite" },
  ],
};

const agent = (extra: Partial<Agent> = {}): Agent => ({
  id: "a-1",
  client_id: "chat-abc",
  label: "",
  reported_name: "ChatApp 2.0",
  state: "pending",
  allowed_tools: [],
  all_connections: true,
  connections: [],
  expires_at: null,
  first_seen_at: "2026-09-21T10:00:00Z",
  last_seen_at: "2026-09-21T10:00:00Z",
  last_user_sub: "u-pat",
  last_user_name: "Pat",
  decided_by: null,
  decided_at: null,
  calls_24h: 0,
  ...extra,
});

describe("the pop-up for a new agent", () => {
  it("says who is asking, and that what it calls itself is not verified", async () => {
    const { api } = fakeApi({ "GET /agents/options": options });
    renderScreen(<PendingAgentPrompt agents={[agent()]} onDecided={() => {}} />, api);

    const dialog = await screen.findByRole("dialog", { name: "A new agent wants to connect" });
    expect(dialog).toHaveTextContent("ChatApp 2.0");
    expect(dialog).toHaveTextContent("not verified");
    expect(dialog).toHaveTextContent("chat-abc");
    expect(dialog).toHaveTextContent("Pat");
    expect(dialog).toHaveTextContent("it can’t do anything");
  });

  it("stays out of the way when nobody is waiting", () => {
    const { api, calls } = fakeApi({});
    renderScreen(<PendingAgentPrompt agents={[]} onDecided={() => {}} />, api);
    expect(screen.queryByRole("dialog")).toBeNull();
    expect(calls).toHaveLength(0);
  });

  it("starts from the safest choice: schema only, for 30 days", async () => {
    const post = vi.fn();
    const decided = vi.fn();
    const { api } = fakeApi({ "GET /agents/options": options, "POST /agents/a-1/approve": (_p: unknown, body: unknown) => (post(body), agent({ state: "approved" })) });
    renderScreen(<PendingAgentPrompt agents={[agent()]} onDecided={decided} />, api);

    await userEvent.click(await screen.findByRole("button", { name: "Allow" }));
    await waitFor(() => expect(decided).toHaveBeenCalled());
    expect(post).toHaveBeenCalledWith({ label: "", tools: ["list_tables"], all_connections: true, connection_ids: [], expires_in_hours: 720 });
  });

  it("sends exactly what the administrator picked", async () => {
    const post = vi.fn();
    const { api } = fakeApi({ "GET /agents/options": options, "POST /agents/a-1/approve": (_p: unknown, body: unknown) => (post(body), agent()) });
    renderScreen(<PendingAgentPrompt agents={[agent()]} onDecided={() => {}} />, api);
    await screen.findByRole("dialog");

    await userEvent.type(screen.getByLabelText(/^Name/), "Pat's chatbot");
    await userEvent.click(screen.getByRole("radio", { name: /Full read access/ }));
    await userEvent.click(screen.getByRole("radio", { name: "Only these" }));
    await userEvent.click(screen.getByRole("checkbox", { name: "shop-sqlite" }));
    await userEvent.selectOptions(screen.getByLabelText("How long"), "1 day");
    await userEvent.click(screen.getByRole("button", { name: "Allow" }));

    await waitFor(() => expect(post).toHaveBeenCalled());
    expect(post.mock.calls[0]![0]).toEqual({
      label: "Pat's chatbot",
      tools: ["list_tables", "run_query"],
      all_connections: false,
      connection_ids: ["c-2"],
      expires_in_hours: 24,
    });
  });

  it("won't allow an agent with no databases or no tools chosen", async () => {
    const { api } = fakeApi({ "GET /agents/options": options });
    renderScreen(<PendingAgentPrompt agents={[agent()]} onDecided={() => {}} />, api);
    await screen.findByRole("dialog");

    await userEvent.click(screen.getByRole("radio", { name: "Only these" }));
    expect(screen.getByRole("button", { name: "Allow" })).toBeDisabled();
    await userEvent.click(screen.getByRole("checkbox", { name: "shop-pg" }));
    expect(screen.getByRole("button", { name: "Allow" })).toBeEnabled();
    await userEvent.click(screen.getByRole("radio", { name: "Choose tools" }));
    await userEvent.click(screen.getByRole("checkbox", { name: "list_tables" })); // untick the only tool
    expect(screen.getByRole("button", { name: "Allow" })).toBeDisabled();
  });

  it("blocks with one click", async () => {
    const block = vi.fn();
    const { api } = fakeApi({ "GET /agents/options": options, "POST /agents/a-1/block": () => (block(), agent({ state: "blocked" })) });
    renderScreen(<PendingAgentPrompt agents={[agent()]} onDecided={() => {}} />, api);
    await userEvent.click(await screen.findByRole("button", { name: "Block" }));
    await waitFor(() => expect(block).toHaveBeenCalled());
  });

  it("lets the decision wait, without deciding anything", async () => {
    const { api, calls } = fakeApi({ "GET /agents/options": options });
    renderScreen(<PendingAgentPrompt agents={[agent()]} onDecided={() => {}} />, api);
    await userEvent.click(await screen.findByRole("button", { name: "Decide later" }));
    await waitFor(() => expect(screen.queryByRole("dialog")).toBeNull());
    expect(calls.filter((c) => c.method === "POST")).toHaveLength(0);
  });

  it("shows how many are waiting, and the next one after the first is dealt with", async () => {
    const { api } = fakeApi({ "GET /agents/options": options });
    const second = agent({ id: "a-2", client_id: "chat-two", reported_name: "Other" });
    renderScreen(<PendingAgentPrompt agents={[agent(), second]} onDecided={() => {}} />, api);
    expect(await screen.findByText("1 of 2 waiting for a decision.")).toBeInTheDocument();
    await userEvent.click(screen.getByRole("button", { name: "Decide later" }));
    expect(await screen.findByRole("dialog")).toHaveTextContent("chat-two");
  });

  it("shows the reason if the decision doesn't go through", async () => {
    const { api } = fakeApi({
      "GET /agents/options": options,
      "POST /agents/a-1/approve": () => {
        throw new Error("That agent doesn't exist (it may have been removed).");
      },
    });
    renderScreen(<PendingAgentPrompt agents={[agent()]} onDecided={() => {}} />, api);
    await userEvent.click(await screen.findByRole("button", { name: "Allow" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("may have been removed");
  });
});

describe("the agents screen", () => {
  const approved = agent({
    id: "a-2",
    client_id: "chat-two",
    label: "Nightly report",
    state: "approved",
    allowed_tools: ["list_tables", "run_query"],
    expires_at: "2026-10-21T10:00:00Z",
    decided_by: "admin-1",
    decided_at: "2026-09-21T10:05:00Z",
    calls_24h: 12,
  });
  const routes = (extra: Record<string, unknown> = {}) => ({ "GET /agents": [agent(), approved], "GET /agents/options": options, ...extra });

  it("lists agents with their state in words and what each may do", async () => {
    const { api } = fakeApi(routes());
    renderScreen(<AgentsPage />, api);
    const table = await screen.findByRole("table", { name: "Agents" });
    const rows = table.querySelectorAll("tbody tr");
    expect(rows[0]).toHaveTextContent("ChatApp 2.0");
    expect(rows[0]).toHaveTextContent("Waiting for a decision");
    expect(rows[1]).toHaveTextContent("Nightly report");
    expect(rows[1]).toHaveTextContent("Approved");
    expect(rows[1]).toHaveTextContent("2 tools, all databases");
    expect(rows[1]).toHaveTextContent("12");
  });

  it("opens an approved agent on the choices it was given", async () => {
    const { api } = fakeApi(routes());
    renderScreen(<AgentsPage />, api);
    await userEvent.click(await screen.findByRole("button", { name: "Nightly report" }));
    expect(screen.getByRole("radio", { name: /Full read access/ })).toBeChecked();
    expect(screen.getByLabelText(/^Name/)).toHaveValue("Nightly report");
    expect(screen.getByRole("button", { name: "Save changes" })).toBeInTheDocument();
  });

  it("saves changed limits", async () => {
    const post = vi.fn();
    const { api } = fakeApi(routes({ "POST /agents/a-2/approve": (_p: unknown, body: unknown) => (post(body), approved) }));
    renderScreen(<AgentsPage />, api);
    await userEvent.click(await screen.findByRole("button", { name: "Nightly report" }));
    await userEvent.click(screen.getByRole("radio", { name: /Schema only/ }));
    await userEvent.click(screen.getByRole("button", { name: "Save changes" }));
    await waitFor(() => expect(post).toHaveBeenCalled());
    expect(post.mock.calls[0]![0]).toMatchObject({ tools: ["list_tables"], label: "Nightly report" });
    expect(await screen.findByRole("status")).toHaveTextContent("uses the new limits");
  });

  it("asks before blocking, and says what happens", async () => {
    const block = vi.fn();
    const { api } = fakeApi(routes({ "POST /agents/a-2/block": () => (block(), { ...approved, state: "blocked" }) }));
    renderScreen(<AgentsPage />, api);
    await userEvent.click(await screen.findByRole("button", { name: "Nightly report" }));
    await userEvent.click(screen.getByRole("button", { name: "Block…" }));
    expect(block).not.toHaveBeenCalled();
    expect(screen.getByRole("dialog", { name: /Block Nightly report/ })).toHaveTextContent("refused everything");
    await userEvent.click(screen.getByRole("button", { name: "Block agent" }));
    await waitFor(() => expect(block).toHaveBeenCalled());
  });

  it("removes an agent only after confirmation", async () => {
    const del = vi.fn();
    const { api } = fakeApi(routes({ "DELETE /agents/a-2": () => del() }));
    renderScreen(<AgentsPage />, api);
    await userEvent.click(await screen.findByRole("button", { name: "Nightly report" }));
    await userEvent.click(screen.getByRole("button", { name: "Remove…" }));
    expect(del).not.toHaveBeenCalled();
    await userEvent.click(screen.getByRole("button", { name: "Remove agent" }));
    await waitFor(() => expect(del).toHaveBeenCalled());
  });

  it("approves an agent before it has connected", async () => {
    const post = vi.fn();
    const { api } = fakeApi(routes({ "POST /agents": (_p: unknown, body: unknown) => (post(body), agent({ id: "a-9", client_id: "nightly-job", state: "approved" })) }));
    renderScreen(<AgentsPage />, api);
    await userEvent.click(await screen.findByRole("button", { name: "Approve in advance" }));
    expect(screen.getByRole("button", { name: "Approve" })).toBeDisabled(); // no client id yet
    await userEvent.type(screen.getByLabelText(/^Client id/), "nightly-job");
    await userEvent.click(screen.getByRole("button", { name: "Approve" }));
    await waitFor(() => expect(post).toHaveBeenCalled());
    expect(post.mock.calls[0]![0]).toMatchObject({ client_id: "nightly-job", tools: ["list_tables"] });
  });

  it("explains itself when nobody has connected yet", async () => {
    const { api } = fakeApi({ "GET /agents": [], "GET /agents/options": options });
    renderScreen(<AgentsPage />, api);
    expect(await screen.findByText(/No agent has connected yet/)).toBeInTheDocument();
  });
});

describe("checking for new agents", () => {
  afterEach(() => vi.useRealTimers());

  it("asks straight away and then every few seconds, but not while the tab is hidden", async () => {
    vi.useFakeTimers();
    const { api, calls } = fakeApi({ "GET /agents/pending": { count: 1, agents: [agent()] } });
    const { result } = renderHook(() => usePendingAgents(api, true));
    await act(async () => {});
    expect(result.current.agents).toHaveLength(1);
    expect(calls).toHaveLength(1);

    await act(async () => void vi.advanceTimersByTime(POLL_MS));
    expect(calls).toHaveLength(2);

    Object.defineProperty(document, "hidden", { configurable: true, value: true });
    await act(async () => void vi.advanceTimersByTime(POLL_MS * 3));
    expect(calls).toHaveLength(2);
    Object.defineProperty(document, "hidden", { configurable: true, value: false });
  });

  it("does nothing for someone who isn't an administrator", async () => {
    const { api, calls } = fakeApi({});
    renderHook(() => usePendingAgents(api, false));
    await act(async () => {});
    expect(calls).toHaveLength(0);
  });

  it("keeps showing what it last knew if a check fails", async () => {
    vi.useFakeTimers();
    let fail = false;
    const { api } = fakeApi({
      "GET /agents/pending": () => {
        if (fail) throw new Error("offline");
        return { count: 1, agents: [agent()] };
      },
    });
    const { result } = renderHook(() => usePendingAgents(api, true));
    await act(async () => {});
    fail = true;
    await act(async () => void vi.advanceTimersByTime(POLL_MS));
    expect(result.current.agents).toHaveLength(1);
  });
});
