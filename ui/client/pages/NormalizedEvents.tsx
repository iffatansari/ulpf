import { useEffect, useMemo, useState } from "react";
import { Link } from "react-router-dom";
import { ArrowRight, Ban, CheckCircle2, FileCode2, Layers, PlayCircle } from "lucide-react";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { PageHeader, StatChip } from "@/components/common/bits";
import EventTable from "@/components/events/EventTable";
import JsonViewer from "@/components/events/JsonViewer";
import { ACCENT } from "@/lib/accents";
import { isEvent } from "@/lib/guards";
import { useNormalizer } from "@/lib/normalize-context";
import type { NormalizedEventResult } from "@shared/api";
import { backendEventToLineResult, listBackendEvents } from "@/lib/backend";

export default function NormalizedEvents() {
  const { result, loading, runSample } = useNormalizer();
  const [selectedIndex, setSelectedIndex] = useState(0);
  const [live, setLive] = useState<{ total: number; events: NormalizedEventResult[] } | null>(null);

  // With no local ingestion yet, surface the live pipeline feed from OpenSearch.
  useEffect(() => {
    let cancelled = false;
    if (result) {
      setLive(null);
      return;
    }
    listBackendEvents(50)
      .then((d) => {
        if (cancelled) return;
        setLive({ total: d.total, events: d.events.map(backendEventToLineResult) });
      })
      .catch(() => {
        if (!cancelled) setLive(null);
      });
    return () => {
      cancelled = true;
    };
  }, [result]);

  const normalized = useMemo(() => (result ? result.lines.filter(isEvent) : live ? live.events : []), [result, live]);
  const selected = selectedIndex < normalized.length ? normalized[selectedIndex] : undefined;

  const totalEvents = result ? result.summary.events : live?.total ?? 0;
  const classCount = result
    ? Object.keys(result.summary.by_class).length
    : new Set((live?.events ?? []).map((l) => l.event.class_name ?? "Base Event")).size;
  const formatCount = result
    ? Object.keys(result.summary.by_format).length
    : new Set((live?.events ?? []).map((l) => l.parser)).size;
  const rejectedCount = result ? result.summary.rejected : 0;

  const timeLabel = (ev: { time: number; metadata?: { labels?: string[] } }) => {
    if (!ev.time || (ev.metadata?.labels ?? []).includes("no_timestamp_in_source")) return "no timestamp in source";
    return new Date(ev.time).toISOString();
  };

  if (!result && !live) {
    return (
      <>
        <PageHeader eyebrow="Events · Normalized" title="Normalized events" subtitle="Inspect the OCSF 1.3.0 events produced by the last ingestion.">
          <Button size="sm" onClick={() => runSample()} disabled={loading}>
            <PlayCircle className="h-3.5 w-3.5" />
            {loading ? "Running…" : "Run sample bundle"}
          </Button>
        </PageHeader>
        <div className="flex flex-col items-start gap-3 rounded-lg border border-dashed border-border bg-card/50 px-5 py-10">
          <p className="text-sm text-muted-foreground">No ingestion yet. Run a sample or normalize logs on the Ingest Events page first.</p>
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
        eyebrow="Events · Normalized"
        title="Normalized events"
        subtitle={`${normalized.length} schema-valid OCSF events from the last ingestion. Click a row to inspect its full payload.`}
      >
        <div className="flex items-center gap-2">
          <Badge variant="outline" className="font-mono text-[11px]">
            source: {result ? result.summary.source_format : "backend · live pipeline"}
          </Badge>
          <Button variant="outline" size="sm" onClick={() => runSample()} disabled={loading}>
            <PlayCircle className="h-3.5 w-3.5" />
            Re-run sample
          </Button>
        </div>
      </PageHeader>

      <div className="mb-5 grid grid-cols-2 gap-2 sm:grid-cols-4">
        <StatChip icon={CheckCircle2} label="Events" value={totalEvents} color="#2f8ce0" tone="text-emerald-600" />
        <StatChip icon={Layers} label="Classes" value={classCount} color="#7c4dcc" tone="text-[#25263A]" />
        <StatChip icon={FileCode2} label="Formats" value={formatCount} color={ACCENT.mint} tone="text-[#25263A]" />
        <StatChip icon={Ban} label="Rejected" value={rejectedCount} color="#e11d48" tone="text-rose-600" />
      </div>

      <div className="grid gap-5 lg:grid-cols-[minmax(0,1.15fr)_minmax(0,0.85fr)]">
        <section>
          <EventTable events={normalized} selectedIndex={selectedIndex} onSelect={setSelectedIndex} showFilter />
        </section>
        <section>
          <JsonViewer event={selected?.event ?? null} lineNumber={selected?.line_number} footnote={selected ? timeLabel(selected.event) : undefined} />
        </section>
      </div>
    </>
  );
}