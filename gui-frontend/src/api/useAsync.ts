import { useCallback, useEffect, useRef, useState, type DependencyList } from "react";

export interface AsyncState<T> {
  data: T | undefined;
  error: string | undefined;
  loading: boolean;
  /** Load again (e.g. after saving), keeping the current data on screen meanwhile. */
  reload: () => void;
}

/**
 * Run an async loader whenever `deps` change and expose its state.
 *
 * Late answers from an outdated request are ignored, so quickly changing a filter can never leave
 * the screen showing the result of an earlier one.
 */
export function useAsync<T>(load: (signal: AbortSignal) => Promise<T>, deps: DependencyList): AsyncState<T> {
  const [state, setState] = useState<{ data?: T; error?: string; loading: boolean }>({ loading: true });
  const [tick, setTick] = useState(0);
  const latest = useRef(load);
  latest.current = load;

  useEffect(() => {
    const controller = new AbortController();
    setState((s) => ({ ...s, loading: true, error: undefined }));
    latest
      .current(controller.signal)
      .then((data) => {
        if (!controller.signal.aborted) setState({ data, loading: false });
      })
      .catch((e: unknown) => {
        if (controller.signal.aborted) return;
        setState((s) => ({ ...s, error: e instanceof Error ? e.message : "Something went wrong.", loading: false }));
      });
    return () => controller.abort();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [...deps, tick]);

  const reload = useCallback(() => setTick((t) => t + 1), []);
  return { data: state.data, error: state.error, loading: state.loading, reload };
}
