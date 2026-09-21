import { createContext, useCallback, useContext, useMemo, useState, type ReactNode } from "react";
import { toast } from "sonner";
import type { LogFormat, NormalizeResponse } from "@shared/api";
import { MIXED_SAMPLE } from "./samples";

export interface RunOptions {
  content: string;
  sourceName?: string;
  format?: LogFormat | "auto";
}

export interface LastInput {
  content: string;
  sourceName?: string;
  format: LogFormat | "auto";
}

interface NormalizerState {
  result: NormalizeResponse | null;
  loading: boolean;
  ranAt: number | null;
  lastInput: LastInput | null;
  run: (opts: RunOptions) => Promise<NormalizeResponse | null>;
  runSample: () => Promise<NormalizeResponse | null>;
  reRun: () => Promise<NormalizeResponse | null>;
  clear: () => void;
}

const Ctx = createContext<NormalizerState | null>(null);

async function post(content: string, sourceName?: string, format?: LogFormat | "auto"): Promise<NormalizeResponse | null> {
  const res = await fetch("/api/normalize", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ content, source: { name: sourceName || undefined, format: format ?? "auto" } }),
  });
  const data: NormalizeResponse = await res.json();
  if (!data.ok) {
    toast.error(data.error ?? "Normalization failed");
    return null;
  }
  return data;
}

export function NormalizerProvider({ children }: { children: ReactNode }) {
  const [result, setResult] = useState<NormalizeResponse | null>(null);
  const [loading, setLoading] = useState(false);
  const [ranAt, setRanAt] = useState<number | null>(null);
  const [lastInput, setLastInput] = useState<LastInput | null>(null);

  const run = useCallback(async (opts: RunOptions): Promise<NormalizeResponse | null> => {
    if (!opts.content.trim()) {
      toast.error("Paste or upload some log content first");
      return null;
    }
    setLoading(true);
    try {
      const data = await post(opts.content, opts.sourceName, opts.format);
      if (data) {
        setResult(data);
        setRanAt(Date.now());
        setLastInput({ content: opts.content, sourceName: opts.sourceName, format: opts.format ?? "auto" });
      }
      return data;
    } catch {
      toast.error("Request failed — is the dev server running?");
      return null;
    } finally {
      setLoading(false);
    }
  }, []);

  const runSample = useCallback(async (): Promise<NormalizeResponse | null> => {
    setLoading(true);
    try {
      const data = await post(MIXED_SAMPLE, "Sample bundle", "auto");
      if (data) {
        setResult(data);
        setRanAt(Date.now());
        setLastInput({ content: MIXED_SAMPLE, sourceName: "Sample bundle", format: "auto" });
      }
      return data;
    } catch {
      toast.error("Request failed — is the dev server running?");
      return null;
    } finally {
      setLoading(false);
    }
  }, []);

  const reRun = useCallback(async (): Promise<NormalizeResponse | null> => {
    if (!lastInput) {
      toast.error("Nothing to re-run — run an ingestion first");
      return null;
    }
    return run({ content: lastInput.content, sourceName: lastInput.sourceName, format: lastInput.format });
  }, [lastInput, run]);

  const clear = useCallback(() => {
    setResult(null);
    setRanAt(null);
  }, []);

  const value = useMemo(
    () => ({ result, loading, ranAt, lastInput, run, runSample, reRun, clear }),
    [result, loading, ranAt, lastInput, run, runSample, reRun, clear],
  );

  return <Ctx.Provider value={value}>{children}</Ctx.Provider>;
}

export function useNormalizer(): NormalizerState {
  const v = useContext(Ctx);
  if (!v) throw new Error("useNormalizer must be used within NormalizerProvider");
  return v;
}