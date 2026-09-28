import { useMemo } from "react";
import { Link } from "react-router-dom";
import { ArrowRight, Ban, Check, CheckCircle2, Download, FileText, Info, LifeBuoy, ScrollText } from "lucide-react";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { StatChip } from "@/components/common/bits";
import EventTable from "@/components/events/EventTable";
import RejectedLines from "@/components/events/RejectedLines";
import { isEvent, isRejected } from "@/lib/guards";
import { useNormalizer } from "@/lib/normalize-context";

export default function ResultsView() {
  const { result } = useNormalizer();

  const normalized = useMemo(() => (result ? result.lines.filter(isEvent) : []), [result]);
  const rejected = useMemo(() => (result ? result.lines.filter(isRejected) : []), [result]);

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

      <div className="mt-8 flex flex-wrap items-center justify-between gap-3">
        <h2 className="flex items-center gap-2 text-base font-bold tracking-tight">
          <CheckCircle2 className="h-4 w-4 text-emerald-500" />
          Normalized logs
          <span className="text-sm font-normal text-muted-foreground">({normalized.length})</span>
        </h2>
        <Button asChild variant="outline" size="sm">
          <Link to="/events/normalized">
            Open events <ArrowRight className="h-3.5 w-3.5" />
          </Link>
        </Button>
      </div>

      <div className="mt-3">
        <EventTable
          events={normalized}
          heightClass="max-h-[70vh]"
          showFilter
          emptyMessage="No events were produced — check the rejected lines below."
        />
      </div>

      <div className="mt-8">
        <RejectedLines rejected={rejected} />
      </div>
    </>
  );
}