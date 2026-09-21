import { useEffect, useMemo, useState } from "react";
import { Link } from "react-router-dom";
import { ArrowRight, Ban, LifeBuoy, PlayCircle, RefreshCcw, Tags } from "lucide-react";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { PageHeader, StatChip } from "@/components/common/bits";
import { isEvent, isRejected } from "@/lib/guards";
import { useNormalizer } from "@/lib/normalize-context";
import type { RejectedEventResult } from "@shared/api";
import { backendDlqToRejected, listBackendDlq } from "@/lib/backend";

export default function Dlq() {
  const { result, loading, runSample, reRun } = useNormalizer();
  const [recovered, setRecovered] = useState<number[]>([]);
  const [state, setState] = useState<"idle" | "rerunned">("idle");
  const [live, setLive] = useState<RejectedEventResult[] | null>(null);

  // With no local ingestion yet, show the quarantine the pipeline recorded.
  useEffect(() => {
    let cancelled = false;
    if (result) {
      setLive(null);
      return;
    }
    listBackendDlq(100)
      .then((d) => {
        if (cancelled) return;
        setLive(d.records.map((r, i) => backendDlqToRejected(r, i)));
      })
      .catch(() => {
        if (!cancelled) setLive(null);
      });
    return () => {
      cancelled = true;
    };
  }, [result]);

  const rejected = useMemo(() => (result ? result.lines.filter(isRejected) : live ?? []), [result, live]);
  const reasons = useMemo(() => {
    const m = new Map<string, number>();
    rejected.forEach((r) => m.set(r.reason, (m.get(r.reason) ?? 0) + 1));
    return [...m.entries()].sort((a, b) => b[1] - a[1]);
  }, [rejected]);

  const reParseBatch = async () => {
    const before = rejected.map((r) => r.line_number);
    const next = await reRun();
    if (next) {
      const nowOk = new Set(next.lines.filter(isEvent).map((l) => l.line_number));
      setRecovered(before.filter((n) => nowOk.has(n)));
      setState("rerunned");
    }
  };

  const freshRun = async () => {
    setRecovered([]);
    setState("idle");
    await runSample();
  };

  if (!result && !live) {
    return (
      <>
        <PageHeader eyebrow="Events · DLQ" title="DLQ (Failed Events)" subtitle="Lines that failed the full parser chain are quarantined here with a reason, a parsers-tried audit, and a one-click re-parse loop.">
          <Button size="sm" onClick={freshRun} disabled={loading}>
            <PlayCircle className="h-3.5 w-3.5" />
            {loading ? "Running…" : "Run sample bundle"}
          </Button>
        </PageHeader>
        <div className="flex flex-col items-start gap-3 rounded-lg border border-dashed border-border bg-card/50 px-5 py-10">
          <p className="text-sm text-muted-foreground">The dead-letter queue is empty because no ingestion has run yet. The sample bundle intentionally includes noise so you can see rejection reasons and the parser audit trail.</p>
          <Button asChild size="sm">
            <Link to="/events/ingest">
              Go to ingest <ArrowRight className="h-3.5 w-3.5" />
            </Link>
          </Button>
        </div>
      </>
    );
  }

  return (
    <>
      <PageHeader
        eyebrow="Events · DLQ"
        title="DLQ (Failed Events)"
        subtitle="Quarantined lines that were rejected by the whole parser chain. The audit trail shows every parser attempted, and re-parse separates “our parser was too limited” from “this log is genuinely broken”."
      >
        <Button variant="outline" size="sm" onClick={freshRun} disabled={loading}>
          <PlayCircle className="h-3.5 w-3.5" />
          Re-run sample
        </Button>
        <Button size="sm" onClick={reParseBatch} disabled={loading || rejected.length === 0 || !result} className="bg-gradient-to-r from-[#2f8ce0] to-[#7c4dcc] text-white shadow-lg shadow-[#2f8ce0]/25">
          <RefreshCcw className={`h-3.5 w-3.5 ${loading ? "animate-spin" : ""}`} />
          Re-parse batch
        </Button>
      </PageHeader>

      {state === "rerunned" && (
        <div className={`mb-5 flex flex-wrap items-center gap-2 rounded-xl border px-4 py-3 ${recovered.length > 0 ? "border-[#BDEDE3] bg-[#E9FFF9]" : "border-border bg-card/60"}`}>
          {recovered.length > 0 ? (
            <>
              <LifeBuoy className="h-4 w-4 text-[#0f766e]" />
              <p className="text-sm text-[#0f766e]">
                <span className="font-bold">{recovered.length}</span> previously failed line{recovered.length > 1 ? "s" : ""} now normalizes with the current parser set — that was a <span className="font-semibold">parser limitation</span>, not a bad log.
              </p>
            </>
          ) : (
            <p className="text-sm text-muted-foreground">Re-parse done — every previously failed line still fails under the current parser set. These belong in the DLQ for manual review.</p>
          )}
        </div>
      )}

      <div className="mb-5 grid grid-cols-2 gap-2 sm:grid-cols-4">
        <StatChip icon={Ban} label="Failed" value={rejected.length} color="#e11d48" tone="text-rose-600" />
        <StatChip icon={Tags} label="Unique reasons" value={reasons.length} color="#72748A" tone="text-[#25263A]" />
        <StatChip icon={LifeBuoy} label="Recovered" value={recovered.length} color="#7c4dcc" tone={recovered.length ? "text-[#25263A]" : "text-[#A6AABF]"} />
        <StatChip icon={RefreshCcw} label="Parsers tried / line" value={rejected[0]?.tried_parsers?.length ?? 0} color="#0f766e" tone="text-[#25263A]" />
      </div>

      {reasons.length > 0 && (
        <div className="mb-5 flex flex-wrap items-center gap-1.5">
          {reasons.map(([reason, count]) => (
            <Badge key={reason} variant="destructive" className="font-mono text-[11px]">
              {reason} · {count}
            </Badge>
          ))}
        </div>
      )}

      <div className="overflow-hidden rounded-lg border border-border bg-card">
        {rejected.length === 0 && (
          <div className="flex items-center gap-2 px-4 py-8 text-sm text-muted-foreground">
            <LifeBuoy className="h-4 w-4 text-[#7c4dcc]" />
            Nothing was rejected in the last ingestion — the queue is empty.
          </div>
        )}
        {rejected.map((r) => (
          <div key={r.line_number} className="flex gap-3 border-b border-border px-3 py-2.5 last:border-0">
            <span className="w-10 shrink-0 font-mono text-[10px] text-muted-foreground">#{r.line_number}</span>
            <div className="min-w-0 flex-1">
              <div className="mb-1 flex flex-wrap items-center gap-2">
                <Badge variant="destructive" className="px-1.5 py-0.5 text-[10px]">
                  {r.reason}
                </Badge>
                {r.tried_parsers && r.tried_parsers.length > 0 && (
                  <>
                    <span className="font-mono text-[9px] uppercase tracking-wider text-muted-foreground">tried</span>
                    <Badge variant="outline" className="px-1.5 py-0.5 font-mono text-[10px] text-muted-foreground" title="Every parser in the fallback chain was attempted before this line was quarantined.">
                      {r.tried_parsers.join(" → ")}
                    </Badge>
                  </>
                )}
                {recovered.includes(r.line_number) && (
                  <Badge className="border-transparent bg-[#BDEDE3] px-1.5 py-0.5 text-[10px] font-bold text-[#0f766e]">
                    recovered on re-parse
                  </Badge>
                )}
                <span className="font-mono text-[10px] text-muted-foreground">payload dropped</span>
              </div>
              {r.line ? (
                <pre className="overflow-x-auto whitespace-pre-wrap break-words font-mono text-[11px] leading-relaxed text-muted-foreground">{r.line}</pre>
              ) : (
                <p className="font-mono text-[11px] italic text-muted-foreground/70">empty line</p>
              )}
            </div>
          </div>
        ))}
      </div>
    </>
  );
}