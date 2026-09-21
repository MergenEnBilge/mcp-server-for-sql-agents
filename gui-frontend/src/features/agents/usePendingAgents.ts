import { useCallback, useEffect, useState } from "react";

import type { Api } from "../../api/client";
import type { Agent, PendingAgents } from "../../api/types";

/** How often the console asks whether a new agent is waiting. */
export const POLL_MS = 5000;

/**
 * The agents waiting for a decision, refreshed while the console is open so a new one can pop up
 * on whatever screen the administrator is looking at. It doesn't poll in a background tab (it
 * catches up the moment the tab is shown again), and a failed check keeps what was last known
 * rather than raising an alarm every few seconds.
 */
export function usePendingAgents(api: Api, enabled: boolean) {
  const [agents, setAgents] = useState<Agent[]>([]);

  const refresh = useCallback(async () => {
    try {
      const answer = await api.get<PendingAgents>("/agents/pending");
      setAgents(answer.agents);
    } catch {
      /* keep the last answer */
    }
  }, [api]);

  useEffect(() => {
    if (!enabled) return;
    void refresh();
    const timer = window.setInterval(() => {
      if (!document.hidden) void refresh();
    }, POLL_MS);
    const onVisible = () => {
      if (!document.hidden) void refresh();
    };
    document.addEventListener("visibilitychange", onVisible);
    return () => {
      window.clearInterval(timer);
      document.removeEventListener("visibilitychange", onVisible);
    };
  }, [enabled, refresh]);

  return { agents, refresh };
}
