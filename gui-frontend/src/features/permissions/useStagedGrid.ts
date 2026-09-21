import { useCallback, useState } from "react";

import { effective, revocations, stage, type Pending } from "./staging";

/**
 * Everything a grid screen needs about staged changes: the pending map, how to stage cells, and the
 * save flow (confirm if anything is being revoked, save, report). Screens only supply how to
 * *read* what is saved, how to *write* changes, and how to describe a revocation in words.
 */
export function useStagedGrid({
  saved,
  write,
  describeRevocation,
  onSaved,
}: {
  saved: (row: string, col: string) => boolean;
  write: (pending: Pending) => Promise<void>;
  describeRevocation: (row: string, cols: string[]) => string;
  onSaved: () => void;
}) {
  const [pending, setPending] = useState<Pending>(new Map());
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [savedMessage, setSavedMessage] = useState<string | null>(null);
  const [confirming, setConfirming] = useState(false);

  const set = useCallback(
    (cells: { row: string; col: string }[], granted: boolean) => {
      setSavedMessage(null);
      setError(null);
      setPending((current) => {
        let next = current;
        for (const { row, col } of cells) next = stage(next, `${row}\u0000${col}`, saved(row, col), granted);
        return next;
      });
    },
    [saved],
  );

  const doSave = useCallback(async () => {
    setBusy(true);
    setError(null);
    const count = pending.size;
    try {
      await write(pending);
      setPending(new Map());
      setConfirming(false);
      setSavedMessage(`Saved ${count} ${count === 1 ? "change" : "changes"}. They are in effect now.`);
      onSaved();
    } catch (e) {
      setConfirming(false);
      setError(e instanceof Error ? e.message : "Saving failed.");
    } finally {
      setBusy(false);
    }
  }, [pending, write, onSaved]);

  const requestSave = useCallback(() => {
    if (revocations(pending).size > 0) setConfirming(true);
    else void doSave();
  }, [pending, doSave]);

  const discard = useCallback(() => {
    setPending(new Map());
    setError(null);
  }, []);

  return {
    pending,
    set,
    busy,
    error,
    savedMessage,
    confirming,
    cancelConfirm: () => setConfirming(false),
    requestSave,
    confirmSave: doSave,
    discard,
    revokedLines: [...revocations(pending)].map(([row, cols]) => describeRevocation(row, cols)),
    isOn: (row: string, col: string) => effective(pending, `${row}\u0000${col}`, saved(row, col)),
  };
}
