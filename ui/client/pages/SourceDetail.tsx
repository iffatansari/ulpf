import { useEffect, useMemo, useState } from "react";
import { Link, useParams } from "react-router-dom";
import { ArrowLeft, ArrowRight, Ban, CheckCircle2, HardDriveDownload, PlayCircle, Radar, Sparkles, Trash2 } from "lucide-react";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent } from "@/components/ui/card";
import { PageHeader, StatChip } from "@/components/common/bits";
import EventTable from "@/components/events/EventTable";
import { ACCENT } from "@/lib/accents";
import { collectorSnippet, TRANSPORTS, useSources } from "@/lib/source-context";
import { useNormalizer } from "@/lib/normalize-context";
import { MIXED_SAMPLE } from "@/lib/samples";
import { isEvent, isRejected } from "@/lib/guards";
import type { NormalizedEventResult, RejectedEventResult } from "@shared/api";
import type { SourceStats } from "@/lib/backend";
import { backendDlqToRejected, backendEventToLineResult, getBackendSourceEvents, getBackendSourceStats, listBackendDlq } from "@/lib/backend";

export default function SourceDetail() {
  const { id } = useParams<{ id: string }>();
  const { removeSource, reportRun, sourceType, sourceById } = useSources();
  const { result, loading, lastInput, run } = useNormalizer();
  const [selectedIndex, setSelectedIndex] = useState(0);
  const [stats, setStats] = useState<SourceStats | null>(null);
  const [liveEvents, setLiveEvents] = useState<NormalizedEventResult[]>([]);
  const [liveDlq, setLiveDlq] = useState<RejectedEventResult[]>([]);

  const source = id ? sourceById(id) : undefined;
  const type = source ? sourceType(source) : undefined;

  // Live pipeline data: real OpenSearch counts / events / DLQ for this source.
  // Falls back silently to local lastRun when the backend is unreachable.
  useEffect(() => {
    let cancelled = false;
    if (!source) return;

    const load = async () => {
      try {
        const [s, e, dq] = await Promise.all([
          getBackendSourceStats(source.id),
          getBackendSourceEvents(source.id, 50),
          listBackendDlq(200),
        ]);
        if (cancelled) return;
        setStats(s);
        setLiveEvents(e.events.map(backendEventToLineResult));
        setLiveDlq(
          dq.records
            .filter((r) => r.metadata?.source_id === source.id)
            .map((r, i) => backendDlqToRejected(r, i)),
        );
      } catch {
        if (cancelled) return;
        setStats(null);
        setLiveEvents([]);
        setLiveDlq([]);
      }
    };

    load();
    const tick = setInterval(load, 10000);
    return () => {
      cancelled = true;
      clearInterval(tick);
    };
  }, [source]);

  const attributed = !!(result && lastInput?.sourceName === source?.name);

  const taggedEvents = useMemo(
    () => (result && source ? result.lines.filter(isEvent).filter((l) => l.event.metadata.log?.name === source.name) : []),
    [result, source],
  );
  const rejected = useMemo(() => (result ? result.lines.filter(isRejected) : []), [result]);

  const displayEvents = attributed && taggedEvents.length > 0 ? taggedEvents : liveEvents;
  const displayDlq = attributed && rejected.length > 0 ? rejected : liveDlq;

  const accepted = stats?.normalized_events ?? source?.lastRun?.accepted ?? 0;
  const dlqCount = stats?.dlq_events ?? source?.lastRun?.rejected ?? 0;
  const linesCount = stats?.raw_events ?? source?.lastRun?.total ?? 0;

  if (!source || !type) {
    return (
      <div className="flex flex-col items-start gap-3 rounded-xl border border-dashed border-border bg-card/50 px-6 py-10">
        <p className="text-sm text-muted-foreground">That source no longer exists.</p>
        <Button asChild size="sm">
          <Link to="/sources">
            Back to sources <ArrowLeft className="h-3.5 w-3.5" />
          </Link>
        </Button>
      </div>
    );
  }

  const transport = TRANSPORTS.find((t) => t.id === source.transport);
  const Icon = type.icon;
  const color = type.color;

  const runSourceSample = async () => {
    const data = await run({ content: type.sample, sourceName: source.name, format: "auto" });
    if (data) reportRun(source.id, { total: data.summary.total_lines, accepted: data.summary.events, rejected: data.summary.rejected, rescued: data.summary.rescued });
    setSelectedIndex(0);
  };

  const runSourceBundle = async () => {
    const data = await run({ content: MIXED_SAMPLE, sourceName: source.name, format: "auto" });
    if (data) reportRun(source.id, { total: data.summary.total_lines, accepted: data.summary.events, rejected: data.summary.rejected, rescued: data.summary.rescued });
    setSelectedIndex(0);
  };

  return (
    <>
      <PageHeader
        eyebrow="Sources · detail view"
        title={source.name}
        subtitle={`${type.label} collector on ${transport?.label ?? source.transport} (${source.endpoint}). Tagged events appear here, its parser plan below, and DLQ per source rolls up globally.`}
      >
        <Button size="sm" variant="outline" onClick={runSourceSample} disabled={loading}>
          <Sparkles className="h-3.5 w-3.5" />
          {loading ? "Testing…" : "Test with sample"}
        </Button>
        <Button size="sm" onClick={runSourceBundle} disabled={loading} className="bg-gradient-to-r from-[#2f8ce0] to-[#7c4dcc] text-white shadow-lg shadow-[#2f8ce0]/25">
          <PlayCircle className="h-3.5 w-3.5" />
          {loading ? "Running…" : "Test with sample bundle"}
        </Button>
      </PageHeader>

      <div className="mb-5 flex flex-wrap items-center gap-1.5 font-mono text-[11px]">
        <Link to="/sources" className="flex items-center gap-1 text-muted-foreground hover:text-[#2f8ce0]">
          <ArrowLeft className="h-3 w-3" />
          Sources
        </Link>
      </div>

      <div className="grid gap-3 lg:grid-cols-[minmax(0,1fr)_minmax(0,1fr)]">
        <Card className="gap-2 overflow-hidden bg-card/60">
          <CardContent className="p-5">
            <div className="flex items-start justify-between gap-3">
              <div className="flex items-center gap-2.5">
                <span className="flex h-11 w-11 items-center justify-center rounded-xl text-white shadow-lg" style={{ backgroundColor: color, boxShadow: `0 12px 24px -12px ${color}` }}>
                  <Icon className="h-5 w-5" />
                </span>
                <div>
                  <p className="text-sm font-bold">{type.label}</p>
                  <p className="font-mono text-[11px] text-muted-foreground">created {new Date(source.createdAt).toLocaleString()}</p>
                </div>
              </div>
              <span className="inline-flex items-center gap-1 rounded-full bg-[#BDEDE3] px-2 py-0.5 text-[10px] font-bold text-[#0f766e]">
                <span className="h-1.5 w-1.5 rounded-full bg-[#0f766e]" />
                Active
              </span>
            </div>

            <div className="mt-4 grid gap-2 sm:grid-cols-3">
              <Field label="Transport" value={`${transport?.label}`} />
              <Field label="Endpoint" value={source.endpoint} mono />
              <Field label="Format" value={source.format} mono />
            </div>

            <div className="mt-4 border-t border-border pt-4">
              <p className="mb-2 font-mono text-[10px] uppercase tracking-wider text-muted-foreground">Parser plan · fallback chain</p>
              <div className="flex flex-wrap items-center gap-1.5">
                {source.parser_chain.length > 0 ? (
                  source.parser_chain.map((p, i) => (
                    <span key={p} className="flex items-center gap-1.5">
                      <Badge className={cnChain(i)}>{i === 0 && "primary · "}{p}</Badge>
                      {i < source.parser_chain.length - 1 && <ArrowRight className="h-3 w-3 text-muted-foreground" />}
                    </span>
                  ))
                ) : (
                  <span className="text-sm text-muted-foreground">Run a test to lock the chain for this source.</span>
                )}
              </div>
            </div>
          </CardContent>
        </Card>

        <Card className="gap-2 overflow-hidden bg-card/60">
          <CardContent className="p-5">
            <p className="mb-2 font-mono text-[10px] uppercase tracking-wider text-muted-foreground">Collector deployment · one ingest contract</p>
            <pre className="overflow-x-auto rounded-lg bg-[#25263A] p-3 font-mono text-[11px] leading-relaxed text-[#E8F4FF]">{collectorSnippet(source)}</pre>
            <p className="mt-2 text-[11px] text-muted-foreground">
              Every event carrying <span className="font-mono">source.name = “{source.name}”</span> is attributed to this source dashboard, metrics and DLQ.
            </p>
          </CardContent>
        </Card>
      </div>

      <div className="mt-5 grid grid-cols-2 gap-2 sm:grid-cols-4">
        <StatChip icon={CheckCircle2} label="Accepted" value={accepted} color="#2f8ce0" tone={accepted ? "text-emerald-600" : "text-[#A6AABF]"} />
        <StatChip icon={Radar} label="Rescued" value={source.lastRun?.rescued ?? 0} color="#7c4dcc" tone={source.lastRun?.rescued ? "text-[#25263A]" : "text-[#A6AABF]"} />
        <StatChip icon={Ban} label="DLQ" value={dlqCount} color="#e11d48" tone={dlqCount ? "text-rose-600" : "text-[#A6AABF]"} />
        <StatChip icon={HardDriveDownload} label="Lines" value={linesCount} color={ACCENT.gray} tone="text-[#25263A]" />
      </div>

      {displayEvents.length > 0 && (
        <div className="mt-6 min-w-0">
          <h2 className="mb-2 flex items-center gap-2 text-base font-bold tracking-tight">
            <CheckCircle2 className="h-4 w-4 text-emerald-500" />
            Live event stream · {source.name}
            <span className="text-sm font-normal text-muted-foreground">({displayEvents.length})</span>
          </h2>
          <EventTable events={displayEvents} selectedIndex={selectedIndex} onSelect={setSelectedIndex} heightClass="max-h-[380px]" emptyMessage="No events attributed to this source yet." showFilter />
          {attributed && source.lastRun && <p className="mt-2 font-mono text-[10px] text-muted-foreground">last run · {new Date(source.lastRun.at).toLocaleString()}</p>}
          {!attributed && stats?.last_ingested_at && <p className="mt-2 font-mono text-[10px] text-muted-foreground">last ingested · {new Date(stats.last_ingested_at).toLocaleString()} · live from OpenSearch</p>}
        </div>
      )}

      {displayDlq.length > 0 && (
        <div className="mt-6">
          <h2 className="mb-2 flex items-center gap-2 text-base font-bold tracking-tight">
            <Ban className="h-4 w-4 text-rose-500" />
            Ingest DLQ · {source.name}
            <span className="text-sm font-normal text-muted-foreground">({displayDlq.length}) — parsers tried are recorded per line</span>
          </h2>
          <div className="max-h-64 overflow-y-auto rounded-lg border border-border bg-card">
            {displayDlq.map((r) => (
              <div key={`${r.line_number}-${r.reason}`} className="flex gap-3 border-b border-border px-3 py-2.5 last:border-0">
                <span className="w-10 shrink-0 font-mono text-[10px] text-muted-foreground">#{r.line_number}</span>
                <div className="min-w-0 flex-1">
                  <div className="flex flex-wrap items-center gap-2">
                    <Badge variant="destructive" className="px-1.5 py-0.5 text-[10px]">{r.reason}</Badge>
                  </div>
                  {r.line ? <p className="mt-1 line-clamp-1 font-mono text-[11px] text-muted-foreground">{r.line}</p> : <p className="mt-1 font-mono text-[11px] italic text-muted-foreground/70">empty line</p>}
                </div>
              </div>
            ))}
          </div>
        </div>
      )}

      {!attributed && liveEvents.length === 0 && liveDlq.length === 0 && (
        <div className="mt-6 flex flex-col items-start gap-2 rounded-xl border border-dashed border-border bg-card/50 px-5 py-6">
          <p className="text-sm text-muted-foreground">Run “Test with sample” or the sample bundle to populate this source’s event stream, parse health and DLQ. The last global run isn’t attributed to this source.</p>
          <div className="flex gap-2">
            <Button size="sm" onClick={runSourceSample} disabled={loading}>
              <Sparkles className="h-3.5 w-3.5" />
              {loading ? "Testing…" : "Test with sample"}
            </Button>
            <Button size="sm" variant="outline" onClick={runSourceBundle} disabled={loading}>
              <PlayCircle className="h-3.5 w-3.5" />
              Sample bundle
            </Button>
          </div>
        </div>
      )}

      <div className="mt-8 flex justify-end border-t border-border pt-4">
        <Button variant="ghost" size="sm" className="gap-1 text-xs text-muted-foreground hover:text-rose-600" onClick={() => removeSource(source.id)}>
          <Trash2 className="h-3 w-3" />
          Remove source
        </Button>
      </div>
    </>
  );
}

function cnChain(i: number): string {
  return i === 0 ? "border-transparent bg-[#E8F4FF] px-2 py-1 font-mono text-[11px] font-semibold text-[#2f8ce0]" : "border-transparent bg-[#F0ECFF] px-2 py-1 font-mono text-[11px] font-semibold text-[#7c4dcc]";
}

function Field({ label, value, mono }: { label: string; value: string; mono?: boolean }) {
  return (
    <div className="rounded-lg bg-muted/40 px-3 py-2">
      <p className="font-mono text-[9px] uppercase tracking-wider text-muted-foreground">{label}</p>
      <p className={`truncate text-xs font-semibold text-[#25263A] ${mono ? "font-mono" : ""}`}>{value}</p>
    </div>
  );
}