import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { Link } from "react-router-dom";
import {
  AlertTriangle,
  Ban,
  CheckCircle2,
  History,
  LifeBuoy,
  PlayCircle,
  RefreshCcw,
  Search,
  Tags,
} from "lucide-react";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { PageHeader, StatChip } from "@/components/common/bits";
import {
  isReprocessTerminal,
  listBackendDlq,
  pollReprocessRun,
  previewReprocessBatch,
  reprocessDlqBatch,
  reprocessDlqRecord,
  type BackendDlqRecord,
  type BatchDryRunResult,
  type ReprocessRun,
} from "@/lib/backend";

const PAGE_SIZE = 200;
const REASON_MAX = 280;

type Busy = false | "single" | "batch" | "preview";

function errorText(err: unknown): string {
  return err instanceof Error ? err.message : String(err);
}

function formatWhen(value?: string | null): string {
  if (!value) return "—";
  const parsed = Date.parse(value);
  return Number.isNaN(parsed) ? value : new Date(parsed).toLocaleString();
}

/**
 * Rejections that are an honest answer, not a fault in the pipeline.
 *
 * `no_recoverable_identity` means every parser was tried, including the drain3
 * fallback, and the line simply had no field the fallback could label as an
 * identity. No parser would have saved that line, so presenting it in the same
 * alarm-red as an index rejection sends an operator hunting for a broken
 * parser that does not exist. It is still a real rejection and still counted,
 * just not a defect.
 */
const BENIGN_REASONS = new Set(["no_recoverable_identity"]);

function isBenignReason(reason: string): boolean {
  return BENIGN_REASONS.has(reason);
}

/**
 * DLQ recovery control room.
 *
 * This page never parses anything. Re-parse is not a browser-side re-run of
 * the local normalizer with a line-count comparison -- that produces a
 * "recovered" verdict no pipeline ever agreed with. Instead it asks the API to
 * republish the original Bronze event onto logs.raw and then reports what the
 * orchestrator actually decided, by polling the reprocess run until it
 * reaches a terminal status.
 */
