import { useMemo, useState } from "react";
import { Ban, Check, CheckCircle2, Download, FileText, Info, LifeBuoy, ScrollText, X } from "lucide-react";
import type { OcsfEvent } from "@shared/api";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { StatChip } from "@/components/common/bits";
import EventTable from "@/components/events/EventTable";
import JsonViewer from "@/components/events/JsonViewer";
import { isEvent, isRejected } from "@/lib/guards";
import { useNormalizer } from "@/lib/normalize-context";

export default function ResultsView() {
  const { result } = useNormalizer();
  const [selectedIndex, setSelectedIndex] = useState(0);

  const normalized = useMemo(() => (result ? result.lines.filter(isEvent) : []), [result]);
  const rejected = useMemo(() => (result ? result.lines.filter(isRejected) : []), [result]);

  const selected = selectedIndex < normalized.length ? normalized[selectedIndex] : undefined;
  const selectedEvent: OcsfEvent | null = selected?.event ?? null;

  const timeLabel = (ev: OcsfEvent) => {
    if (!ev.time || (ev.metadata.labels ?? []).includes("no_timestamp_in_source")) return "no timestamp in source";
    return new Date(ev.time).toISOString();
  };

  if (!result) return null;

  const downloadJson = () => {
    const payload = JSON.stringify(
      {
        version: "OCSF 1.3.0",
        summary: result.summary,
        events: normalized.map((l) => ({ line: l.line_number, event: l.event })),
        rejected: rejected.map((r) => ({ line: r.line_number, reason: r.reason, source: r.line })),
      },
      null,
      2,
    );
    const url = URL.createObjectURL(new Blob([payload], { type: "application/json" }));
    const a = document.createElement("a");
    a.href = url;
    a.download = `ocsf-normalized-${new Date().toISOString().slice(0, 19).replace(/[:T]/g, "-")}.json`;
    a.click();
    URL.revokeObjectURL(url);
  };

  return (
    <>
      <div className="mt-8 flex items-center justify-between gap-3">
        <h2 className="flex items-center gap-2 text-base font-bold tracking-tight">
          <ScrollText className="h-4 w-4 text-[#2f8ce0]" />
          Summary
        </h2>
        <Button variant="outline" size="sm" onClick={downloadJson}>
          <Download className="h-3.5 w-3.5" />
          Download results (.json)
        </Button>
      </div>

      <div className="mt-3 grid grid-cols-2 gap-2 sm:grid-cols-4 lg:grid-cols-6">
        <StatChip icon={FileText} label="Lines" value={result.summary.total_lines} color="#72748A" tone="text-[#25263A]" />
        <StatChip icon={CheckCircle2} label="Events" value={result.summary.events} color="#2f8ce0" tone="text-emerald-600" />
        <StatChip icon={Ban} label="Rejected" value={result.summary.rejected} color="#e11d48" tone="text-rose-600" />
        <StatChip icon={Info} label="Skipped" value={result.summary.skipped} color="#d97706" tone="text-amber-600" />
        <StatChip icon={LifeBuoy} label="Rescued" value={result.summary.rescued ?? 0} color="#7c4dcc" tone={result.summary.rescued ? "text-[#25263A]" : "text-[#A6AABF]"} />
        <StatChip icon={Check} label="Formats" value={Object.keys(result.summary.by_format).length} color="#0f766e" tone="text-[#25263A]" />
      </div>

      <div className="mt-3 flex flex-wrap items-center gap-1.5">
        {result.summary.source_format && (
          <Badge variant="outline" className="font-mono text-[11px]">
            source: {result.summary.source_format}
          </Badge>
        )}
        {Object.entries(result.summary.by_class)
          .sort((a, b) => b[1] - a[1])
          .map(([cls, count]) => (
            <Badge key={cls} variant="secondary" className="font-mono text-[11px]">
              {cls} · {count}
            </Badge>
          ))}
      </div>

      <div className="mt-8 grid gap-5 lg:grid-cols-[minmax(0,1.15fr)_minmax(0,0.85fr)]">
        <section>
          <h2 className="mb-2 flex items-center gap-2 text-base font-bold tracking-tight">
            <CheckCircle2 className="h-4 w-4 text-emerald-500" />
            Normalized events
            <span className="text-sm font-normal text-muted-foreground">({normalized.length})</span>
          </h2>
          <EventTable events={normalized} selectedIndex={selectedIndex} onSelect={setSelectedIndex} emptyMessage="No events were produced — check the rejected lines below." />
        </section>
        <section>
          <JsonViewer event={selectedEvent} lineNumber={selected?.line_number} footnote={selectedEvent ? timeLabel(selectedEvent) : undefined} />
        </section>
      </div>

      <div className="mt-8">
        <h2 className="mb-2 flex items-center gap-2 text-base font-bold tracking-tight">
          <X className="h-4 w-4 text-rose-500" />
          Rejected lines
          <span className="text-sm font-normal text-muted-foreground">({rejected.length})</span>
        </h2>
        <div className="max-h-64 overflow-y-auto rounded-lg border border-border bg-card">
          {rejected.length === 0 && (
            <div className="flex items-center gap-2 px-4 py-3 text-sm text-muted-foreground">
              <CheckCircle2 className="h-4 w-4 text-emerald-500" />
              Everything parsed cleanly — nothing rejected.
            </div>
          )}
          {rejected.map((r) => (
            <div key={r.line_number} className="flex gap-3 border-b border-border px-3 py-2.5 last:border-0">
              <span className="w-10 shrink-0 font-mono text-[10px] text-muted-foreground">#{r.line_number}</span>
              <div className="min-w-0 flex-1">
                <div className="flex flex-wrap items-center gap-2">
                  <Badge variant="destructive" className="px-1.5 py-0.5 text-[10px]">
                    {r.reason}
                  </Badge>
                </div>
                {r.line && <p className="mt-1 line-clamp-1 font-mono text-[11px] text-muted-foreground">{r.line}</p>}
              </div>
            </div>
          ))}
        </div>
      </div>
    </>
  );
}