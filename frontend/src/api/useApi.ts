import { useCallback, useEffect, useRef, useState } from "react";

export interface ApiState<T> {
  data: T | undefined;
  error: Error | undefined;
  loading: boolean;
  reload: () => void;
}

/** Runs ``load`` whenever ``deps`` change; aborts superseded requests so stale responses never win. */
export function useApi<T>(load: (signal: AbortSignal) => Promise<T>, deps: unknown[]): ApiState<T> {
  const [data, setData] = useState<T>();
  const [error, setError] = useState<Error>();
  const [loading, setLoading] = useState(true);
  const [nonce, setNonce] = useState(0);
  const loadRef = useRef(load);
  loadRef.current = load;

  useEffect(() => {
    const controller = new AbortController();
    setLoading(true);
    setError(undefined);
    loadRef
      .current(controller.signal)
      .then((result) => {
        if (!controller.signal.aborted) setData(result);
      })
      .catch((err: Error) => {
        if (!controller.signal.aborted && err.name !== "AbortError") setError(err);
      })
      .finally(() => {
        if (!controller.signal.aborted) setLoading(false);
      });
    return () => controller.abort();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [...deps, nonce]);

  const reload = useCallback(() => setNonce((n) => n + 1), []);
  return { data, error, loading, reload };
}