export default function Dlq() {
  const [records, setRecords] = useState<BackendDlqRecord[] | null>(null);
  const [loadError, setLoadError] = useState<string | null>(null);
  const [selected, setSelected] = useState<string[]>([]);
  const [reason, setReason] = useState("");
  const [run, setRun] = useState<ReprocessRun | null>(null);
  const [preview, setPreview] = useState<BatchDryRunResult | null>(null);
  const [busy, setBusy] = useState<Busy>(false);
  const [actionError, setActionError] = useState<string | null>(null);
  const abortRef = useRef<AbortController | null>(null);

  const refresh = useCallback(async () => {
    try {
      const data = await listBackendDlq(PAGE_SIZE);
      setRecords(data.records);
      setLoadError(null);
    } catch (err) {
      setLoadError(errorText(err));
    }
  }, []);

  useEffect(() => {
    void refresh();
    return () => abortRef.current?.abort();
  }, [refresh]);

  const unresolved = useMemo(
    () => (records ?? []).filter((r) => r.resolution_status !== "recovered"),
    [records],
  );
  const recovered = useMemo(
    () => (records ?? []).filter((r) => r.resolution_status === "recovered"),
    [records],
  );
  const reasons = useMemo(() => {
    const counts = new Map<string, number>();
    for (const r of records ?? [])
      counts.set(r.classification ?? r.status, (counts.get(r.classification ?? r.status) ?? 0) + 1);
    return [...counts.entries()].sort((a, b) => b[1] - a[1]);
  }, [records]);

  // A batch is always explicit. With nothing ticked we offer the unresolved
  // records as the suggested set, but the operator still sees and confirms the
  // exact ids before anything is published.
  const targets = selected.length > 0 ? selected : unresolved.map((r) => r.dlq_id);

  const startRun = useCallback(
    async (ids: string[]) => {
      if (ids.length === 0) return;
      abortRef.current?.abort();
      const controller = new AbortController();
      abortRef.current = controller;

      setBusy(ids.length === 1 ? "single" : "batch");
      setActionError(null);
      setPreview(null);

      try {
        const started =
          ids.length === 1
            ? await reprocessDlqRecord(ids[0], reason.trim() || undefined)
            : await reprocessDlqBatch(ids, reason.trim() || undefined);
        setRun(started);
        const settled = await pollReprocessRun(started.reprocess_id, {
          signal: controller.signal,
          onUpdate: setRun,
        });
        setRun(settled);
        // Re-read the queue so resolution_status and attempt_history come from
        // the persisted record, not from a tally this page invented.
        await refresh();
      } catch (err) {
        if (!controller.signal.aborted) setActionError(errorText(err));
      } finally {
        if (!controller.signal.aborted) setBusy(false);
      }
    },
    [reason, refresh],
  );

  const runPreview = useCallback(async () => {
    if (targets.length === 0) return;
    setBusy("preview");
    setActionError(null);
    try {
      setPreview(await previewReprocessBatch(targets));
    } catch (err) {
      setActionError(errorText(err));
    } finally {
      setBusy(false);
    }
  }, [targets]);

  const toggle = useCallback((dlqId: string) => {
    setSelected((current) =>
      current.includes(dlqId)
        ? current.filter((id) => id !== dlqId)
        : [...current, dlqId],
    );
  }, []);

  if (loadError && !records) {
    return (
      <>
        <PageHeader
          eyebrow="Events · DLQ"
          title="DLQ (Failed Events)"
          subtitle="Quarantined events the full parser chain could not normalize."
        />
        <div className="flex items-start gap-3 rounded-lg border border-destructive/40 bg-destructive/5 px-5 py-4">
          <AlertTriangle className="mt-0.5 h-4 w-4 shrink-0 text-destructive" />
          <div>
            <p className="text-sm font-semibold">Could not load the DLQ</p>
            <p className="mt-1 text-sm text-muted-foreground">{loadError}</p>
            <Button variant="outline" size="sm" className="mt-3" onClick={() => void refresh()}>
              <RefreshCcw className="h-3.5 w-3.5" /> Retry
            </Button>
          </div>
        </div>
      </>
    );
  }

  const allTicked = records !== null && records.length > 0 && selected.length === records.length;

  return (
    <>
      <PageHeader
        eyebrow="Events · DLQ"
        title="DLQ (Failed Events)"
        subtitle="Quarantined events with the parser audit that rejected them. Reprocess republishes the original Bronze event into the live pipeline — the outcome shown here is the pipeline's own verdict, never a browser guess."
      >
        <Button variant="outline" size="sm" onClick={() => void refresh()} disabled={busy !== false}>
          <RefreshCcw className={`h-3.5 w-3.5 ${busy !== false ? "animate-spin" : ""}`} />
          Refresh
        </Button>
        <Button asChild variant="outline" size="sm">
          <Link to="/events/ingest">
            <PlayCircle className="h-3.5 w-3.5" /> Ingest more
          </Link>
        </Button>
      </PageHeader>

      {/* ---------------------------------------------------------- run state */}
      {run && (
        <div
          className={`mb-5 rounded-xl border px-4 py-3 ${
            isReprocessTerminal(run.status)
              ? run.recovered_count > 0
                ? "border-[#BDEDE3] bg-[#E9FFF9]"
                : "border-border bg-card/60"
              : "border-[#cfd6ff] bg-[#F3F5FF]"
          }`}
        >
          <div className="flex flex-wrap items-center gap-2">
            {isReprocessTerminal(run.status) ? (
              <CheckCircle2 className="h-4 w-4 text-[#0f766e]" />
            ) : (
              <RefreshCcw className="h-4 w-4 animate-spin text-[#4b5bd6]" />
            )}
            <span className="text-sm font-semibold">Reprocess run {run.reprocess_id.slice(0, 8)}</span>
            <Badge variant="outline" className="font-mono text-[10px]">
              {run.status}
            </Badge>
            {run.reason && (
              <span className="text-xs text-muted-foreground">reason: {run.reason}</span>
            )}
          </div>
          <div className="mt-2 flex flex-wrap gap-4 text-xs text-muted-foreground">
            <span>requested <b className="text-foreground">{run.requested_count}</b></span>
            <span>published <b className="text-foreground">{run.published_count}</b></span>
            <span>recovered <b className="text-[#0f766e]">{run.recovered_count}</b></span>
            <span>failed again <b className="text-rose-600">{run.failed_count}</b></span>
          </div>
          {!isReprocessTerminal(run.status) && (
            <p className="mt-2 text-xs text-muted-foreground">
              Waiting on the orchestrator to resolve {run.published_count} replayed event
              {run.published_count === 1 ? "" : "s"}. Nothing is reported as recovered until the
              pipeline says so.
            </p>
          )}
          {run.errors.length > 0 && (
            <ul className="mt-2 space-y-1 text-xs text-rose-600">
              {run.errors.map((e) => (
                <li key={`${e.dlq_id}:${e.detail}`} className="font-mono">
                  {e.dlq_id}: {e.detail}
                </li>
              ))}
            </ul>
          )}
        </div>
      )}

      {preview && (
        <div className="mb-5 rounded-xl border border-border bg-card/60 px-4 py-3">
          <div className="flex flex-wrap items-center gap-2">
            <Search className="h-4 w-4 text-muted-foreground" />
            <span className="text-sm font-semibold">Dry run — nothing was published</span>
          </div>
          <p className="mt-1 text-xs text-muted-foreground">
            <b className="text-foreground">{preview.would_succeed}</b> of {preview.requested} would
            normalize now.
            {preview.drain3_dependent > 0 && (
              <>
                {" "}
                <b className="text-foreground">{preview.drain3_dependent}</b> depend on the
                orchestrator&apos;s live Drain3 templates, so this preview cannot decide them — only a
                real replay can.
              </>
            )}
          </p>
        </div>
      )}

      {actionError && (
        <div className="mb-5 flex items-start gap-3 rounded-xl border border-destructive/40 bg-destructive/5 px-4 py-3">
          <AlertTriangle className="mt-0.5 h-4 w-4 shrink-0 text-destructive" />
          <p className="text-sm text-destructive">{actionError}</p>
        </div>
      )}

      {/* --------------------------------------------------------- stats */}
      <div className="mb-5 grid grid-cols-2 gap-2 sm:grid-cols-4">
        <StatChip icon={Ban} label="Unresolved" value={unresolved.length} color="#e11d48" tone="text-rose-600" />
        <StatChip icon={LifeBuoy} label="Recovered" value={recovered.length} color="#0f766e" tone={recovered.length ? "text-[#0f766e]" : "text-[#A6AABF]"} />
        <StatChip icon={Tags} label="Unique reasons" value={reasons.length} color="#72748A" tone="text-[#25263A]" />
        <StatChip
          icon={History}
          label="Replay attempts"
          value={(records ?? []).reduce((sum, r) => sum + (r.attempt_history?.length ?? 0), 0)}
          color="#7c4dcc"
          tone="text-[#25263A]"
        />
      </div>

      {reasons.length > 0 && (
        <div className="mb-5 flex flex-wrap items-center gap-1.5">
          {reasons.map(([reason, count]) => (
            <Badge
              key={reason}
              variant={isBenignReason(reason) ? "outline" : "destructive"}
              className="font-mono text-[11px]"
            >
              {reason} · {count}
            </Badge>
          ))}
        </div>
      )}

      {/* --------------------------------------------------------- actions */}
      <div className="mb-5 flex flex-col gap-3 rounded-lg border border-border bg-card/60 px-4 py-3">
        <div className="flex flex-wrap items-center gap-2">
          <label htmlFor="replay-reason" className="text-xs font-semibold text-muted-foreground">
            Replay reason
          </label>
          <input
            id="replay-reason"
            value={reason}
            maxLength={REASON_MAX}
            onChange={(e) => setReason(e.target.value)}
            placeholder="e.g. upgraded the syslog parser after the 14:02 incident"
            className="min-w-[16rem] flex-1 rounded-md border border-border bg-background px-3 py-1.5 text-sm outline-none focus:border-[#2f8ce0]"
          />
        </div>
        <div className="flex flex-wrap items-center gap-2">
          <Button size="sm" onClick={() => void startRun(targets)} disabled={busy !== false || targets.length === 0}>
            <LifeBuoy className={`h-3.5 w-3.5 ${busy === "batch" || busy === "single" ? "animate-pulse" : ""}`} />
            {busy === "single" || busy === "batch"
              ? "Reprocessing…"
              : `Reprocess ${targets.length} record${targets.length === 1 ? "" : "s"}`}
          </Button>
          <Button variant="outline" size="sm" onClick={() => void runPreview()} disabled={busy !== false || targets.length === 0}>
            <Search className="h-3.5 w-3.5" />
            {busy === "preview" ? "Evaluating…" : "Dry run"}
          </Button>
          <span className="text-xs text-muted-foreground">
            {selected.length > 0
              ? `${selected.length} selected`
              : `No selection — targeting all ${unresolved.length} unresolved record${unresolved.length === 1 ? "" : "s"}`}
          </span>
          {selected.length > 0 && (
            <Button variant="ghost" size="sm" onClick={() => setSelected([])}>
              Clear selection
            </Button>
          )}
        </div>
      </div>

      {/* --------------------------------------------------------- records */}
      <div className="overflow-hidden rounded-lg border border-border bg-card">
        {records === null && (
          <div className="px-4 py-8 text-sm text-muted-foreground">Loading the queue…</div>
        )}
        {records?.length === 0 && (
          <div className="flex items-center gap-2 px-4 py-8 text-sm text-muted-foreground">
            <LifeBuoy className="h-4 w-4 text-[#7c4dcc]" />
            Nothing is quarantined. Every event the pipeline has seen normalized cleanly.
          </div>
        )}
        {records && records.length > 0 && (
          <>
            <div className="flex items-center gap-2 border-b border-border bg-card/60 px-3 py-2">
              <input
                type="checkbox"
                aria-label="Select all records"
                checked={allTicked}
                onChange={(e) => setSelected(e.target.checked ? records.map((r) => r.dlq_id) : [])}
              />
              <span className="text-xs text-muted-foreground">
                Select all {records.length} record{records.length === 1 ? "" : "s"}
              </span>
            </div>
            {records.map((r) => {
              const history = r.attempt_history ?? [];
              const isRecovered = r.resolution_status === "recovered";
              const reason = r.classification ?? r.status;
              const rejectReason =
                typeof r.metadata?.reject_reason === "string" ? r.metadata.reject_reason : null;
              return (
                <div key={r.dlq_id} className="border-b border-border px-3 py-3 last:border-0">
                  <div className="flex items-start gap-3">
                    <input
                      type="checkbox"
                      className="mt-1 shrink-0"
                      aria-label={`Select ${r.dlq_id}`}
                      checked={selected.includes(r.dlq_id)}
                      onChange={() => toggle(r.dlq_id)}
                    />
                    <div className="min-w-0 flex-1">
                      <div className="mb-1 flex flex-wrap items-center gap-2">
                        <Badge
                          variant={isBenignReason(reason) ? "outline" : "destructive"}
                          className="px-1.5 py-0.5 text-[10px]"
                        >
                          {reason}
                        </Badge>
                        {isRecovered ? (
                          <Badge className="border-transparent bg-[#BDEDE3] px-1.5 py-0.5 text-[10px] font-bold text-[#0f766e]">
                            recovered
                          </Badge>
                        ) : (
                          <Badge variant="outline" className="px-1.5 py-0.5 text-[10px] text-muted-foreground">
                            unresolved
                          </Badge>
                        )}
                        {r.parsers_attempted?.map((p) => (
                          <span key={p} className="font-mono text-[10px] text-muted-foreground">
                            {p}
                          </span>
                        ))}
                      </div>
                      <p className="font-mono text-[10px] text-muted-foreground">
                        {r.dlq_id} · bronze {r.raw_event_id} · {r.reprocess_count} replay
                        {r.reprocess_count === 1 ? "" : "s"}
                        {r.last_reprocess_id ? ` · run ${r.last_reprocess_id.slice(0, 8)}` : ""}
                      </p>
                      {rejectReason && (
                        <p className="mt-1 text-[11px] text-muted-foreground">{rejectReason}</p>
                      )}
                      {r.replay_reason && (
                        <p className="mt-1 text-[11px] text-muted-foreground">
                          Reason: {r.replay_reason}
                        </p>
                      )}
                      {history.length > 0 && (
                        <ol className="mt-2 space-y-1 border-l border-border pl-3">
                          {history.map((h) => (
                            <li key={`${h.reprocess_id}-${h.attempt}`} className="text-[11px]">
                              <span
                                className={
                                  h.result === "recovered" ? "text-[#0f766e]" : "text-rose-600"
                                }
                              >
                                {h.result === "recovered" ? "recovered" : "failed again"}
                              </span>{" "}
                              <span className="text-muted-foreground">
                                · attempt {h.attempt} · {formatWhen(h.started_at)}
                                {h.reason ? ` · ${h.reason}` : ""}
                              </span>
                            </li>
                          ))}
                        </ol>
                      )}
                      {r.raw_payload ? (
                        <pre className="mt-2 max-h-40 overflow-auto whitespace-pre-wrap break-words rounded bg-muted/40 px-2 py-1.5 font-mono text-[11px] text-muted-foreground">
                          {r.raw_payload}
                        </pre>
                      ) : (
                        <p className="mt-2 text-[11px] italic text-muted-foreground/70">
                          Payload held in Bronze ({r.raw_event_id}) — replay always reads it from there.
                        </p>
                      )}
                    </div>
                    <Button
                      variant="outline"
                      size="sm"
                      className="shrink-0"
                      disabled={busy !== false}
                      onClick={() => void startRun([r.dlq_id])}
                    >
                      <RefreshCcw className="h-3.5 w-3.5" />
                      Reprocess
                    </Button>
                  </div>
                </div>
              );
            })}
          </>
        )}
      </div>
    </>
  );
}
